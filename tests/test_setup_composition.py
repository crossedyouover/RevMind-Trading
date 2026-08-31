"""Adversarial tests for deterministic Phase 9 setup composition."""

import inspect
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

import pytest
from pydantic import ValidationError

import app.setups.engine as setup_engine_module
import app.setups.models as setup_models_module
from app.core.schemas import AssetClass, Instrument, MarketBar, Timeframe
from app.evidence import (
    AlignedTechnicalHistory,
    DeterministicMarketEvidenceEngine,
    MarketEvidenceConfig,
    MarketEvidenceKey,
    MarketEvidenceStatus,
)
from app.setups import (
    SETUP_KEY_ORDER,
    AlignedEvidenceHistory,
    DeterministicSetupCompositionEngine,
    SetupCompositionInvalidInputError,
    SetupEvidenceReference,
    SetupHypothesis,
    SetupKey,
    SetupSnapshot,
    SetupStatus,
)
from app.technical import DeterministicTechnicalAnalysisEngine, TechnicalAnalysisConfig

_START = datetime(2025, 1, 1, tzinfo=UTC)
_INSTRUMENT = Instrument(symbol="NVDA", asset_class=AssetClass.EQUITY, exchange="XNAS")


def _bars(
    closes: list[Decimal],
    *,
    instrument: Instrument = _INSTRUMENT,
    timeframe: Timeframe = Timeframe.ONE_DAY,
    start_index: int = 0,
    step: int = 1,
) -> tuple[MarketBar, ...]:
    return tuple(
        MarketBar(
            instrument=instrument,
            timeframe=timeframe,
            timestamp=_START + timedelta(days=start_index + index * step),
            open=close,
            high=close,
            low=close,
            close=close,
            volume=Decimal(100 + index),
        )
        for index, close in enumerate(closes)
    )


def _evidence_history(
    closes: list[Decimal],
    *,
    instrument: Instrument = _INSTRUMENT,
    timeframe: Timeframe = Timeframe.ONE_DAY,
    start_index: int = 0,
    step: int = 1,
) -> AlignedEvidenceHistory:
    bars = _bars(
        closes,
        instrument=instrument,
        timeframe=timeframe,
        start_index=start_index,
        step=step,
    )
    technical = DeterministicTechnicalAnalysisEngine().analyze(
        bars, TechnicalAnalysisConfig()
    )
    evidence = DeterministicMarketEvidenceEngine().analyze(
        AlignedTechnicalHistory(bars=bars, technical_snapshots=technical),
        MarketEvidenceConfig(),
    )
    return AlignedEvidenceHistory(snapshots=evidence)


def _references(
    key: SetupKey,
    first: MarketEvidenceStatus,
    second: MarketEvidenceStatus,
    *,
    timestamp: datetime = _START,
) -> tuple[SetupEvidenceReference, ...]:
    keys = {
        SetupKey.UPSIDE_BREAKOUT_ABOVE_SMA: (
            MarketEvidenceKey.PRICE_ABOVE_SMA,
            MarketEvidenceKey.CLOSE_BREAKOUT_ABOVE_PRIOR_HIGH,
        ),
        SetupKey.DOWNSIDE_BREAKDOWN_BELOW_SMA: (
            MarketEvidenceKey.PRICE_BELOW_SMA,
            MarketEvidenceKey.CLOSE_BREAKDOWN_BELOW_PRIOR_LOW,
        ),
    }[key]
    return (
        SetupEvidenceReference(timestamp=timestamp, key=keys[0], status=first),
        SetupEvidenceReference(timestamp=timestamp, key=keys[1], status=second),
    )


def _hypothesis(
    key: SetupKey,
    status: SetupStatus,
    first: MarketEvidenceStatus,
    second: MarketEvidenceStatus,
    *,
    timestamp: datetime = _START,
) -> SetupHypothesis:
    return SetupHypothesis(
        key=key,
        status=status,
        evidence_references=_references(key, first, second, timestamp=timestamp),
    )


