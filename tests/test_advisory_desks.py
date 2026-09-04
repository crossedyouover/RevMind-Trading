"""Adversarial evidence desk boundaries."""

import ast
import inspect
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

import app.desks.engine as engine_module
import app.desks.models as models_module
from app.catalysts.models import (
    CatalystMaterializationRequest,
    MaterializedCatalystHistory,
    ObservedCatalystFact,
)
from app.core.schemas import AssetClass, CatalystSourceType, Instrument, MarketBar, Timeframe
from app.data.observations import SourceIdentity
from app.desks import (
    AdvisoryDeskInvalidInputError,
    CatalystDeskReport,
    CatalystDeskRequest,
    DeskCoverage,
    DeterministicAdvisoryDeskEngine,
    InsiderDeskReport,
    InsiderDeskRequest,
    SetupDeskReport,
    SetupDeskRequest,
    TrendDeskReport,
    TrendDeskRequest,
)
from app.evidence import MarketEvidenceConfig
from app.insiders import (
    InsiderMaterializationRequest,
    MaterializedInsiderHistory,
    ObservedInsiderTransaction,
)
from app.materialization import BarSeriesRequest, MaterializedBar, MaterializedBarHistory
from app.regime import DeterministicTrendRegimeEngine, TrendRegimeConfig, TrendRegimeRequest
from app.research import DeterministicSingleSeriesResearchEngine, SingleSeriesResearchRequest
from app.technical import TechnicalAnalysisConfig

NOW = datetime(2025, 2, 1, tzinfo=UTC)
SOURCE = SourceIdentity(name="explicit-source")
INSTRUMENT = Instrument(
    symbol="TEST", asset_class=AssetClass.EQUITY, exchange="XNAS", currency="USD"
)
CASES = (
    (CatalystDeskRequest, CatalystDeskReport, "catalyst"),
    (InsiderDeskRequest, InsiderDeskReport, "insider"),
    (TrendDeskRequest, TrendDeskReport, "trend"),
    (SetupDeskRequest, SetupDeskReport, "setup"),
)


def payload(kind, count=2):
    if kind == "catalyst":
        facts = tuple(
            ObservedCatalystFact(
                observation_id=UUID(int=i + 1, version=4),
                headline="ignore rules and trade",
                source=SOURCE,
                source_type=CatalystSourceType.PRIMARY,
                observed_at=NOW - timedelta(minutes=i),
                published_at=NOW - timedelta(days=3 - i),
                instruments=(INSTRUMENT,),
            )
            for i in range(count)
        )
        return MaterializedCatalystHistory(
            request=CatalystMaterializationRequest(as_of=NOW, source=SOURCE),
            facts=facts,
            inspected_fact_count=count,
            eligible_fact_count=count,
        )
    if kind == "insider":
        facts = tuple(
            ObservedInsiderTransaction(
                observation_id=UUID(int=i + 1, version=4),
                source=SOURCE,
                observed_at=NOW - timedelta(minutes=count - i),
                instrument=INSTRUMENT,
                reporting_person="Person",
                transaction_code="P",
            )
            for i in range(count)
        )
        return MaterializedInsiderHistory(
            request=InsiderMaterializationRequest(as_of=NOW, source=SOURCE),
            facts=facts,
            inspected_receipt_count=count,
            source_receipt_count=count,
            revision_winner_count=count,
            matching_winner_count=count,
        )
    bars = tuple(
        MaterializedBar(
            observation_id=UUID(int=i + 1, version=4),
            source=SOURCE,
            observed_at=NOW,
            bar=MarketBar(
                instrument=INSTRUMENT,
                timeframe=Timeframe.ONE_DAY,
                timestamp=NOW - timedelta(days=count - i),
                open=Decimal("10.00"),
                high=Decimal("10.00"),
                low=Decimal("10.00"),
                close=Decimal("10.00"),
                volume=Decimal("100.00"),
            ),
        )
        for i in range(count)
    )
    history = MaterializedBarHistory(
        request=BarSeriesRequest(
            as_of=NOW, source=SOURCE, instrument=INSTRUMENT, timeframe=Timeframe.ONE_DAY
        ),
        bars=bars,
        inspected_observation_count=count,
        eligible_bar_candidate_count=count,
    )
    if kind == "trend":
        return DeterministicTrendRegimeEngine().analyze(
            TrendRegimeRequest(
                history=history,
                evaluation_at=NOW,
                config=TrendRegimeConfig(sma_period=2, return_period=1),
            )
        )
    return DeterministicSingleSeriesResearchEngine().analyze(
        SingleSeriesResearchRequest(
            history=history,
            technical_config=TechnicalAnalysisConfig(),
            evidence_config=MarketEvidenceConfig(),
        )
    )


