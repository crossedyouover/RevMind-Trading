"""Canonical external-account facts remain immutable, PIT-safe, and read-only."""

import inspect
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.accounts.models import (
    AccountFactBatch,
    ExternalOpenPositionFact,
    ExternalPerformanceObservation,
    ExternalSentimentContext,
    TradingAccountSnapshot,
)
from app.accounts.protocol import ReadOnlyTradingAccountProvider
from app.core.schemas import AssetClass, Instrument

OBSERVED = datetime(2026, 9, 20, 12, tzinfo=UTC)
EURUSD = Instrument(symbol="EURUSD", asset_class=AssetClass.FX, exchange="FX", currency="USD")


def account(**changes: object) -> TradingAccountSnapshot:
    values: dict[str, object] = {
        "provider": "Myfxbook",
        "provider_account_id": "account-1",
        "currency": "eur",
        "balance": Decimal("10000.123456789"),
        "equity": Decimal("9980.123456789"),
        "margin": Decimal("125.50"),
        "observed_at": OBSERVED,
    }
    values.update(changes)
    return TradingAccountSnapshot.model_validate(values)


def position(**changes: object) -> ExternalOpenPositionFact:
    values: dict[str, object] = {
        "provider": "myfxbook",
        "provider_account_id": "account-1",
        "provider_record_id": "trade-7",
        "provider_symbol": "EURUSD",
        "instrument": EURUSD,
        "side": "LONG",
        "quantity": Decimal("0.10"),
        "open_price": Decimal("1.12345"),
        "opened_at": OBSERVED - timedelta(hours=1),
        "observed_at": OBSERVED,
    }
    values.update(changes)
    return ExternalOpenPositionFact.model_validate(values)


def test_account_identity_normalizes_and_decimal_precision_survives() -> None:
    value = account()

    assert value.provider == "myfxbook"
    assert value.currency == "EUR"
    assert value.balance == Decimal("10000.123456789")
    assert '"10000.123456789"' in value.model_dump_json()


def test_models_are_immutable_and_reject_unknown_fields() -> None:
    value = account()
    with pytest.raises(ValidationError, match="frozen"):
        value.balance = Decimal("1")
    with pytest.raises(ValidationError, match="extra"):
        account(secret="must-not-enter-canonical-model")


def test_datetimes_require_timezone_and_normalize_to_utc() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        account(observed_at=datetime(2026, 9, 20, 12))

    value = account(observed_at=datetime.fromisoformat("2026-09-20T14:00:00+02:00"))
    assert value.observed_at == OBSERVED


def test_future_position_event_is_rejected() -> None:
    with pytest.raises(ValidationError, match="opened_at"):
        position(opened_at=OBSERVED + timedelta(seconds=1))


def test_unmapped_position_is_preserved_without_inventing_identity() -> None:
    value = position(instrument=None, provider_symbol="EURUSD.a")
    assert value.instrument is None
    assert value.provider_symbol == "EURUSD.a"


def test_batch_is_atomic_to_provider_account_and_observation_boundary() -> None:
    valid = AccountFactBatch(
        account=account(), positions=(position(),), history_scope="RECENT_INCOMPLETE"
    )
    assert valid.positions[0].provider_record_id == "trade-7"

    with pytest.raises(ValidationError, match="another provider or account"):
        AccountFactBatch(account=account(), positions=(position(provider_account_id="other"),))
    with pytest.raises(ValidationError, match="observed_at"):
        AccountFactBatch(
            account=account(), positions=(position(observed_at=OBSERVED - timedelta(seconds=1)),)
        )


def test_performance_requires_measurement_and_cannot_be_future_dated() -> None:
    base = {
        "provider": "myfxbook",
        "provider_account_id": "account-1",
        "effective_date": date(2026, 9, 20),
        "observed_at": OBSERVED,
    }
    with pytest.raises(ValidationError, match="measurement"):
        ExternalPerformanceObservation(**base)
    with pytest.raises(ValidationError, match="effective_date"):
        ExternalPerformanceObservation(
            **{**base, "effective_date": date(2026, 9, 21)}, balance=Decimal("1")
        )


def test_sentiment_is_context_only_and_requires_complete_population() -> None:
    value = ExternalSentimentContext(
        provider="myfxbook",
        provider_symbol="EURUSD",
        instrument=EURUSD,
        long_percent=Decimal("47.25"),
        short_percent=Decimal("52.75"),
        observed_at=OBSERVED,
    )
    assert value.authority == "CONTEXT_ONLY"
    with pytest.raises(ValidationError, match="total 100"):
        value.model_copy(update={"short_percent": Decimal("50")}).model_validate(
            {**value.model_dump(), "short_percent": Decimal("50")}
        )


def test_provider_protocol_exposes_no_execution_surface() -> None:
    public_methods = {
        name
        for name, member in inspect.getmembers(ReadOnlyTradingAccountProvider)
        if inspect.isfunction(member) and not name.startswith("_")
    }
    assert public_methods == {
        "sync_account",
        "daily_performance",
        "sentiment_context",
        "disconnect",
        "aclose",
    }
    assert not public_methods & {"place_order", "cancel_order", "modify_order", "execute"}
