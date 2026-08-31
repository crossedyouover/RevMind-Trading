"""Adversarial tests for deterministic Phase 10 universe scanning."""

import inspect
import json
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from pydantic import ValidationError

import app.scanner.engine as scanner_engine_module
import app.scanner.models as scanner_models_module
from app.core.schemas import AssetClass, Instrument, Timeframe
from app.evidence.models import MarketEvidenceKey, MarketEvidenceStatus
from app.scanner import (
    DeterministicScannerEngine,
    InstrumentScanResult,
    ScannerInvalidInputError,
    ScannerSnapshot,
    SetupUniverseSnapshot,
)
from app.setups import (
    SETUP_KEY_ORDER,
    SetupEvidenceReference,
    SetupHypothesis,
    SetupKey,
    SetupSnapshot,
    SetupStatus,
)

_TIME = datetime(2025, 1, 2, tzinfo=UTC)


def _hypothesis(key: SetupKey, status: SetupStatus, timestamp: datetime) -> SetupHypothesis:
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
    evidence_status = {
        SetupStatus.ACTIVE: MarketEvidenceStatus.ACTIVE,
        SetupStatus.INACTIVE: MarketEvidenceStatus.INACTIVE,
        SetupStatus.WARMING_UP: MarketEvidenceStatus.WARMING_UP,
        SetupStatus.UNDEFINED: MarketEvidenceStatus.UNDEFINED,
    }[status]
    return SetupHypothesis(
        key=key,
        status=status,
        evidence_references=tuple(
            SetupEvidenceReference(timestamp=timestamp, key=item, status=evidence_status)
            for item in keys
        ),
    )


def _snapshot(
    symbol: str,
    *,
    asset_class: AssetClass = AssetClass.EQUITY,
    exchange: str | None = "XNAS",
    currency: str | None = "USD",
    timeframe: Timeframe = Timeframe.ONE_DAY,
    timestamp: datetime = _TIME,
    statuses: tuple[SetupStatus, SetupStatus] = (
        SetupStatus.INACTIVE,
        SetupStatus.INACTIVE,
    ),
) -> SetupSnapshot:
    return SetupSnapshot(
        instrument=Instrument(
            symbol=symbol,
            asset_class=asset_class,
            exchange=exchange,
            currency=currency,
        ),
        timeframe=timeframe,
        timestamp=timestamp,
        setups=tuple(
            _hypothesis(key, status, timestamp)
            for key, status in zip(SETUP_KEY_ORDER, statuses, strict=True)
        ),
    )


def _universe(*snapshots: SetupSnapshot) -> SetupUniverseSnapshot:
    return SetupUniverseSnapshot(
        scan_as_of=_TIME,
        timeframe=Timeframe.ONE_DAY,
        setup_snapshots=snapshots,
    )


def test_empty_single_and_multiple_universes() -> None:
    assert _universe().setup_snapshots == ()
    one = _universe(_snapshot("AAPL"))
    many = _universe(_snapshot("AAPL"), _snapshot("MSFT"))
    assert len(one.setup_snapshots) == 1
    assert len(many.setup_snapshots) == 2
    output = DeterministicScannerEngine().scan(_universe())
    assert output.results == ()
    assert output.scan_as_of == _TIME


def test_universe_requires_tuple_and_actual_snapshots() -> None:
    snapshot = _snapshot("AAPL")
    with pytest.raises(ValidationError, match="tuple"):
        SetupUniverseSnapshot.model_validate(
            {
                "scan_as_of": _TIME,
                "timeframe": Timeframe.ONE_DAY,
                "setup_snapshots": [snapshot],
            }
        )
    with pytest.raises(ValidationError, match="actual"):
        SetupUniverseSnapshot(
            scan_as_of=_TIME,
            timeframe=Timeframe.ONE_DAY,
            setup_snapshots=(cast(SetupSnapshot, object()),),
        )


def test_complete_identity_duplicates_rejected_even_at_different_times() -> None:
    first = _snapshot("AAPL", timestamp=_TIME - timedelta(hours=2))
    same = _snapshot("AAPL", timestamp=_TIME - timedelta(hours=1))
    for duplicate in (first, same):
        with pytest.raises(ValidationError, match="duplicate"):
            _universe(first, duplicate)


def test_same_symbol_distinct_complete_identities_are_allowed_in_order() -> None:
    snapshots = (
        _snapshot("SAME", asset_class=AssetClass.CRYPTO, exchange="A", currency="USD"),
        _snapshot("SAME", asset_class=AssetClass.EQUITY, exchange="A", currency="EUR"),
        _snapshot("SAME", asset_class=AssetClass.EQUITY, exchange="A", currency="USD"),
        _snapshot("SAME", asset_class=AssetClass.EQUITY, exchange="B", currency="USD"),
    )
    assert _universe(*snapshots).setup_snapshots == snapshots