def test_history_empty_single_gap_tuple_and_immutability() -> None:
    empty = AlignedEvidenceHistory(snapshots=())
    assert DeterministicSetupCompositionEngine().analyze(empty) == ()
    single = _evidence_history([Decimal(1)])
    assert len(single.snapshots) == 1
    gapped = _evidence_history([Decimal(1), Decimal(2)], step=10)
    assert len(gapped.snapshots) == 2
    with pytest.raises(ValidationError):
        AlignedEvidenceHistory.model_validate({"snapshots": list(single.snapshots)})
    with pytest.raises(ValidationError):
        single.snapshots = ()


@pytest.mark.parametrize(
    ("instrument", "timeframe", "message"),
    (
        (
            Instrument(symbol="NVDA", asset_class=AssetClass.EQUITY, exchange="XNYS"),
            Timeframe.ONE_DAY,
            "instruments",
        ),
        (
            Instrument(symbol="NVDA", asset_class=AssetClass.CRYPTO, exchange="XNAS"),
            Timeframe.ONE_DAY,
            "instruments",
        ),
        (
            Instrument(
                symbol="NVDA",
                asset_class=AssetClass.EQUITY,
                exchange="XNAS",
                currency="EUR",
            ),
            Timeframe.ONE_DAY,
            "instruments",
        ),
        (_INSTRUMENT, Timeframe.ONE_HOUR, "timeframes"),
    ),
)
def test_history_rejects_mixed_identity_and_timeframe(
    instrument: Instrument, timeframe: Timeframe, message: str
) -> None:
    first = _evidence_history([Decimal(1)])
    second = _evidence_history(
        [Decimal(2)], instrument=instrument, timeframe=timeframe, start_index=1
    )
    with pytest.raises(ValidationError, match=message):
        AlignedEvidenceHistory(snapshots=(first.snapshots[0], second.snapshots[0]))


def test_history_rejects_duplicate_decreasing_and_reordered_snapshots() -> None:
    history = _evidence_history([Decimal(1), Decimal(2)])
    with pytest.raises(ValidationError, match="duplicate"):
        AlignedEvidenceHistory(snapshots=(history.snapshots[0], history.snapshots[0]))
    with pytest.raises(ValidationError, match="strictly increasing"):
        AlignedEvidenceHistory(snapshots=tuple(reversed(history.snapshots)))


def test_engine_revalidates_model_copy_and_rejects_non_history() -> None:
    history = _evidence_history([Decimal(1), Decimal(2)])
    copied = history.model_copy(
        update={"snapshots": (history.snapshots[0], history.snapshots[0])}
    )
    engine = DeterministicSetupCompositionEngine()
    with pytest.raises(SetupCompositionInvalidInputError):
        engine.analyze(copied)
    with pytest.raises(SetupCompositionInvalidInputError):
        engine.analyze(cast(AlignedEvidenceHistory, object()))
    malformed_snapshot = history.snapshots[0].model_copy(
        update={"evidence": history.snapshots[0].evidence[:-1]}
    )
    malformed = history.model_copy(update={"snapshots": (malformed_snapshot,)})
    with pytest.raises(SetupCompositionInvalidInputError):
        engine.analyze(malformed)
    duplicate_snapshot = history.snapshots[0].model_copy(
        update={
            "evidence": history.snapshots[0].evidence[:-1]
            + (history.snapshots[0].evidence[0],)
        }
    )
    reordered_snapshot = history.snapshots[0].model_copy(
        update={"evidence": tuple(reversed(history.snapshots[0].evidence))}
    )
    for invalid_snapshot in (duplicate_snapshot, reordered_snapshot):
        invalid_history = history.model_copy(update={"snapshots": (invalid_snapshot,)})
        with pytest.raises(SetupCompositionInvalidInputError):
            engine.analyze(invalid_history)


def test_engine_rejects_forged_nested_phase8_state() -> None:
    history = _evidence_history([Decimal(index) for index in range(1, 22)])
    snapshot = history.snapshots[-1]
    price = snapshot.evidence[1]
    forged_status = price.model_copy(update={"status": MarketEvidenceStatus.ACTIVE})
    forged_source = price.feature_sources[0].model_copy(
        update={"status": "FORGED", "value": None}
    )
    forged_provenance = price.model_copy(update={"feature_sources": (forged_source,)})
    forged_measurement = price.measurements[0].model_copy(update={"value": "FORGED"})
    forged_measurements = price.model_copy(update={"measurements": (forged_measurement,)})
    engine = DeterministicSetupCompositionEngine()
    for forged in (forged_status, forged_provenance, forged_measurements):
        evidence = list(snapshot.evidence)
        evidence[1] = forged
        copied_snapshot = snapshot.model_copy(update={"evidence": tuple(evidence)})
        copied_history = history.model_copy(update={"snapshots": (copied_snapshot,)})
        with pytest.raises(SetupCompositionInvalidInputError):
            engine.analyze(copied_history)


