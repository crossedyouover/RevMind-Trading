"""Adversarial Phase 15 deterministic universe-coordination tests."""

import inspect
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from pydantic import ValidationError

import app.universe.engine as engine_module
import app.universe.models as models_module
from app.core.schemas import AssetClass, Instrument, Timeframe
from app.evidence import MarketEvidenceConfig
from app.materialization import BarSeriesRequest, MaterializedBar, MaterializedBarHistory
from app.research import (
    DeterministicSingleSeriesResearchEngine,
    SingleSeriesResearchRequest,
    SingleSeriesResearchResult,
)
from app.scanner import ScannerEngine, ScannerInvalidInputError
from app.technical import TechnicalAnalysisConfig
from app.universe import (
    DeterministicUniverseCoordinationEngine,
    UniverseCoordinationComputationError,
    UniverseCoordinationInvalidInputError,
    UniverseCoordinationRequest,
    UniverseSeriesStatus,
)
from tests.test_single_series_research import _history

_KNOWLEDGE = datetime(2025, 2, 1, tzinfo=UTC)
_SCAN = datetime(2025, 1, 25, tzinfo=UTC)


def _series(symbol: str, count: int = 25) -> SingleSeriesResearchResult:
    instrument = Instrument(
        symbol=symbol, asset_class=AssetClass.EQUITY, exchange="XNAS", currency="USD"
    )
    base = _history(count)
    bars = tuple(
        MaterializedBar(
            bar=item.bar.model_copy(update={"instrument": instrument}),
            observation_id=item.observation_id,
            observed_at=item.observed_at,
            source=item.source,
            source_record_id=item.source_record_id,
        )
        for item in base.bars
    )
    history = MaterializedBarHistory(
        request=BarSeriesRequest(
            instrument=instrument,
            timeframe=Timeframe.ONE_DAY,
            source=base.request.source,
            as_of=_KNOWLEDGE,
        ),
        bars=bars,
        inspected_observation_count=count,
        eligible_bar_candidate_count=count,
    )
    return DeterministicSingleSeriesResearchEngine().analyze(
        SingleSeriesResearchRequest(
            history=history,
            technical_config=TechnicalAnalysisConfig(),
            evidence_config=MarketEvidenceConfig(),
        )
    )


def _request(*results: SingleSeriesResearchResult) -> UniverseCoordinationRequest:
    return UniverseCoordinationRequest(
        knowledge_as_of=_KNOWLEDGE,
        scan_as_of=_SCAN,
        timeframe=Timeframe.ONE_DAY,
        series_results=results,
    )


def test_available_series_select_latest_at_cutoff_and_scan_once() -> None:
    result = DeterministicUniverseCoordinationEngine().coordinate(
        _request(_series("AAPL"), _series("MSFT"))
    )
    assert len(result.selections) == 2
    assert all(item.status is UniverseSeriesStatus.AVAILABLE for item in result.selections)
    assert all(item.selected_setup is not None for item in result.selections)
    assert tuple(item.setup_snapshot for item in result.scanner_snapshot.results) == tuple(
        item.selected_setup for item in result.selections
    )
    assert all(item.setup_snapshot.timestamp == _SCAN for item in result.scanner_snapshot.results)


def test_empty_and_post_cutoff_series_are_retained_but_not_fabricated() -> None:
    empty = _series("AAPL", 0)
    post_cutoff = _series("MSFT", 25)
    request = UniverseCoordinationRequest(
        knowledge_as_of=_KNOWLEDGE,
        scan_as_of=datetime(2024, 12, 31, tzinfo=UTC),
        timeframe=Timeframe.ONE_DAY,
        series_results=(empty, post_cutoff),
    )
    result = DeterministicUniverseCoordinationEngine().coordinate(request)
    assert len(result.selections) == 2
    assert all(
        item.status is UniverseSeriesStatus.NO_ELIGIBLE_HISTORY
        for item in result.selections
    )
    assert result.scanner_snapshot.results == ()


def test_request_requires_shared_pit_boundary_timeframe_and_canonical_order() -> None:
    aapl, msft = _series("AAPL"), _series("MSFT")
    with pytest.raises(ValidationError, match="canonical instrument order"):
        _request(msft, aapl)
    with pytest.raises(ValidationError, match="canonical instrument order"):
        _request(aapl, aapl)
    with pytest.raises(ValidationError, match="knowledge_as_of"):
        UniverseCoordinationRequest(
            knowledge_as_of=_KNOWLEDGE + timedelta(seconds=1),
            scan_as_of=_SCAN,
            timeframe=Timeframe.ONE_DAY,
            series_results=(aapl,),
        )


def test_empty_universe_is_valid_and_deterministic() -> None:
    engine = DeterministicUniverseCoordinationEngine()
    first = engine.coordinate(_request())
    second = engine.coordinate(_request())
    assert first == second
    assert first.selections == ()
    assert first.scanner_snapshot.results == ()
    assert first.model_dump_json() == second.model_dump_json()


def test_wrong_or_bypassed_request_fails_closed() -> None:
    engine = DeterministicUniverseCoordinationEngine()
    with pytest.raises(UniverseCoordinationInvalidInputError):
        engine.coordinate(cast(UniverseCoordinationRequest, object()))
    valid = _request(_series("AAPL"))
    forged = valid.model_copy(update={"series_results": (object(),)})
    with pytest.raises(UniverseCoordinationInvalidInputError):
        engine.coordinate(forged)


class _FailingScanner:
    def scan(self, universe: object) -> object:
        raise ScannerInvalidInputError("injected failure")


def test_scanner_failure_is_chained_and_no_fallback_occurs() -> None:
    engine = DeterministicUniverseCoordinationEngine(
        scanner=cast(ScannerEngine, _FailingScanner())
    )
    with pytest.raises(UniverseCoordinationComputationError) as captured:
        engine.coordinate(_request(_series("AAPL")))
    assert isinstance(captured.value.__cause__, ScannerInvalidInputError)


def test_phase15_has_no_forbidden_authority_or_side_effects() -> None:
    source = inspect.getsource(engine_module) + inspect.getsource(models_module)
    forbidden = (
        "sqlite", "app.data.replay", "app.data.providers", "httpx", "socket",
        "datetime.now", "time.time", "random", "secrets", "app.risk", "app.desks",
        "app.llm", "app.alerts", "Angelo", "except Exception",
    )
    assert not any(item in source for item in forbidden)
