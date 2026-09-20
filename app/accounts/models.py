"""Immutable canonical facts from read-only external trading-account providers."""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from app.core.schemas import (
    CanonicalModel,
    FiniteDecimal,
    Instrument,
    NonBlankStr,
    NonNegativeDecimal,
    UtcDatetime,
)


class TradingAccountSnapshot(CanonicalModel):
    """Account state known at one RevMind observation boundary."""

    schema_version: Literal[1] = 1
    provider: NonBlankStr
    provider_account_id: NonBlankStr
    account_name: NonBlankStr | None = None
    currency: NonBlankStr
    balance: FiniteDecimal
    equity: FiniteDecimal
    margin: NonNegativeDecimal | None = None
    free_margin: FiniteDecimal | None = None
    observed_at: UtcDatetime

    def model_post_init(self, __context: object) -> None:
        object.__setattr__(self, "provider", self.provider.lower())
        object.__setattr__(self, "currency", self.currency.upper())


class ExternalOpenPositionFact(CanonicalModel):
    """Provider-reported open position; an absent instrument means explicitly unmapped."""

    schema_version: Literal[1] = 1
    provider: NonBlankStr
    provider_account_id: NonBlankStr
    provider_record_id: NonBlankStr
    provider_symbol: NonBlankStr
    instrument: Instrument | None = None
    side: Literal["LONG", "SHORT"]
    quantity: NonNegativeDecimal = Field(gt=0)
    open_price: NonNegativeDecimal
    opened_at: UtcDatetime
    current_price: NonNegativeDecimal | None = None
    unrealized_profit_loss: FiniteDecimal | None = None
    observed_at: UtcDatetime

    @model_validator(mode="after")
    def event_precedes_observation(self) -> "ExternalOpenPositionFact":
        if self.opened_at > self.observed_at:
            raise ValueError("opened_at cannot be after observed_at")
        return self


class ExternalOpenOrderFact(CanonicalModel):
    """Provider-reported pending order with no mutation capability."""

    schema_version: Literal[1] = 1
    provider: NonBlankStr
    provider_account_id: NonBlankStr
    provider_record_id: NonBlankStr
    provider_symbol: NonBlankStr
    instrument: Instrument | None = None
    side: Literal["BUY", "SELL"]
    order_type: NonBlankStr
    quantity: NonNegativeDecimal = Field(gt=0)
    declared_price: NonNegativeDecimal | None = None
    created_at: UtcDatetime
    observed_at: UtcDatetime

    @model_validator(mode="after")
    def event_precedes_observation(self) -> "ExternalOpenOrderFact":
        if self.created_at > self.observed_at:
            raise ValueError("created_at cannot be after observed_at")
        return self


class ExternalTransactionFact(CanonicalModel):
    """Recent provider transaction; completeness is declared by the containing batch."""

    schema_version: Literal[1] = 1
    provider: NonBlankStr
    provider_account_id: NonBlankStr
    provider_record_id: NonBlankStr
    provider_symbol: NonBlankStr | None = None
    instrument: Instrument | None = None
    transaction_type: NonBlankStr
    quantity: NonNegativeDecimal | None = None
    price: NonNegativeDecimal | None = None
    profit_loss: FiniteDecimal | None = None
    event_at: UtcDatetime
    observed_at: UtcDatetime

    @model_validator(mode="after")
    def event_precedes_observation(self) -> "ExternalTransactionFact":
        if self.event_at > self.observed_at:
            raise ValueError("event_at cannot be after observed_at")
        if self.instrument is not None and self.provider_symbol is None:
            raise ValueError("mapped transaction requires provider_symbol")
        return self


class ExternalPerformanceObservation(CanonicalModel):
    """Daily account performance reported by an external provider."""

    schema_version: Literal[1] = 1
    provider: NonBlankStr
    provider_account_id: NonBlankStr
    effective_date: date
    balance: FiniteDecimal | None = None
    equity: FiniteDecimal | None = None
    gain_percent: FiniteDecimal | None = None
    observed_at: UtcDatetime

    @model_validator(mode="after")
    def contains_measurement(self) -> "ExternalPerformanceObservation":
        if self.balance is None and self.equity is None and self.gain_percent is None:
            raise ValueError("performance observation requires at least one measurement")
        if self.effective_date > self.observed_at.date():
            raise ValueError("effective_date cannot be after observed_at")
        return self


class ExternalSentimentContext(CanonicalModel):
    """Non-authoritative population context that cannot participate in trade decisions."""

    schema_version: Literal[1] = 1
    provider: NonBlankStr
    provider_symbol: NonBlankStr
    instrument: Instrument | None = None
    long_percent: Decimal = Field(ge=0, le=100, allow_inf_nan=False)
    short_percent: Decimal = Field(ge=0, le=100, allow_inf_nan=False)
    observed_at: UtcDatetime
    authority: Literal["CONTEXT_ONLY"] = "CONTEXT_ONLY"

    @model_validator(mode="after")
    def percentages_total(self) -> "ExternalSentimentContext":
        if self.long_percent + self.short_percent != Decimal("100"):
            raise ValueError("sentiment percentages must total 100")
        return self


type ExternalAccountFact = (
    ExternalOpenPositionFact
    | ExternalOpenOrderFact
    | ExternalTransactionFact
    | ExternalPerformanceObservation
)


class AccountFactBatch(CanonicalModel):
    """Atomic read-only result from one account synchronization."""

    schema_version: Literal[1] = 1
    account: TradingAccountSnapshot
    positions: tuple[ExternalOpenPositionFact, ...] = ()
    orders: tuple[ExternalOpenOrderFact, ...] = ()
    transactions: tuple[ExternalTransactionFact, ...] = ()
    performance: tuple[ExternalPerformanceObservation, ...] = ()
    history_scope: Literal["RECENT_INCOMPLETE", "NOT_REQUESTED"] = "NOT_REQUESTED"

    @model_validator(mode="after")
    def one_observation_boundary(self) -> "AccountFactBatch":
        expected = (self.account.provider, self.account.provider_account_id)
        facts: tuple[ExternalAccountFact, ...] = (
            *self.positions,
            *self.orders,
            *self.transactions,
            *self.performance,
        )
        for fact in facts:
            if (fact.provider.lower(), fact.provider_account_id) != expected:
                raise ValueError("batch contains a fact from another provider or account")
            if fact.observed_at != self.account.observed_at:
                raise ValueError("batch facts must share one observed_at boundary")
        return self