def test_timeframe_and_temporal_boundary() -> None:
    _universe(_snapshot("AAPL", timestamp=_TIME))
    _universe(_snapshot("AAPL", timestamp=_TIME - timedelta(days=1)))
    with pytest.raises(ValidationError, match="timeframe"):
        _universe(_snapshot("AAPL", timeframe=Timeframe.ONE_HOUR))
    with pytest.raises(ValidationError, match="scan_as_of"):
        _universe(_snapshot("AAPL", timestamp=_TIME + timedelta(microseconds=1)))


def test_canonical_order_is_required_and_never_repaired() -> None:
    ordered = (_snapshot("AAPL"), _snapshot("MSFT"))
    assert _universe(*ordered).setup_snapshots == ordered
    with pytest.raises(ValidationError, match="canonical"):
        _universe(*reversed(ordered))


@pytest.mark.parametrize(
    ("statuses", "expected"),
    (
        ((SetupStatus.INACTIVE, SetupStatus.INACTIVE), ()),
        ((SetupStatus.ACTIVE, SetupStatus.INACTIVE), (SETUP_KEY_ORDER[0],)),
        ((SetupStatus.INACTIVE, SetupStatus.ACTIVE), (SETUP_KEY_ORDER[1],)),
        ((SetupStatus.ACTIVE, SetupStatus.ACTIVE), SETUP_KEY_ORDER),
        ((SetupStatus.WARMING_UP, SetupStatus.UNDEFINED), ()),
    ),
)
def test_active_projection_is_exact_and_complete_state_is_retained(
    statuses: tuple[SetupStatus, SetupStatus], expected: tuple[SetupKey, ...]
) -> None:
    source = _snapshot("AAPL", statuses=statuses)
    result = DeterministicScannerEngine().scan(_universe(source)).results[0]
    assert result.setup_snapshot == source
    assert result.active_setup_keys == expected
    assert len(result.setup_snapshot.setups) == len(SETUP_KEY_ORDER)


def test_result_rejects_every_incorrect_projection() -> None:
    source = _snapshot(
        "AAPL", statuses=(SetupStatus.ACTIVE, SetupStatus.INACTIVE)
    )
    invalid = (
        (),
        (SetupKey.DOWNSIDE_BREAKDOWN_BELOW_SMA,),
        SETUP_KEY_ORDER,
        (SETUP_KEY_ORDER[0], SETUP_KEY_ORDER[0]),
        tuple(reversed(SETUP_KEY_ORDER)),
    )
    for keys in invalid:
        with pytest.raises(ValidationError, match="projection"):
            InstrumentScanResult(setup_snapshot=source, active_setup_keys=keys)


def test_result_rejects_warming_undefined_and_inactive_as_active() -> None:
    for status in (
        SetupStatus.WARMING_UP,
        SetupStatus.UNDEFINED,
        SetupStatus.INACTIVE,
    ):
        source = _snapshot("AAPL", statuses=(status, SetupStatus.INACTIVE))
        with pytest.raises(ValidationError):
            InstrumentScanResult(
                setup_snapshot=source, active_setup_keys=(SETUP_KEY_ORDER[0],)
            )


def test_scanner_snapshot_revalidates_all_universe_invariants() -> None:
    engine = DeterministicScannerEngine()
    a = engine.scan(_universe(_snapshot("AAPL"))).results[0]
    m = engine.scan(_universe(_snapshot("MSFT"))).results[0]
    assert ScannerSnapshot(
        scan_as_of=_TIME, timeframe=Timeframe.ONE_DAY, results=()
    ).results == ()
    for results in ((a, a), (m, a)):
        with pytest.raises(ValidationError):
            ScannerSnapshot(
                scan_as_of=_TIME, timeframe=Timeframe.ONE_DAY, results=results
            )
    hourly = m.model_copy(
        update={
            "setup_snapshot": m.setup_snapshot.model_copy(
                update={"timeframe": Timeframe.ONE_HOUR}
            )
        }
    )
    future = m.model_copy(
        update={
            "setup_snapshot": m.setup_snapshot.model_copy(
                update={"timestamp": _TIME + timedelta(days=1)}
            )
        }
    )
    for result in (hourly, future):
        with pytest.raises(ValidationError):
            ScannerSnapshot(
                scan_as_of=_TIME, timeframe=Timeframe.ONE_DAY, results=(result,)
            )