def test_exact_catalogue_and_engine_output_order() -> None:
    assert tuple(SetupKey) == SETUP_KEY_ORDER == (
        SetupKey.UPSIDE_BREAKOUT_ABOVE_SMA,
        SetupKey.DOWNSIDE_BREAKDOWN_BELOW_SMA,
    )
    output = DeterministicSetupCompositionEngine().analyze(
        _evidence_history([Decimal(1)])
    )[0]
    assert tuple(item.key for item in output.setups) == SETUP_KEY_ORDER


@pytest.mark.parametrize("key", tuple(SetupKey))
@pytest.mark.parametrize(
    ("first", "second", "expected"),
    (
        (MarketEvidenceStatus.ACTIVE, MarketEvidenceStatus.ACTIVE, SetupStatus.ACTIVE),
        (MarketEvidenceStatus.INACTIVE, MarketEvidenceStatus.ACTIVE, SetupStatus.INACTIVE),
        (MarketEvidenceStatus.ACTIVE, MarketEvidenceStatus.INACTIVE, SetupStatus.INACTIVE),
        (MarketEvidenceStatus.INACTIVE, MarketEvidenceStatus.INACTIVE, SetupStatus.INACTIVE),
        (MarketEvidenceStatus.WARMING_UP, MarketEvidenceStatus.ACTIVE, SetupStatus.WARMING_UP),
        (MarketEvidenceStatus.ACTIVE, MarketEvidenceStatus.WARMING_UP, SetupStatus.WARMING_UP),
        (MarketEvidenceStatus.UNDEFINED, MarketEvidenceStatus.ACTIVE, SetupStatus.UNDEFINED),
        (MarketEvidenceStatus.ACTIVE, MarketEvidenceStatus.UNDEFINED, SetupStatus.UNDEFINED),
        (
            MarketEvidenceStatus.WARMING_UP,
            MarketEvidenceStatus.UNDEFINED,
            SetupStatus.WARMING_UP,
        ),
        (
            MarketEvidenceStatus.UNDEFINED,
            MarketEvidenceStatus.WARMING_UP,
            SetupStatus.WARMING_UP,
        ),
        (
            MarketEvidenceStatus.WARMING_UP,
            MarketEvidenceStatus.INACTIVE,
            SetupStatus.WARMING_UP,
        ),
        (
            MarketEvidenceStatus.INACTIVE,
            MarketEvidenceStatus.WARMING_UP,
            SetupStatus.WARMING_UP,
        ),
        (
            MarketEvidenceStatus.UNDEFINED,
            MarketEvidenceStatus.INACTIVE,
            SetupStatus.UNDEFINED,
        ),
        (
            MarketEvidenceStatus.INACTIVE,
            MarketEvidenceStatus.UNDEFINED,
            SetupStatus.UNDEFINED,
        ),
    ),
)
def test_exact_composition_matrix(
    key: SetupKey,
    first: MarketEvidenceStatus,
    second: MarketEvidenceStatus,
    expected: SetupStatus,
) -> None:
    assert _hypothesis(key, expected, first, second).status is expected


@pytest.mark.parametrize("key", tuple(SetupKey))
@pytest.mark.parametrize(
    ("declared", "first", "second"),
    (
        (SetupStatus.ACTIVE, MarketEvidenceStatus.INACTIVE, MarketEvidenceStatus.ACTIVE),
        (SetupStatus.ACTIVE, MarketEvidenceStatus.WARMING_UP, MarketEvidenceStatus.ACTIVE),
        (SetupStatus.ACTIVE, MarketEvidenceStatus.UNDEFINED, MarketEvidenceStatus.ACTIVE),
        (SetupStatus.INACTIVE, MarketEvidenceStatus.ACTIVE, MarketEvidenceStatus.ACTIVE),
        (SetupStatus.INACTIVE, MarketEvidenceStatus.WARMING_UP, MarketEvidenceStatus.INACTIVE),
        (SetupStatus.UNDEFINED, MarketEvidenceStatus.WARMING_UP, MarketEvidenceStatus.UNDEFINED),
    ),
)
def test_hypothesis_rejects_contradictory_status(
    key: SetupKey,
    declared: SetupStatus,
    first: MarketEvidenceStatus,
    second: MarketEvidenceStatus,
) -> None:
    with pytest.raises(ValidationError, match="contradicts"):
        _hypothesis(key, declared, first, second)


