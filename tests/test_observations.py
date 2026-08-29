"""Adversarial tests for observation-time market-data ingestion."""

import socket
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

import app.data.observations as observations_module
from app.core.schemas import AssetClass, Instrument, MarketBar, MarketSnapshot, Timeframe
from app.data.observations import FakeMarketDataIngestion, ObservedMarketData, SourceIdentity

EVENT_TIME = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)


def instrument(exchange: str = "NASDAQ") -> Instrument:
    return Instrument(symbol="NVDA", asset_class=AssetClass.EQUITY, exchange=exchange)


def bar(
    *,
    timestamp: datetime = EVENT_TIME,
    exchange: str = "NASDAQ",
    close: str = "123.4567890123456789",
) -> MarketBar:
    price = Decimal(close)
    return MarketBar(
        instrument=instrument(exchange),
        timeframe=Timeframe.ONE_MINUTE,
        timestamp=timestamp,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Decimal("1000.0000000000000001"),
    )


def snapshot(*, timestamp: datetime = EVENT_TIME) -> MarketSnapshot:
    return MarketSnapshot(
        instrument=instrument(),
        timestamp=timestamp,
        last_price=Decimal("123.4567890123456789"),
        day_volume=Decimal("987654321.0000000001"),
    )


def observation(**overrides: object) -> ObservedMarketData:
    values: dict[str, object] = {
        "payload": bar(),
        "observed_at": EVENT_TIME + timedelta(minutes=5),
        "source": SourceIdentity(name=" test-feed "),
        "source_record_id": " record-1 ",
    }
    values.update(overrides)
    return ObservedMarketData.model_validate(values)


def test_aware_observed_at_is_accepted_and_strings_are_trimmed() -> None:
    value = observation()
    assert value.observed_at == EVENT_TIME + timedelta(minutes=5)
    assert value.source.name == "test-feed"
    assert value.source_record_id == "record-1"


def test_naive_observed_at_is_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        observation(observed_at=datetime(2026, 8, 29, 10, 5))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-08-29T12:05:00+02:00", EVENT_TIME + timedelta(minutes=5)),
        ("2026-08-29T05:05:00-05:00", EVENT_TIME + timedelta(minutes=5)),
    ],
)
def test_observed_at_offsets_normalize_to_utc(value: str, expected: datetime) -> None:
    result = observation(observed_at=value)
    assert result.observed_at == expected
    assert result.observed_at.tzinfo is UTC


@pytest.mark.parametrize("payload", [bar(), snapshot()])
def test_market_payload_json_round_trip(payload: MarketBar | MarketSnapshot) -> None:
    original = observation(payload=payload)
    restored = ObservedMarketData.model_validate_json(original.model_dump_json())
    assert restored == original
    assert type(restored.payload) is type(payload)
    assert restored.payload.instrument == payload.instrument


def test_decimal_precision_and_timeframe_survive_round_trip() -> None:
    restored = ObservedMarketData.model_validate_json(observation(payload=bar()).model_dump_json())
    assert isinstance(restored.payload, MarketBar)
    assert restored.payload.close == Decimal("123.4567890123456789")
    assert restored.payload.volume == Decimal("1000.0000000000000001")
    assert restored.payload.timeframe is Timeframe.ONE_MINUTE


def test_observation_and_nested_payload_are_immutable() -> None:
    value = observation()
    with pytest.raises(ValidationError, match="frozen"):
        value.source_record_id = "changed"
    with pytest.raises(ValidationError, match="frozen"):
        value.payload.instrument.symbol = "AAPL"


def test_extra_fields_are_rejected() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        observation(metadata={"token": "forbidden"})


def test_blank_source_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SourceIdentity(name="   ")


def test_source_identity_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SourceIdentity.model_validate({"name": "feed", "api_key": "forbidden"})


def test_blank_source_record_id_is_rejected_when_supplied() -> None:
    with pytest.raises(ValidationError):
        observation(source_record_id="   ")


def test_payload_accepts_only_canonical_market_types() -> None:
    with pytest.raises(ValidationError):
        observation(payload={"headline": "not market data"})


def test_generated_observation_ids_are_independent_uuid4_values() -> None:
    first = observation()
    second = observation()
    assert isinstance(first.observation_id, UUID)
    assert first.observation_id.version == 4
    assert first.observation_id != second.observation_id


def test_supplied_observation_id_round_trips() -> None:
    supplied = uuid4()
    restored = ObservedMarketData.model_validate_json(
        observation(observation_id=supplied).model_dump_json()
    )
    assert restored.observation_id == supplied


def test_supplied_non_uuid4_observation_id_is_rejected() -> None:
    version_one = UUID("00000000-0000-1000-8000-000000000001")

    with pytest.raises(ValidationError, match="UUID version 4"):
        observation(observation_id=version_one)


def test_source_identity_is_immutable() -> None:
    source = SourceIdentity(name="test-feed")

    with pytest.raises(ValidationError, match="frozen"):
        source.name = "changed-feed"


def test_observation_at_or_before_clock_is_available() -> None:
    value = observation(observed_at=EVENT_TIME + timedelta(minutes=5))
    assert value.is_available_at(EVENT_TIME + timedelta(minutes=5))
    assert value.is_available_at(EVENT_TIME + timedelta(minutes=6))


def test_observation_after_clock_is_unavailable() -> None:
    assert not observation().is_available_at(EVENT_TIME + timedelta(minutes=4))