def test_model_copy_attacks_fail_closed_at_models_and_engine() -> None:
    source = _snapshot("AAPL", statuses=(SetupStatus.ACTIVE, SetupStatus.INACTIVE))
    malformed_reference = source.setups[0].evidence_references[0].model_copy(
        update={"status": "FORGED"}
    )
    malformed_hypothesis = source.setups[0].model_copy(
        update={
            "evidence_references": (malformed_reference,)
            + source.setups[0].evidence_references[1:]
        }
    )
    malformed_snapshot = source.model_copy(
        update={"setups": (malformed_hypothesis, source.setups[1])}
    )
    malformed_universe = _universe(source).model_copy(
        update={"setup_snapshots": (malformed_snapshot,)}
    )
    with pytest.raises(ScannerInvalidInputError):
        DeterministicScannerEngine().scan(malformed_universe)
    copied_result = InstrumentScanResult(
        setup_snapshot=source, active_setup_keys=(SETUP_KEY_ORDER[0],)
    ).model_copy(update={"active_setup_keys": ()})
    with pytest.raises(ValidationError):
        ScannerSnapshot(
            scan_as_of=_TIME, timeframe=Timeframe.ONE_DAY, results=(copied_result,)
        )
    copied_output = DeterministicScannerEngine().scan(_universe(source)).model_copy(
        update={"results": (copied_result,)}
    )
    with pytest.raises(ValidationError):
        ScannerSnapshot.model_validate(
            copied_output.model_dump(mode="python", round_trip=True)
        )


def test_engine_rejects_wrong_input_and_forged_universe_projection() -> None:
    engine = DeterministicScannerEngine()
    with pytest.raises(ScannerInvalidInputError):
        engine.scan(cast(SetupUniverseSnapshot, object()))
    source = _snapshot("AAPL")
    forged = _universe(source).model_copy(
        update={"setup_snapshots": (source, source)}
    )
    with pytest.raises(ScannerInvalidInputError):
        engine.scan(forged)


def test_source_fidelity_determinism_statelessness_and_input_immutability() -> None:
    a = _universe(
        _snapshot("AAPL", statuses=(SetupStatus.ACTIVE, SetupStatus.INACTIVE)),
        _snapshot("MSFT", statuses=(SetupStatus.INACTIVE, SetupStatus.ACTIVE)),
    )
    b = _universe(_snapshot("TSLA"))
    before = a.model_dump_json()
    engine = DeterministicScannerEngine()
    first = engine.scan(a)
    assert engine.scan(a) == first
    assert engine.scan(a).model_dump_json() == first.model_dump_json()
    engine.scan(b)
    assert engine.scan(a) == first
    assert DeterministicScannerEngine().scan(a) == first
    assert a.model_dump_json() == before
    for result in first.results:
        expected = tuple(
            item.key for item in result.setup_snapshot.setups if item.status is SetupStatus.ACTIVE
        )
        assert result.active_setup_keys == expected


def test_json_round_trip_extra_fields_and_corruption() -> None:
    output = DeterministicScannerEngine().scan(
        _universe(_snapshot("AAPL", statuses=(SetupStatus.ACTIVE, SetupStatus.INACTIVE)))
    )
    serialized = output.model_dump_json()
    assert ScannerSnapshot.model_validate_json(serialized) == output
    assert ScannerSnapshot.model_validate_json(serialized).model_dump_json() == serialized
    payload = output.model_dump(mode="json")
    corruptions: list[dict[str, object]] = []
    extra = dict(payload)
    extra["unexpected"] = True
    corruptions.append(extra)
    wrong_keys = json.loads(json.dumps(payload))
    wrong_keys["results"][0]["active_setup_keys"] = []
    corruptions.append(wrong_keys)
    future = json.loads(json.dumps(payload))
    future["results"][0]["setup_snapshot"]["timestamp"] = "2026-01-01T00:00:00Z"
    corruptions.append(future)
    for corrupted in corruptions:
        with pytest.raises(ValidationError):
            ScannerSnapshot.model_validate_json(json.dumps(corrupted))


def test_models_are_deeply_immutable() -> None:
    output = DeterministicScannerEngine().scan(_universe(_snapshot("AAPL")))
    with pytest.raises(ValidationError):
        output.results = ()
    with pytest.raises(ValidationError):
        output.results[0].active_setup_keys = ()
    with pytest.raises(ValidationError):
        output.results[0].setup_snapshot.setups = ()


def test_scan_boundary_is_not_historical_eligibility_or_retrieval() -> None:
    source = _snapshot("AAPL", timestamp=_TIME - timedelta(days=1))
    output = DeterministicScannerEngine().scan(_universe(source))
    assert output.results[0].setup_snapshot.timestamp < output.scan_as_of
    assert output.results[0].setup_snapshot == source


def test_no_prohibited_architecture_or_hidden_strategy_surface() -> None:
    source = inspect.getsource(scanner_engine_module) + inspect.getsource(scanner_models_module)
    prohibited_imports = (
        "app.data", "app.technical", "app.evidence.engine", "app.setups.engine",
        "app.desks", "sqlite", "requests", "httpx", "socket", "datetime.now",
        "time.time", "random", "secrets", "openai", "anthropic",
    )
    assert not any(term in source.lower() for term in prohibited_imports)
    public_fields = set(InstrumentScanResult.model_fields) | set(ScannerSnapshot.model_fields)
    forbidden_fields = {
        "score", "rank", "confidence", "probability", "strength", "recommendation",
        "signal", "direction", "buy", "sell", "entry", "exit", "stop", "position_size",
    }
    assert public_fields.isdisjoint(forbidden_fields)
