"""Adversarial Phase 14 deterministic single-series research tests."""

import inspect
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID

import pytest
from pydantic import ValidationError

import app.research.engine as engine_module
import app.research.models as models_module
from app.core.schemas import AssetClass, Instrument, MarketBar, Timeframe
from app.data.observations import SourceIdentity
from app.evidence import (
    AlignedTechnicalHistory,
    DeterministicMarketEvidenceEngine,
    MarketEvidenceConfig,
    MarketEvidenceEngine,
)
from app.materialization import BarSeriesRequest, MaterializedBar, MaterializedBarHistory
from app.research import (
    DeterministicSingleSeriesResearchEngine,
    SingleSeriesResearchComputationError,
    SingleSeriesResearchInvalidInputError,
    SingleSeriesResearchRequest,
    SingleSeriesResearchResult,
)
from app.setups import (
    AlignedEvidenceHistory,
    DeterministicSetupCompositionEngine,
    SetupCompositionEngine,
)
from app.technical import (
    DeterministicTechnicalAnalysisEngine,
    TechnicalAnalysisConfig,
    TechnicalAnalysisEngine,
    TechnicalAnalysisInvalidInputError,
)

_START = datetime(2025, 1, 1, tzinfo=UTC)
_AS_OF = datetime(2025, 2, 1, tzinfo=UTC)
_INSTRUMENT = Instrument(
    symbol="AAPL", asset_class=AssetClass.EQUITY, exchange="XNAS", currency="USD"
)
_SOURCE = SourceIdentity(name="provider-a")


def _history(count: int = 25) -> MaterializedBarHistory:
    request = BarSeriesRequest(
        instrument=_INSTRUMENT,
        timeframe=Timeframe.ONE_DAY,
        source=_SOURCE,
        as_of=_AS_OF,
    )
    items = tuple(
        MaterializedBar(
            bar=MarketBar(
                instrument=_INSTRUMENT,
                timeframe=Timeframe.ONE_DAY,
                timestamp=_START + timedelta(days=index),
                open=Decimal(100 + index),
                high=Decimal(102 + index),
                low=Decimal(99 + index),
                close=Decimal(101 + index),
                volume=Decimal(1_000 + index * 10),
            ),
            observation_id=UUID(f"00000000-0000-4000-8000-{index + 1:012d}"),
            observed_at=_START + timedelta(days=index, hours=1),
            source=_SOURCE,
            source_record_id=f"bar-{index}",
        )
        for index in range(count)
    )
    return MaterializedBarHistory(
        request=request,
        bars=items,
        inspected_observation_count=count,
        eligible_bar_candidate_count=count,
    )


def _request(count: int = 25) -> SingleSeriesResearchRequest:
    return SingleSeriesResearchRequest(
        history=_history(count),
        technical_config=TechnicalAnalysisConfig(),
        evidence_config=MarketEvidenceConfig(),
    )


def test_complete_pipeline_is_aligned_and_preserves_phase13_provenance() -> None:
    request = _request()
    result = DeterministicSingleSeriesResearchEngine().analyze(request)
    assert result.request == request
    assert result.request.history == request.history
    assert len(result.technical_snapshots) == 25
    assert len(result.evidence_snapshots) == 25
    assert len(result.setup_snapshots) == 25
    expected_times = tuple(item.bar.timestamp for item in request.history.bars)
    assert tuple(item.timestamp for item in result.technical_snapshots) == expected_times
    assert tuple(item.timestamp for item in result.evidence_snapshots) == expected_times
    assert tuple(item.timestamp for item in result.setup_snapshots) == expected_times
    assert result.request.history.bars[-1].source_record_id == "bar-24"


def test_empty_materialized_history_remains_explicit_through_every_stage() -> None:
    result = DeterministicSingleSeriesResearchEngine().analyze(_request(0))
    assert result.request.history.bars == ()
    assert result.technical_snapshots == ()
    assert result.evidence_snapshots == ()
    assert result.setup_snapshots == ()


def test_identical_request_produces_identical_immutable_result() -> None:
    request = _request()
    engine = DeterministicSingleSeriesResearchEngine()
    first = engine.analyze(request)
    second = engine.analyze(request)
    assert first == second
    assert first.model_dump_json() == second.model_dump_json()
    with pytest.raises(ValidationError, match="frozen"):
        first.setup_snapshots = ()