def test_hypothesis_rejects_wrong_missing_duplicate_reordered_and_timestamps() -> None:
    references = _references(
        SetupKey.UPSIDE_BREAKOUT_ABOVE_SMA,
        MarketEvidenceStatus.ACTIVE,
        MarketEvidenceStatus.ACTIVE,
    )
    invalid_references = (
        references[:1],
        references + (references[1],),
        tuple(reversed(references)),
        (
            references[0].model_copy(update={"key": MarketEvidenceKey.RSI_MIDRANGE}),
            references[1],
        ),
        (
            references[0],
            references[1].model_copy(update={"timestamp": _START + timedelta(days=1)}),
        ),
    )
    for invalid in invalid_references:
        with pytest.raises(ValidationError):
            SetupHypothesis(
                key=SetupKey.UPSIDE_BREAKOUT_ABOVE_SMA,
                status=SetupStatus.ACTIVE,
                evidence_references=invalid,
            )
    forged_reference = references[0].model_copy(update={"status": "FORGED"})
    with pytest.raises(ValidationError, match="contradicts|canonical"):
        SetupHypothesis(
            key=SetupKey.UPSIDE_BREAKOUT_ABOVE_SMA,
            status=SetupStatus.INACTIVE,
            evidence_references=(forged_reference, references[1]),
        )


def test_snapshot_rejects_missing_duplicate_reordered_and_wrong_timestamp() -> None:
    upside = _hypothesis(
        SetupKey.UPSIDE_BREAKOUT_ABOVE_SMA,
        SetupStatus.ACTIVE,
        MarketEvidenceStatus.ACTIVE,
        MarketEvidenceStatus.ACTIVE,
    )
    downside = _hypothesis(
        SetupKey.DOWNSIDE_BREAKDOWN_BELOW_SMA,
        SetupStatus.INACTIVE,
        MarketEvidenceStatus.INACTIVE,
        MarketEvidenceStatus.ACTIVE,
    )
    for setups in (
        (upside,),
        (upside, downside, downside),
        (upside, upside),
        (downside, upside),
    ):
        with pytest.raises(ValidationError):
            SetupSnapshot(
                instrument=_INSTRUMENT,
                timeframe=Timeframe.ONE_DAY,
                timestamp=_START,
                setups=setups,
            )
    with pytest.raises(ValidationError, match="timestamp"):
        SetupSnapshot(
            instrument=_INSTRUMENT,
            timeframe=Timeframe.ONE_DAY,
            timestamp=_START + timedelta(days=1),
            setups=(upside, downside),
        )
    for field, value in (("instrument", object()), ("timeframe", object())):
        payload: dict[str, object] = {
            "instrument": _INSTRUMENT,
            "timeframe": Timeframe.ONE_DAY,
            "timestamp": _START,
            "setups": (upside, downside),
        }
        payload[field] = value
        with pytest.raises(ValidationError):
            SetupSnapshot.model_validate(payload)
    forged_hypothesis = upside.model_copy(update={"status": SetupStatus.INACTIVE})
    with pytest.raises(ValidationError, match="contradicts|canonical"):
        SetupSnapshot(
            instrument=_INSTRUMENT,
            timeframe=Timeframe.ONE_DAY,
            timestamp=_START,
            setups=(forged_hypothesis, downside),
        )