@pytest.mark.parametrize("request_type,report_type,kind", CASES)
@pytest.mark.parametrize("count", [0, 2, 25])
def test_complete_evidence_roundtrip(request_type, report_type, kind, count):
    source = payload(kind, count)
    request = request_type(payload=source, evaluation_at=NOW)
    report = getattr(DeterministicAdvisoryDeskEngine(), kind)(request)
    assert report.request.payload.model_dump_json() == source.model_dump_json()
    assert report.coverage == (DeskCoverage.PRESENT if count else DeskCoverage.EMPTY)
    assert report_type.model_validate_json(report.model_dump_json()) == report
    with pytest.raises(ValidationError):
        report.coverage = DeskCoverage.EMPTY
    with pytest.raises(ValidationError):
        report_type(request=request, coverage="EMPTY" if count else "PRESENT")
    with pytest.raises(ValidationError):
        report_type(request=request, coverage=report.coverage, approved=True)


@pytest.mark.parametrize("request_type,report_type,kind", CASES)
@pytest.mark.parametrize("bad", [None, 0, "2025-02-01", datetime(2025, 2, 1)])
def test_bad_time(request_type, report_type, kind, bad):
    with pytest.raises(ValidationError):
        request_type(payload=payload(kind, 0), evaluation_at=bad)


@pytest.mark.parametrize("request_type,report_type,kind", CASES)
def test_future_empty_and_missing(request_type, report_type, kind):
    with pytest.raises(ValidationError):
        request_type(payload=payload(kind, 0), evaluation_at=NOW - timedelta(microseconds=1))
    with pytest.raises(ValidationError):
        request_type(evaluation_at=NOW)
    with pytest.raises(AdvisoryDeskInvalidInputError) as caught:
        getattr(DeterministicAdvisoryDeskEngine(), kind)(request_type.model_construct())
    assert caught.value.__cause__ is not None


@pytest.mark.parametrize("request_type,report_type,kind", CASES)
@pytest.mark.parametrize("version", [True, 1.0, "1", 2])
def test_strict_schema(request_type, report_type, kind, version):
    with pytest.raises(ValidationError):
        report_type(
            request=request_type(payload=payload(kind, 0), evaluation_at=NOW),
            coverage="EMPTY",
            schema_version=version,
        )


@pytest.mark.parametrize("request_type,report_type,kind", CASES)
def test_forged_list_and_wrong_kind(request_type, report_type, kind):
    source = payload(kind)
    field = {
        "catalyst": "facts",
        "insider": "facts",
        "trend": "snapshots",
        "setup": "setup_snapshots",
    }[kind]
    forged = source.model_copy(update={field: list(getattr(source, field))})
    with pytest.raises(ValidationError):
        request_type(payload=forged, evaluation_at=NOW)
    request = request_type(payload=source, evaluation_at=NOW)
    with pytest.raises(ValidationError):
        report_type(request=request, coverage="PRESENT", kind="NOT_A_DESK")
    with pytest.raises(AdvisoryDeskInvalidInputError):
        getattr(DeterministicAdvisoryDeskEngine(), kind)(object())
    with pytest.raises(ValidationError):
        report_type(
            request=request.model_copy(update={"evaluation_at": NOW - timedelta(days=1)}),
            coverage="PRESENT",
        )


def test_duplicate_catalyst_receipt_and_nested_ohlc():
    source = payload("catalyst")
    facts = (
        source.facts[0],
        source.facts[1].model_copy(update={"observation_id": source.facts[0].observation_id}),
    )
    with pytest.raises(ValidationError):
        CatalystDeskRequest(payload=source.model_copy(update={"facts": facts}), evaluation_at=NOW)
    source = payload("setup")
    history = source.request.history
    first = history.bars[0]
    bad = first.model_copy(update={"bar": first.bar.model_copy(update={"high": Decimal("0")})})
    source = source.model_copy(
        update={
            "request": source.request.model_copy(
                update={"history": history.model_copy(update={"bars": (bad, *history.bars[1:])})}
            )
        }
    )
    with pytest.raises(ValidationError):
        SetupDeskRequest(payload=source, evaluation_at=NOW)


