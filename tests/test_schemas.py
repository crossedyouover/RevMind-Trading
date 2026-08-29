"""Tests for canonical domain contracts."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel, ValidationError

from app.core.schemas import (
    AssetClass,
    Catalyst,
    CatalystSourceType,
    DeskDecision,
    DeskDecisionStatus,
    InsiderActivity,
    Instrument,
    MarketBar,
    MarketRegime,
    MarketRegimeType,
    MarketSnapshot,
    PortfolioPosition,
    RiskDecision,
    RiskDecisionStatus,
    Setup,
    Signal,
    SignalDirection,
    SignalStatus,
)


@pytest.fixture
def instrument() -> Instrument:
    return Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY)


def make_bar(instrument: Instrument, **overrides: object) -> MarketBar:
    values: dict[str, object] = {
        "instrument": instrument,
        "timestamp": "2026-08-29T09:15:00Z",
        "open": "100.10",
        "high": "105.25",
        "low": "99.90",
        "close": "104.80",
        "volume": "1000000",
    }
    values.update(overrides)
    return MarketBar.model_validate(values)


def make_signal(instrument: Instrument, **overrides: object) -> Signal:
    values: dict[str, object] = {
        "instrument": instrument,
        "created_at": "2026-08-29T09:15:00Z",
        "signal_type": "unusual_activity",
        "direction": SignalDirection.LONG,
        "confidence": "0.75",
        "evidence": ["Volume exceeded its reference range"],
        "source_component": "scanner",
        "status": SignalStatus.CANDIDATE,
    }
    values.update(overrides)
    return Signal.model_validate(values)


def test_instrument_normalization() -> None:
    value = Instrument(
        symbol=" aapl ", asset_class=AssetClass.EQUITY, exchange=" nasdaq ", currency=" usd "
    )

    assert value.symbol == "AAPL"
    assert value.exchange == "NASDAQ"
    assert value.currency == "USD"


def test_blank_symbol_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Instrument(symbol="   ", asset_class=AssetClass.EQUITY)


def test_valid_market_bar_preserves_decimals(instrument: Instrument) -> None:
    bar = make_bar(instrument)

    assert bar.high == Decimal("105.25")
    assert isinstance(bar.close, Decimal)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"high": "98"}, "high must be greater"),
        ({"low": "106"}, "high must be greater"),
        ({"high": "103"}, "high must be greater"),
        ({"low": "101"}, "low must be less"),
    ],
)
def test_invalid_market_bar_relationships(
    instrument: Instrument, overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        make_bar(instrument, **overrides)


@pytest.mark.parametrize("field", ["open", "high", "low", "close"])
def test_negative_prices_are_rejected(instrument: Instrument, field: str) -> None:
    with pytest.raises(ValidationError):
        make_bar(instrument, **{field: "-0.01"})


def test_negative_volume_is_rejected(instrument: Instrument) -> None:
    with pytest.raises(ValidationError):
        make_bar(instrument, volume="-1")


def test_timezone_aware_datetime_is_accepted(instrument: Instrument) -> None:
    timestamp = datetime(2026, 8, 29, 9, 15, tzinfo=UTC)

    assert make_bar(instrument, timestamp=timestamp).timestamp == timestamp


def test_utc_z_datetime_string_is_accepted(instrument: Instrument) -> None:
    assert make_bar(instrument, timestamp="2026-08-29T09:15:00Z").timestamp.tzinfo is UTC


def test_offset_datetime_is_normalized_to_utc(instrument: Instrument) -> None:
    bar = make_bar(instrument, timestamp="2026-08-29T11:15:00+02:00")

    assert bar.timestamp == datetime(2026, 8, 29, 9, 15, tzinfo=UTC)
    assert bar.timestamp.tzinfo is UTC


def test_negative_offset_datetime_is_normalized_to_utc(instrument: Instrument) -> None:
    bar = make_bar(instrument, timestamp="2026-08-29T04:15:00-05:00")

    assert bar.timestamp == datetime(2026, 8, 29, 9, 15, tzinfo=UTC)


def test_naive_datetime_is_rejected(instrument: Instrument) -> None:
    with pytest.raises(ValidationError, match="timezone"):
        make_bar(instrument, timestamp="2026-08-29T09:15:00")


def test_naive_datetime_object_is_rejected(instrument: Instrument) -> None:
    with pytest.raises(ValidationError, match="timezone"):
        make_bar(instrument, timestamp=datetime(2026, 8, 29, 9, 15))


def timestamp_model_inputs(
    instrument: Instrument,
) -> list[tuple[type[BaseModel], dict[str, object]]]:
    timestamp = "2026-08-29T09:15:00Z"
    return [
        (MarketBar, {"instrument": instrument, "timestamp": timestamp, "open": 1, "high": 1,
                     "low": 1, "close": 1, "volume": 0}),
        (MarketSnapshot, {"instrument": instrument, "timestamp": timestamp, "last_price": 1}),
        (Signal, {"instrument": instrument, "created_at": timestamp, "signal_type": "type",
                  "direction": "LONG", "confidence": 0, "evidence": [],
                  "source_component": "scanner", "status": "CANDIDATE"}),
        (Setup, {"instrument": instrument, "created_at": timestamp, "setup_type": "type",
                 "direction": "LONG", "confidence": 0, "evidence": []}),
        (Catalyst, {"observed_at": timestamp, "headline": "headline", "source": "source",
                    "source_type": CatalystSourceType.PRIMARY}),
        (InsiderActivity, {"instrument": instrument, "observed_at": timestamp,
                           "insider_name": "name", "transaction_type": "sale", "source": "SEC"}),
        (MarketRegime, {"observed_at": timestamp, "regime": "NEUTRAL", "confidence": 0,
                        "evidence": []}),
        (PortfolioPosition, {"instrument": instrument, "quantity": 0, "average_price": 0,
                             "opened_at": timestamp}),
        (RiskDecision, {"created_at": timestamp, "status": RiskDecisionStatus.REJECTED,
                        "rule_code": "RULE", "reason": "reason"}),
        (DeskDecision, {"created_at": timestamp, "status": DeskDecisionStatus.QUIET,
                        "supporting_signal_ids": []}),
    ]


def test_all_event_timestamp_fields_reject_naive_values(instrument: Instrument) -> None:
    for model_type, values in timestamp_model_inputs(instrument):
        timestamp_field = next(
            name
            for name in ("timestamp", "created_at", "observed_at", "opened_at")
            if name in values
        )
        values[timestamp_field] = "2026-08-29T09:15:00"
        with pytest.raises(ValidationError, match="timezone"):
            model_type.model_validate(values)


@pytest.mark.parametrize("boundary", ["0", "1"])
def test_confidence_boundaries_are_accepted(instrument: Instrument, boundary: str) -> None:
    assert make_signal(instrument, confidence=boundary).confidence == Decimal(boundary)


@pytest.mark.parametrize("invalid", ["-0.0001", "1.0001"])
def test_confidence_outside_range_is_rejected(instrument: Instrument, invalid: str) -> None:
    with pytest.raises(ValidationError):
        make_signal(instrument, confidence=invalid)


def test_unexpected_fields_are_rejected(instrument: Instrument) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Instrument.model_validate(
            {"symbol": "AAPL", "asset_class": AssetClass.EQUITY, "unexpected": True}
        )


def test_uuid_defaults_are_generated(instrument: Instrument) -> None:
    first = make_signal(instrument)
    second = make_signal(instrument)

    assert isinstance(first.signal_id, UUID)
    assert first.signal_id.version == 4
    assert first.signal_id != second.signal_id


def test_supplied_uuid_round_trips(instrument: Instrument) -> None:
    supplied_id = uuid4()
    signal = make_signal(instrument, signal_id=supplied_id)

    restored = Signal.model_validate_json(signal.model_dump_json())

    assert restored.signal_id == supplied_id


def test_json_serialization_round_trip(instrument: Instrument) -> None:
    signal = make_signal(instrument, confidence="0.1234567890123456789")

    serialized = signal.model_dump_json()
    restored = Signal.model_validate_json(serialized)

    assert restored == signal
    assert restored.confidence == Decimal("0.1234567890123456789")
    assert restored.created_at.tzinfo is UTC
    assert restored.instrument == instrument
    assert restored.direction is SignalDirection.LONG
    assert restored.evidence == signal.evidence


@pytest.mark.parametrize("precise", [Decimal("0.1"), Decimal("123456789.123456789")])
def test_decimal_precision_survives_validation_and_json_round_trip(
    instrument: Instrument, precise: Decimal
) -> None:
    snapshot = MarketSnapshot(
        instrument=instrument,
        timestamp=datetime(2026, 8, 29, 9, 15, tzinfo=UTC),
        last_price=precise,
        percent_change=precise,
    )

    restored = MarketSnapshot.model_validate_json(snapshot.model_dump_json())

    assert restored.last_price == precise
    assert restored.percent_change == precise


@pytest.mark.parametrize("non_finite", ["NaN", "Infinity", "-Infinity"])
@pytest.mark.parametrize(
    ("model_type", "values", "field"),
    [
        (MarketSnapshot, {"timestamp": "2026-08-29T09:15:00Z", "last_price": 1}, "last_price"),
        (MarketSnapshot, {"timestamp": "2026-08-29T09:15:00Z", "last_price": 1}, "day_volume"),
        (MarketSnapshot, {"timestamp": "2026-08-29T09:15:00Z", "last_price": 1}, "percent_change"),
        (PortfolioPosition, {"quantity": 1, "average_price": 1}, "quantity"),
        (PortfolioPosition, {"quantity": 1, "average_price": 1}, "average_price"),
        (Signal, {"created_at": "2026-08-29T09:15:00Z", "signal_type": "type",
                  "direction": "LONG", "confidence": 0, "evidence": [],
                  "source_component": "scanner", "status": "CANDIDATE"}, "confidence"),
        (InsiderActivity, {"observed_at": "2026-08-29T09:15:00Z", "insider_name": "name",
                           "transaction_type": "sale", "source": "SEC"}, "shares"),
        (RiskDecision, {"created_at": "2026-08-29T09:15:00Z", "status": "REJECTED",
                        "rule_code": "RULE", "reason": "reason"}, "proposed_risk_amount"),
    ],
)
def test_non_finite_financial_values_are_rejected(
    instrument: Instrument,
    non_finite: str,
    model_type: type[BaseModel],
    values: dict[str, object],
    field: str,
) -> None:
    values = {"instrument": instrument, **values, field: non_finite}
    with pytest.raises(ValidationError):
        model_type.model_validate(values)


@pytest.mark.parametrize("boolean", [True, False])
@pytest.mark.parametrize("field", ["open", "high", "low", "close", "volume"])
def test_market_bar_rejects_boolean_financial_values(
    instrument: Instrument, boolean: bool, field: str
) -> None:
    with pytest.raises(ValidationError):
        make_bar(instrument, **{field: boolean})


@pytest.mark.parametrize("boolean", [True, False])
def test_confidence_rejects_boolean_values(instrument: Instrument, boolean: bool) -> None:
    with pytest.raises(ValidationError):
        make_signal(instrument, confidence=boolean)


def test_negative_short_position_quantity_is_accepted(instrument: Instrument) -> None:
    position = PortfolioPosition.model_validate(
        {
            "instrument": instrument,
            "quantity": Decimal("-12.5"),
            "average_price": Decimal("101.25"),
            "opened_at": "2026-08-29T09:15:00Z",
        }
    )

    assert position.quantity == Decimal("-12.5")


def test_blank_evidence_entry_is_rejected(instrument: Instrument) -> None:
    with pytest.raises(ValidationError):
        make_signal(instrument, evidence=["valid evidence", "   "])


def test_evidence_entries_are_trimmed_without_deduplication(instrument: Instrument) -> None:
    signal = make_signal(instrument, evidence=["  volume spike  ", "volume spike"])

    assert signal.evidence == ["volume spike", "volume spike"]


def test_equal_ohlc_prices_are_valid(instrument: Instrument) -> None:
    bar = make_bar(instrument, open="100", high="100", low="100", close="100")

    assert bar.open == bar.high == bar.low == bar.close == Decimal("100")


def test_canonical_models_and_nested_models_are_immutable(instrument: Instrument) -> None:
    signal = make_signal(instrument)

    with pytest.raises(ValidationError, match="frozen"):
        signal.status = SignalStatus.APPROVED
    with pytest.raises(ValidationError, match="frozen"):
        signal.instrument.symbol = "MSFT"


def test_market_snapshot_rejects_negative_day_volume(instrument: Instrument) -> None:
    with pytest.raises(ValidationError):
        MarketSnapshot.model_validate(
            {
                "instrument": instrument,
                "timestamp": "2026-08-29T09:15:00Z",
                "last_price": Decimal("100"),
                "day_volume": Decimal("-1"),
            }
        )


def test_market_regime_rejects_blank_evidence() -> None:
    with pytest.raises(ValidationError):
        MarketRegime.model_validate(
            {
                "observed_at": "2026-08-29T09:15:00Z",
                "regime": MarketRegimeType.NEUTRAL,
                "confidence": Decimal("0.5"),
                "evidence": [""],
            }
        )