def test_known_active_upside_and_downside_from_real_phase8_outputs() -> None:
    rising = [Decimal(index) for index in range(1, 22)]
    falling = [Decimal(index) for index in range(31, 9, -1)]
    engine = DeterministicSetupCompositionEngine()
    upside = engine.analyze(_evidence_history(rising))[-1]
    downside = engine.analyze(_evidence_history(falling))[-1]
    assert upside.setups[0].status is SetupStatus.ACTIVE
    assert downside.setups[1].status is SetupStatus.ACTIVE
    assert tuple(reference.key for reference in upside.setups[0].evidence_references) == (
        MarketEvidenceKey.PRICE_ABOVE_SMA,
        MarketEvidenceKey.CLOSE_BREAKOUT_ABOVE_PRIOR_HIGH,
    )
    for history, output in (
        (_evidence_history(rising), upside),
        (_evidence_history(falling), downside),
    ):
        source = {item.key: item.status for item in history.snapshots[-1].evidence}
        for setup in output.setups:
            for reference in setup.evidence_references:
                assert reference.status is source[reference.key]
                assert reference.timestamp == history.snapshots[-1].timestamp


def test_no_lookahead_prefix_statelessness_and_no_mutation() -> None:
    common = [Decimal(index) for index in range(1, 26)]
    history_a = _evidence_history(common + [Decimal(10000)])
    history_b = _evidence_history(common + [Decimal("0.1")])
    engine = DeterministicSetupCompositionEngine()
    original = history_a.model_copy(deep=True)
    original_json = tuple(snapshot.model_dump_json() for snapshot in history_a.snapshots)
    result_a = engine.analyze(history_a)
    result_b = engine.analyze(history_b)
    assert result_a[:25] == result_b[:25]
    assert tuple(item.model_dump_json() for item in result_a[:25]) == tuple(
        item.model_dump_json() for item in result_b[:25]
    )
    for length in (1, 14, 15, 20, 21, 25):
        prefix = AlignedEvidenceHistory(snapshots=history_a.snapshots[:length])
        assert engine.analyze(prefix) == result_a[:length]
    engine.analyze(history_b)
    assert engine.analyze(history_a) == result_a
    assert DeterministicSetupCompositionEngine().analyze(history_a) == result_a
    assert history_a == original
    assert tuple(snapshot.model_dump_json() for snapshot in history_a.snapshots) == original_json
    alternate_prefix = _evidence_history(
        [Decimal(1000 - index) for index in range(24)]
    )
    same_current = AlignedEvidenceHistory(
        snapshots=alternate_prefix.snapshots + (history_a.snapshots[24],)
    )
    assert engine.analyze(same_current)[24] == result_a[24]


def test_models_json_extra_fields_and_nested_immutability() -> None:
    snapshot = DeterministicSetupCompositionEngine().analyze(
        _evidence_history([Decimal(1)])
    )[0]
    serialized = snapshot.model_dump_json()
    assert serialized == snapshot.model_dump_json()
    assert SetupSnapshot.model_validate_json(serialized) == snapshot
    with pytest.raises(ValidationError):
        snapshot.setups = ()
    with pytest.raises(ValidationError):
        snapshot.setups[0].status = SetupStatus.ACTIVE
    with pytest.raises(ValidationError):
        SetupEvidenceReference.model_validate(
            {
                "timestamp": _START,
                "key": MarketEvidenceKey.PRICE_ABOVE_SMA,
                "status": MarketEvidenceStatus.ACTIVE,
                "unexpected": True,
            }
        )
    payload = snapshot.model_dump(mode="json")
    for setups in (
        payload["setups"][:1],
        list(reversed(payload["setups"])),
        payload["setups"] + payload["setups"][:1],
    ):
        corrupted = dict(payload)
        corrupted["setups"] = setups
        with pytest.raises(ValidationError):
            SetupSnapshot.model_validate_json(json.dumps(corrupted))


def test_no_forbidden_architecture_or_hidden_strategy_semantics() -> None:
    source = inspect.getsource(setup_engine_module) + inspect.getsource(setup_models_module)
    forbidden = (
        "sqlite", "app.data", "replay", "provider", "app.technical", "app.desks",
        "app.risk", "app.llm", "httpx", "requests", "socket", "datetime.now",
        "time.time", "random", "secrets", "app.orchestration", "Angelo", "direction",
        "confidence", "score", "rank", "signal", "recommendation", "position_size",
        "stop_loss",
    )
    assert not any(term in source for term in forbidden)