def test_request_rejects_extra_fields_and_noncanonical_nested_state() -> None:
    request = _request()
    with pytest.raises(ValidationError, match="extra"):
        SingleSeriesResearchRequest.model_validate(
            {**request.model_dump(mode="python"), "unexpected": True}
        )
    forged = request.model_copy(
        update={
            "technical_config": TechnicalAnalysisConfig.model_construct(
                sma_periods=(0,)
            )
        }
    )
    with pytest.raises(ValidationError, match="noncanonical"):
        SingleSeriesResearchRequest(
            history=forged.history,
            technical_config=forged.technical_config,
            evidence_config=forged.evidence_config,
        )


def test_engine_rejects_wrong_request_type_and_bypassed_request() -> None:
    engine = DeterministicSingleSeriesResearchEngine()
    with pytest.raises(SingleSeriesResearchInvalidInputError, match="request"):
        engine.analyze(cast(SingleSeriesResearchRequest, object()))
    valid = _request()
    bypassed = valid.model_copy(
        update={"history": MaterializedBarHistory.model_construct(bars=())}
    )
    with pytest.raises(SingleSeriesResearchInvalidInputError, match="request"):
        engine.analyze(bypassed)


class _FailingTechnicalEngine:
    def analyze(
        self, bars: object, config: object
    ) -> tuple[()]:
        raise TechnicalAnalysisInvalidInputError("injected failure")


def test_downstream_stage_failure_is_chained_into_pipeline_error() -> None:
    engine = DeterministicSingleSeriesResearchEngine(
        technical_engine=cast(TechnicalAnalysisEngine, _FailingTechnicalEngine())
    )
    with pytest.raises(SingleSeriesResearchComputationError) as captured:
        engine.analyze(_request())
    assert isinstance(captured.value.__cause__, TechnicalAnalysisInvalidInputError)


class _TraceTechnicalEngine:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace

    def analyze(self, bars: object, config: object) -> object:
        self.trace.append("technical")
        return DeterministicTechnicalAnalysisEngine().analyze(
            cast(tuple[MarketBar, ...], bars), cast(TechnicalAnalysisConfig, config)
        )


class _TraceEvidenceEngine:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace

    def analyze(self, history: object, config: object) -> object:
        self.trace.append("evidence")
        return DeterministicMarketEvidenceEngine().analyze(
            cast(AlignedTechnicalHistory, history), cast(MarketEvidenceConfig, config)
        )


class _TraceSetupEngine:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace

    def analyze(self, history: object) -> object:
        self.trace.append("setups")
        return DeterministicSetupCompositionEngine().analyze(
            cast(AlignedEvidenceHistory, history)
        )


def test_injected_stages_run_once_in_frozen_order() -> None:
    trace: list[str] = []
    engine = DeterministicSingleSeriesResearchEngine(
        technical_engine=cast(TechnicalAnalysisEngine, _TraceTechnicalEngine(trace)),
        evidence_engine=cast(MarketEvidenceEngine, _TraceEvidenceEngine(trace)),
        setup_engine=cast(SetupCompositionEngine, _TraceSetupEngine(trace)),
    )
    engine.analyze(_request())
    assert trace == ["technical", "evidence", "setups"]


def test_result_rejects_count_and_timestamp_misalignment() -> None:
    result = DeterministicSingleSeriesResearchEngine().analyze(_request())
    with pytest.raises(ValidationError, match="one-to-one"):
        SingleSeriesResearchResult(
            request=result.request,
            technical_snapshots=result.technical_snapshots[:-1],
            evidence_snapshots=result.evidence_snapshots,
            setup_snapshots=result.setup_snapshots,
        )
    wrong = result.setup_snapshots[0].model_copy(
        update={"timestamp": _START + timedelta(hours=1)}
    )
    with pytest.raises(ValidationError, match="timestamp"):
        SingleSeriesResearchResult(
            request=result.request,
            technical_snapshots=result.technical_snapshots,
            evidence_snapshots=result.evidence_snapshots,
            setup_snapshots=(wrong, *result.setup_snapshots[1:]),
        )


def test_phase14_has_no_forbidden_authority_or_side_effect_dependencies() -> None:
    source = inspect.getsource(engine_module) + inspect.getsource(models_module)
    forbidden = (
        "sqlite",
        "app.data.replay",
        "app.data.observation_store",
        "app.data.providers",
        "httpx",
        "requests",
        "socket",
        "datetime.now",
        "time.time",
        "random",
        "secrets",
        "app.scanner",
        "app.risk",
        "app.desks",
        "app.llm",
        "app.alerts",
        "Angelo",
    )
    assert not any(item in source for item in forbidden)