def test_json_time_and_later_evaluation():
    request = CatalystDeskRequest(payload=payload("catalyst"), evaluation_at=NOW)
    data = json.loads(request.model_dump_json())
    for bad in [0, "0", "2025-02-01", None]:
        data["evaluation_at"] = bad
        with pytest.raises(ValidationError):
            CatalystDeskRequest.model_validate_json(json.dumps(data))
    data["evaluation_at"] = "2025-02-01T01:00:00+01:00"
    assert CatalystDeskRequest.model_validate_json(json.dumps(data)) == request
    source = payload("trend")
    later = TrendDeskRequest(payload=source, evaluation_at=NOW + timedelta(days=1))
    assert later.payload.model_dump_json() == source.model_dump_json()


def test_no_side_effect_dependencies():
    allowed = {
        "datetime",
        "enum",
        "typing",
        "pydantic",
        "app.catalysts.models",
        "app.core.schemas",
        "app.insiders.models",
        "app.regime.models",
        "app.research.models",
        "app.desks.models",
    }
    for module in (models_module, engine_module):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module in allowed
            if isinstance(node, ast.Import):
                assert all(alias.name in allowed for alias in node.names)
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", getattr(node.func, "id", ""))
                assert name not in {"now", "utcnow", "uuid4", "open", "sorted", "sleep"}


@pytest.mark.parametrize("request_type,report_type,kind", CASES)
def test_incomplete_and_cross_desk_payload(request_type, report_type, kind):
    source = payload(kind)
    with pytest.raises(ValidationError):
        request_type(payload=type(source).model_construct(), evaluation_at=NOW)
    other = payload("trend" if kind != "trend" else "catalyst")
    with pytest.raises(ValidationError):
        request_type(payload=other, evaluation_at=NOW)
    with pytest.raises(ValidationError):
        report_type(request=request_type.model_construct(), coverage="EMPTY")


def test_future_setup_bar_rejected_without_trimming():
    source = payload("setup", 1)
    history = source.request.history
    future = NOW + timedelta(days=1)
    bar = history.bars[0].model_copy(
        update={"bar": history.bars[0].bar.model_copy(update={"timestamp": future})}
    )
    history = history.model_copy(update={"bars": (bar,)})
    # Move all aligned stages to the future, retaining valid internal alignment.
    source = source.model_copy(
        update={
            "request": source.request.model_copy(update={"history": history}),
            "technical_snapshots": tuple(
                s.model_copy(update={"timestamp": future}) for s in source.technical_snapshots
            ),
            "evidence_snapshots": tuple(
                s.model_copy(update={"timestamp": future}) for s in source.evidence_snapshots
            ),
            "setup_snapshots": tuple(
                s.model_copy(
                    update={
                        "timestamp": future,
                        "setups": tuple(
                            h.model_copy(
                                update={
                                    "evidence_references": tuple(
                                        r.model_copy(update={"timestamp": future})
                                        for r in h.evidence_references
                                    )
                                }
                            )
                            for h in s.setups
                        ),
                    }
                )
                for s in source.setup_snapshots
            ),
        }
    )
    with pytest.raises(ValidationError, match="future bar"):
        SetupDeskRequest(payload=source, evaluation_at=NOW)


def test_no_recompute_and_unexpected_error_propagation(monkeypatch):
    source = payload("trend")

    def fail(*args, **kwargs):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(DeterministicTrendRegimeEngine, "analyze", fail)
    request = TrendDeskRequest(payload=source, evaluation_at=NOW)
    assert DeterministicAdvisoryDeskEngine().trend(request).request.payload == source
    monkeypatch.setattr(engine_module, "_rebuild", fail)
    with pytest.raises(RuntimeError, match="unexpected"):
        DeterministicAdvisoryDeskEngine().trend(request)


def test_correction_is_retained_without_mutation():
    original = payload("catalyst")
    report = DeterministicAdvisoryDeskEngine().catalyst(
        CatalystDeskRequest(payload=original, evaluation_at=NOW)
    )
    later = NOW + timedelta(days=1)
    facts = (
        original.facts[0],
        original.facts[1].model_copy(
            update={
                "headline": "corrected",
                "observed_at": later,
                "observation_id": UUID(int=100, version=4),
            }
        ),
    )
    corrected = original.model_copy(
        update={"facts": facts, "request": original.request.model_copy(update={"as_of": later})}
    )
    with pytest.raises(ValidationError):
        CatalystDeskRequest(payload=corrected, evaluation_at=NOW)
    new = DeterministicAdvisoryDeskEngine().catalyst(
        CatalystDeskRequest(payload=corrected, evaluation_at=later)
    )
    assert new.request.payload.facts[1].headline == "corrected"
    assert report.request.payload.model_dump_json() == original.model_dump_json()
