"""Canonical domain contracts shared by RevMind Trading components.

Models are immutable and reject unknown fields. Market-relevant datetimes reject naive values and
are normalized to UTC. Decimal values remain ``Decimal`` internally and serialize losslessly as
JSON strings under Pydantic's standard JSON representation.
"""

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints


def _normalize_utc(value: datetime) -> datetime:
    """Reject naive datetimes and normalize aware datetimes to UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include timezone information")
    return value.astimezone(UTC)


UtcDatetime = Annotated[datetime, AfterValidator(_normalize_utc)]
NonBlankStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
FiniteDecimal = Annotated[Decimal, Field(allow_inf_nan=False)]
NonNegativeDecimal = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
Confidence = Annotated[Decimal, Field(ge=0, le=1, allow_inf_nan=False)]


class CanonicalModel(BaseModel):
    """Immutable base contract that rejects unexpected fields."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)


class AssetClass(StrEnum):
    """Canonical asset categories."""

    EQUITY = "EQUITY"
    ETF = "ETF"
    CRYPTO = "CRYPTO"
    INDEX = "INDEX"
    FX = "FX"
    FUTURE = "FUTURE"
    OPTION = "OPTION"
    OTHER = "OTHER"


class SignalDirection(StrEnum):
    """Directional posture of a signal or setup."""

    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"


class SignalStatus(StrEnum):
    """Lifecycle status of a signal."""

    CANDIDATE = "CANDIDATE"
    WATCHLIST = "WATCHLIST"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class CatalystSourceType(StrEnum):
    """Authority category of catalyst evidence."""

    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"


class RiskDecisionStatus(StrEnum):
    """Possible deterministic risk outcomes."""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class DeskDecisionStatus(StrEnum):
    """Possible Head of Desk attention outcomes."""

    QUIET = "QUIET"
    WATCHLIST = "WATCHLIST"
    ALERT = "ALERT"


class MarketRegimeType(StrEnum):
    """Canonical broad-market regimes."""

    RISK_ON = "RISK_ON"
    RISK_OFF = "RISK_OFF"
    NEUTRAL = "NEUTRAL"
    MIXED = "MIXED"


class Timeframe(StrEnum):
    """Provider-neutral canonical market-bar intervals."""

    ONE_MINUTE = "ONE_MINUTE"
    FIVE_MINUTES = "FIVE_MINUTES"
    FIFTEEN_MINUTES = "FIFTEEN_MINUTES"
    ONE_HOUR = "ONE_HOUR"
    ONE_DAY = "ONE_DAY"


class Instrument(CanonicalModel):
    """A uniquely described market instrument."""

    symbol: NonBlankStr
    asset_class: AssetClass
    exchange: NonBlankStr | None = None
    currency: NonBlankStr | None = None

    def model_post_init(self, __context: object) -> None:
        """Normalize market identifiers after validation."""
        object.__setattr__(self, "symbol", self.symbol.upper())
        if self.exchange is not None:
            object.__setattr__(self, "exchange", self.exchange.upper())
        if self.currency is not None:
            object.__setattr__(self, "currency", self.currency.upper())


class MarketBar(CanonicalModel):
    """Canonical OHLCV bar at a UTC timestamp."""

    instrument: Instrument
    timeframe: Timeframe
    timestamp: UtcDatetime
    open: NonNegativeDecimal
    high: NonNegativeDecimal
    low: NonNegativeDecimal
    close: NonNegativeDecimal
    volume: NonNegativeDecimal

    def model_post_init(self, __context: object) -> None:
        """Validate OHLC price relationships."""
        if self.high < self.low:
            raise ValueError("high must be greater than or equal to low")
        if self.high < self.open or self.high < self.close:
            raise ValueError("high must be greater than or equal to open and close")
        if self.low > self.open or self.low > self.close:
            raise ValueError("low must be less than or equal to open and close")


class MarketSnapshot(CanonicalModel):
    """Point-in-time market observation."""

    instrument: Instrument
    timestamp: UtcDatetime
    last_price: NonNegativeDecimal
    day_volume: NonNegativeDecimal | None = None
    percent_change: FiniteDecimal | None = None


class Signal(CanonicalModel):
    """Validated signal evidence emitted by a future component."""

    signal_id: UUID = Field(default_factory=uuid4)
    instrument: Instrument
    created_at: UtcDatetime
    signal_type: NonBlankStr
    direction: SignalDirection
    confidence: Confidence
    evidence: list[NonBlankStr]
    source_component: NonBlankStr
    status: SignalStatus


class Setup(CanonicalModel):
    """Defined setup contract without strategy-specific assumptions."""

    setup_id: UUID = Field(default_factory=uuid4)
    instrument: Instrument
    created_at: UtcDatetime
    setup_type: NonBlankStr
    direction: SignalDirection
    trigger_price: NonNegativeDecimal | None = None
    invalidation_price: NonNegativeDecimal | None = None
    confidence: Confidence
    evidence: list[NonBlankStr]


class Catalyst(CanonicalModel):
    """Observed primary or secondary catalyst."""

    catalyst_id: UUID = Field(default_factory=uuid4)
    instrument: Instrument | None = None
    observed_at: UtcDatetime
    headline: NonBlankStr
    source: NonBlankStr
    source_type: CatalystSourceType
    url: str | None = None
    summary: str | None = None


class InsiderActivity(CanonicalModel):
    """Canonical insider transaction evidence without directional filtering."""

    activity_id: UUID = Field(default_factory=uuid4)
    instrument: Instrument
    observed_at: UtcDatetime
    insider_name: NonBlankStr
    insider_role: str | None = None
    transaction_type: NonBlankStr
    shares: NonNegativeDecimal | None = None
    price: NonNegativeDecimal | None = None
    value: NonNegativeDecimal | None = None
    source: NonBlankStr


class MarketRegime(CanonicalModel):
    """Observed broad-market regime evidence."""

    observed_at: UtcDatetime
    regime: MarketRegimeType
    confidence: Confidence
    evidence: list[NonBlankStr]


class PortfolioPosition(CanonicalModel):
    """Portfolio exposure supporting positive, zero, and negative quantities."""

    instrument: Instrument
    quantity: FiniteDecimal
    average_price: NonNegativeDecimal
    current_price: NonNegativeDecimal | None = None
    opened_at: UtcDatetime | None = None


class RiskDecision(CanonicalModel):
    """Data contract for a future deterministic risk decision."""

    decision_id: UUID = Field(default_factory=uuid4)
    created_at: UtcDatetime
    status: RiskDecisionStatus
    rule_code: NonBlankStr
    reason: NonBlankStr
    proposed_risk_amount: NonNegativeDecimal | None = None


class DeskDecision(CanonicalModel):
    """Data contract for a future Head of Desk decision."""

    decision_id: UUID = Field(default_factory=uuid4)
    created_at: UtcDatetime
    status: DeskDecisionStatus
    instrument: Instrument | None = None
    confidence: Confidence | None = None
    reason: NonBlankStr | None = None
    supporting_signal_ids: list[UUID]
    risk_decision_id: UUID | None = None