def test_evaluation_clock_offset_normalizes_to_utc() -> None:
    assert observation().is_available_at(datetime.fromisoformat("2026-08-29T12:05:00+02:00"))


def test_naive_evaluation_clock_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone"):
        observation().is_available_at(datetime(2026, 8, 29, 10, 5))


def test_event_time_before_observed_time_is_preserved() -> None:
    value = observation(payload=bar(timestamp=EVENT_TIME))
    assert value.event_time == EVENT_TIME
    assert value.observed_at == EVENT_TIME + timedelta(minutes=5)


def test_event_time_after_observed_time_remains_representable() -> None:
    value = observation(payload=bar(timestamp=EVENT_TIME + timedelta(minutes=10)))
    assert value.event_time > value.observed_at


def test_two_providers_can_observe_the_same_event_independently() -> None:
    payload = bar()
    first = observation(payload=payload, source=SourceIdentity(name="provider-a"))
    second = observation(payload=payload, source=SourceIdentity(name="provider-b"))
    assert first.payload == second.payload
    assert first.source != second.source
    assert first.observation_id != second.observation_id


def test_same_provider_can_preserve_repeated_observations() -> None:
    payload = bar()
    first = observation(payload=payload, observed_at=EVENT_TIME + timedelta(minutes=3))
    second = observation(payload=payload, observed_at=EVENT_TIME + timedelta(minutes=4))
    assert FakeMarketDataIngestion([first, second]).available_at(
        EVENT_TIME + timedelta(minutes=5)
    ) == [first, second]


def test_repeated_observations_may_share_source_record_id() -> None:
    first = observation(source_record_id="shared-record")
    second = observation(source_record_id="shared-record")
    available = FakeMarketDataIngestion([first, second]).available_at(
        EVENT_TIME + timedelta(minutes=5)
    )

    assert available == sorted([first, second], key=lambda value: value.observation_id)
    assert [value.source_record_id for value in available] == ["shared-record", "shared-record"]


def test_reverse_observation_time_insertion_returns_ascending_order() -> None:
    later = observation(observed_at=EVENT_TIME + timedelta(minutes=5))
    earlier = observation(observed_at=EVENT_TIME + timedelta(minutes=3))

    available = FakeMarketDataIngestion([later, earlier]).available_at(
        EVENT_TIME + timedelta(minutes=6)
    )

    assert available == [earlier, later]


def test_event_and_observation_order_can_differ_without_look_ahead() -> None:
    observation_a = observation(
        payload=bar(timestamp=EVENT_TIME),
        observed_at=EVENT_TIME + timedelta(minutes=5),
    )
    observation_b = observation(
        payload=bar(timestamp=EVENT_TIME + timedelta(minutes=2)),
        observed_at=EVENT_TIME + timedelta(minutes=3),
    )
    ingestion = FakeMarketDataIngestion([observation_a, observation_b])
    assert observation_a.event_time < observation_b.event_time
    assert observation_b.observed_at < observation_a.observed_at
    assert ingestion.available_at(EVENT_TIME + timedelta(minutes=4)) == [observation_b]


def test_same_symbol_different_exchange_identity_is_preserved() -> None:
    nasdaq = observation(payload=bar(exchange="NASDAQ"))
    other = observation(payload=bar(exchange="OTHER"))
    assert nasdaq.payload.instrument.symbol == other.payload.instrument.symbol
    assert nasdaq.payload.instrument.exchange == "NASDAQ"
    assert other.payload.instrument.exchange == "OTHER"
    assert nasdaq.payload.instrument != other.payload.instrument


def test_fake_ingestion_order_is_deterministic_with_uuid_tie_break() -> None:
    early_id = UUID("00000000-0000-4000-8000-000000000001")
    late_id = UUID("00000000-0000-4000-8000-000000000002")
    later = observation(observation_id=late_id)
    earlier = observation(observation_id=early_id)
    ingestion = FakeMarketDataIngestion([later, earlier])
    first = ingestion.available_at(EVENT_TIME + timedelta(minutes=5))
    assert first == [earlier, later]
    assert ingestion.available_at(EVENT_TIME + timedelta(minutes=5)) == first


def test_fake_ingestion_makes_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    assert FakeMarketDataIngestion([observation()]).available_at(
        EVENT_TIME + timedelta(minutes=5)
    )


def test_fake_ingestion_has_no_system_clock_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ClockTrap:
        @classmethod
        def now(cls, *args: object, **kwargs: object) -> datetime:
            raise AssertionError("system clock accessed")

    monkeypatch.setattr(observations_module, "datetime", ClockTrap)
    value = observation()

    assert FakeMarketDataIngestion([value]).available_at(
        EVENT_TIME + timedelta(minutes=5)
    ) == [value]


def test_observation_history_is_not_destructively_overwritten() -> None:
    first = observation(observed_at=EVENT_TIME + timedelta(minutes=1))
    second = observation(observed_at=EVENT_TIME + timedelta(minutes=2))
    correction = observation(observed_at=EVENT_TIME + timedelta(minutes=3))

    available = FakeMarketDataIngestion([first, second, correction]).available_at(
        EVENT_TIME + timedelta(minutes=4)
    )

    assert available == [first, second, correction]


def test_domain_repr_contains_only_explicit_non_secret_fields() -> None:
    rendered = repr(observation())
    assert "api_key" not in rendered
    assert "authorization" not in rendered
    assert "token" not in rendered
