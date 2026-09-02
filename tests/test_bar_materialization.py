"""Adversarial Phase 13 deterministic PIT bar-materialization tests."""

import inspect
import json
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest
from pydantic import ValidationError

import app.materialization.engine as engine_module
import app.materialization.models as models_module
from app.core.schemas import AssetClass, Instrument, MarketBar, MarketSnapshot, Timeframe
from app.data.observations import ObservedMarketData, SourceIdentity
from app.materialization import (
    BarMaterializationInvalidInputError,
    BarSeriesRequest,
    DeterministicBarMaterializationEngine,
    MaterializedBar,
    MaterializedBarHistory,
)

_EVENT = datetime(2025, 1, 2, tzinfo=UTC)
_KNOWN = datetime(2025, 1, 3, tzinfo=UTC)
_INSTRUMENT = Instrument(
    symbol="AAPL", asset_class=AssetClass.EQUITY, exchange="XNAS", currency="USD"
)
_SOURCE = SourceIdentity(name="provider-a")


def _bar(
    offset: int = 0,
    *,
    close: str = "10",
    instrument: Instrument = _INSTRUMENT,
    timeframe: Timeframe = Timeframe.ONE_DAY,
) -> MarketBar:
    return MarketBar(
        instrument=instrument,
        timeframe=timeframe,
        timestamp=_EVENT + timedelta(days=offset),
        open="10",
        high=max("10", close),
        low=min("10", close),
        close=close,
        volume="100",
    )


def _observation(
    payload: MarketBar | MarketSnapshot,
    *,
    minute: int,
    integer: int,
    source: SourceIdentity = _SOURCE,
    source_record_id: str | None = None,
) -> ObservedMarketData:
    return ObservedMarketData(
        observation_id=UUID(f"00000000-0000-4000-8000-{integer:012d}"),
        payload=payload,
        observed_at=_KNOWN + timedelta(minutes=minute),
        source=source,
        source_record_id=source_record_id,
    )


def _request(**changes: object) -> BarSeriesRequest:
    values: dict[str, object] = {
        "instrument": _INSTRUMENT,
        "timeframe": Timeframe.ONE_DAY,
        "source": _SOURCE,
        "as_of": _KNOWN + timedelta(days=1),
        "start": None,
        "end": None,
    }
    values.update(changes)
    return BarSeriesRequest.model_validate(values)


def _materialize(
    observations: tuple[ObservedMarketData, ...], request: BarSeriesRequest | None = None
) -> MaterializedBarHistory:
    return DeterministicBarMaterializationEngine().materialize(
        observations, request or _request()
    )


def test_empty_history_is_explicit_and_valid() -> None:
    result = _materialize(())
    assert result.bars == ()
    assert result.inspected_observation_count == 0
    assert result.eligible_bar_candidate_count == 0


def test_latest_revision_known_at_cutoff_wins_with_exact_provenance() -> None:
    original = _observation(_bar(close="10"), minute=0, integer=1, source_record_id="old")
    correction = _observation(_bar(close="12"), minute=1, integer=2, source_record_id="new")
    result = _materialize((original, correction))
    assert len(result.bars) == 1
    selected = result.bars[0]
    assert selected.bar.close == 12
    assert selected.observation_id == correction.observation_id
    assert selected.observed_at == correction.observed_at
    assert selected.source == correction.source
    assert selected.source_record_id == "new"
    assert result.eligible_bar_candidate_count == 2


def test_equal_receipt_time_uses_observation_id_tie_break() -> None:
    first = _observation(_bar(close="10"), minute=0, integer=1)
    second = _observation(_bar(close="11"), minute=0, integer=2)
    assert _materialize((first, second)).bars[0].bar.close == 11


def test_future_known_observation_is_rejected_even_for_old_event() -> None:
    future = _observation(_bar(), minute=1, integer=1)
    with pytest.raises(BarMaterializationInvalidInputError, match="not known"):
        _materialize((future,), _request(as_of=_KNOWN))


def test_knowledge_order_is_required_and_never_repaired() -> None:
    first = _observation(_bar(1), minute=0, integer=1)
    second = _observation(_bar(0), minute=1, integer=2)
    with pytest.raises(BarMaterializationInvalidInputError, match="knowledge order"):
        _materialize((second, first))
    result = _materialize((first, second))
    assert tuple(item.bar.timestamp for item in result.bars) == (_EVENT, _EVENT + timedelta(days=1))


def test_duplicate_observation_id_is_rejected_even_at_later_receipt_time() -> None:
    first = _observation(_bar(), minute=0, integer=1)
    duplicate = first.model_copy(update={"observed_at": _KNOWN + timedelta(minutes=1)})
    with pytest.raises(BarMaterializationInvalidInputError, match="duplicate observation ID"):
        _materialize((first, duplicate))


def test_source_is_explicit_and_cross_provider_facts_never_blend() -> None:
    provider_b = SourceIdentity(name="provider-b")
    from_a = _observation(_bar(close="10"), minute=0, integer=1)
    from_b = _observation(_bar(close="99"), minute=1, integer=2, source=provider_b)
    result = _materialize((from_a, from_b))
    assert tuple(item.bar.close for item in result.bars) == (10,)
    assert result.inspected_observation_count == 2
    assert result.eligible_bar_candidate_count == 1
    assert _materialize((from_a, from_b), _request(source=provider_b)).bars[0].bar.close == 99


def test_snapshots_and_nonmatching_identity_timeframe_and_range_are_excluded() -> None:
    other = Instrument(
        symbol="AAPL", asset_class=AssetClass.EQUITY, exchange="XNYS", currency="USD"
    )
    snapshot = MarketSnapshot(instrument=_INSTRUMENT, timestamp=_EVENT, last_price="10")
    observations = (
        _observation(snapshot, minute=0, integer=1),
        _observation(_bar(-1), minute=1, integer=2),
        _observation(_bar(0, instrument=other), minute=2, integer=3),
        _observation(_bar(0, timeframe=Timeframe.ONE_HOUR), minute=3, integer=4),
        _observation(_bar(0), minute=4, integer=5),
        _observation(_bar(1), minute=5, integer=6),
    )
    result = _materialize(
        observations,
        _request(start=_EVENT, end=_EVENT + timedelta(days=1)),
    )
    assert tuple(item.bar.timestamp for item in result.bars) == (_EVENT,)
    assert result.inspected_observation_count == 6
    assert result.eligible_bar_candidate_count == 1


def test_request_range_is_half_open_and_must_be_coherent() -> None:
    assert _request(start=_EVENT, end=_EVENT + timedelta(days=1))
    for end in (_EVENT, _EVENT - timedelta(microseconds=1)):
        with pytest.raises(ValidationError, match="start must be earlier"):
            _request(start=_EVENT, end=end)


def test_input_requires_sequence_and_actual_canonical_observations() -> None:
    with pytest.raises(BarMaterializationInvalidInputError, match="sequence"):
        DeterministicBarMaterializationEngine().materialize(
            cast(list[ObservedMarketData], iter(())), _request()
        )
    with pytest.raises(BarMaterializationInvalidInputError, match="only"):
        DeterministicBarMaterializationEngine().materialize(
            cast(tuple[ObservedMarketData, ...], (object(),)), _request()
        )
    with pytest.raises(BarMaterializationInvalidInputError, match="request"):
        DeterministicBarMaterializationEngine().materialize(
            (), cast(BarSeriesRequest, object())
        )


def test_models_reject_mutation_extra_fields_and_contradictory_history() -> None:
    observation = _observation(_bar(), minute=0, integer=1)
    item = MaterializedBar.from_observation(observation)
    result = _materialize((observation,))
    with pytest.raises(ValidationError, match="frozen"):
        result.inspected_observation_count = 2
    with pytest.raises(ValidationError, match="extra"):
        BarSeriesRequest.model_validate({**_request().model_dump(), "unexpected": True})
    with pytest.raises(ValidationError, match="tuple"):
        MaterializedBarHistory(
            request=_request(),
            bars=[item],
            inspected_observation_count=1,
            eligible_bar_candidate_count=1,
        )
    with pytest.raises(ValidationError, match="candidate count"):
        MaterializedBarHistory(
            request=_request(),
            bars=(item,),
            inspected_observation_count=1,
            eligible_bar_candidate_count=0,
        )
    for invalid_count in (True, "1", -1):
        with pytest.raises(ValidationError, match="nonnegative integers"):
            MaterializedBarHistory.model_validate(
                {
                    **result.model_dump(mode="python"),
                    "inspected_observation_count": invalid_count,
                }
            )


def test_json_round_trip_and_repeated_execution_are_deterministic() -> None:
    observations = (
        _observation(_bar(1), minute=0, integer=1),
        _observation(_bar(0), minute=1, integer=2),
    )
    first = _materialize(observations)
    second = _materialize(observations)
    assert first == second
    assert first.model_dump_json() == second.model_dump_json()
    assert MaterializedBarHistory.model_validate_json(first.model_dump_json()) == first
    timestamp = json.loads(first.model_dump_json())["bars"][0]["bar"]["timestamp"]
    assert timestamp.startswith("2025-01-02")


def test_future_extension_does_not_change_earlier_cutoff_result() -> None:
    original = _observation(_bar(close="10"), minute=0, integer=1)
    future = _observation(_bar(close="20"), minute=2, integer=2)
    request = _request(as_of=_KNOWN + timedelta(minutes=1))
    baseline = _materialize((original,), request)
    with pytest.raises(BarMaterializationInvalidInputError, match="not known"):
        _materialize((original, future), request)
    assert _materialize((original,), request) == baseline


def test_phase13_modules_have_no_forbidden_dependencies_or_side_effects() -> None:
    source = inspect.getsource(engine_module) + inspect.getsource(models_module)
    forbidden = (
        "sqlite",
        "app.data.replay",
        "app.data.observation_store",
        "provider",
        "httpx",
        "requests",
        "socket",
        "datetime.now",
        "time.time",
        "random",
        "secrets",
        "app.technical",
        "app.evidence",
        "app.setups",
        "app.scanner",
        "app.risk",
        "app.desks",
        "app.llm",
        "app.orchestration",
        "Angelo",
    )
    assert not any(item in source for item in forbidden)
