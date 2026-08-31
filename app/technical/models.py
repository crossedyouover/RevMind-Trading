"""Immutable public contracts for deterministic technical evidence."""

from enum import StrEnum
from typing import Annotated, ClassVar

from pydantic import Field, field_validator, model_validator

from app.core.schemas import CanonicalModel, FiniteDecimal, Instrument, Timeframe, UtcDatetime

MAX_TECHNICAL_PERIOD = 100_000
TechnicalPeriod = Annotated[int, Field(strict=True, ge=1, le=MAX_TECHNICAL_PERIOD)]


class TechnicalFeatureKey(StrEnum):
    """Closed Phase 7 technical feature set."""

    SMA_CLOSE = "SMA_CLOSE"
    EMA_CLOSE = "EMA_CLOSE"
    RSI_CLOSE_WILDER = "RSI_CLOSE_WILDER"
    ATR_WILDER = "ATR_WILDER"
    ROLLING_HIGHEST_HIGH = "ROLLING_HIGHEST_HIGH"
    ROLLING_LOWEST_LOW = "ROLLING_LOWEST_LOW"
    ARITHMETIC_RETURN = "ARITHMETIC_RETURN"
    VOLUME_MEAN = "VOLUME_MEAN"
    VOLUME_STDDEV_POPULATION = "VOLUME_STDDEV_POPULATION"
    VOLUME_ZSCORE = "VOLUME_ZSCORE"


TECHNICAL_FEATURE_KEY_ORDER: tuple[TechnicalFeatureKey, ...] = (
    TechnicalFeatureKey.SMA_CLOSE,
    TechnicalFeatureKey.EMA_CLOSE,
    TechnicalFeatureKey.RSI_CLOSE_WILDER,
    TechnicalFeatureKey.ATR_WILDER,
    TechnicalFeatureKey.ROLLING_HIGHEST_HIGH,
    TechnicalFeatureKey.ROLLING_LOWEST_LOW,
    TechnicalFeatureKey.ARITHMETIC_RETURN,
    TechnicalFeatureKey.VOLUME_MEAN,
    TechnicalFeatureKey.VOLUME_STDDEV_POPULATION,
    TechnicalFeatureKey.VOLUME_ZSCORE,
)
_FEATURE_ORDER = {key: index for index, key in enumerate(TECHNICAL_FEATURE_KEY_ORDER)}


class TechnicalFeatureStatus(StrEnum):
    """Availability state of one aligned technical feature."""

    WARMING_UP = "WARMING_UP"
    AVAILABLE = "AVAILABLE"
    UNDEFINED = "UNDEFINED"


class TechnicalAnalysisConfig(CanonicalModel):
    """Deterministic periods enabled for each Phase 7 feature family."""

    sma_periods: tuple[int, ...] = (20,)
    ema_periods: tuple[int, ...] = (20,)
    rsi_periods: tuple[int, ...] = (14,)
    atr_periods: tuple[int, ...] = (14,)
    rolling_high_periods: tuple[int, ...] = (20,)
    rolling_low_periods: tuple[int, ...] = (20,)
    return_periods: tuple[int, ...] = (1,)
    volume_mean_periods: tuple[int, ...] = (20,)
    volume_stddev_periods: tuple[int, ...] = (20,)
    volume_zscore_periods: tuple[int, ...] = (20,)

    _PERIOD_FIELDS: ClassVar[tuple[str, ...]] = (
        "sma_periods", "ema_periods", "rsi_periods", "atr_periods",
        "rolling_high_periods", "rolling_low_periods", "return_periods",
        "volume_mean_periods", "volume_stddev_periods", "volume_zscore_periods",
    )

    @field_validator("*", mode="before")
    @classmethod
    def validate_period_tuple(cls, value: object) -> object:
        """Reject coercion, invalid periods, duplicates, and implicit sorting."""
        if not isinstance(value, tuple):
            raise ValueError("technical periods must be supplied as a tuple")
        if any(type(period) is not int for period in value):
            raise ValueError("technical periods must contain integers, not bool or float")
        if any(period < 1 or period > MAX_TECHNICAL_PERIOD for period in value):
            raise ValueError(f"technical periods must be between 1 and {MAX_TECHNICAL_PERIOD}")
        if any(current <= previous for previous, current in zip(value, value[1:], strict=False)):
            raise ValueError("technical periods must be unique and strictly ascending")
        return value

    @model_validator(mode="after")
    def require_at_least_one_feature(self) -> "TechnicalAnalysisConfig":
        """Reject a configuration which requests no technical evidence."""
        if not any(getattr(self, field) for field in self._PERIOD_FIELDS):
            raise ValueError("at least one technical feature period is required")
        return self


class TechnicalFeature(CanonicalModel):
    """One typed, period-specific technical value aligned to a market bar."""

    key: TechnicalFeatureKey
    period: TechnicalPeriod
    status: TechnicalFeatureStatus
    value: FiniteDecimal | None

    @model_validator(mode="after")
    def validate_status_value(self) -> "TechnicalFeature":
        """Keep availability status and optional value semantically consistent."""
        if self.status is TechnicalFeatureStatus.AVAILABLE and self.value is None:
            raise ValueError("AVAILABLE technical features require a value")
        if self.status is not TechnicalFeatureStatus.AVAILABLE and self.value is not None:
            raise ValueError("WARMING_UP and UNDEFINED technical features require value=None")
        return self


class TechnicalSnapshot(CanonicalModel):
    """Complete aligned technical evidence for one canonical market bar."""

    instrument: Instrument
    timeframe: Timeframe
    timestamp: UtcDatetime
    features: tuple[TechnicalFeature, ...]

    @model_validator(mode="after")
    def validate_feature_order(self) -> "TechnicalSnapshot":
        """Require unique features in explicit canonical key and period order."""
        pairs = tuple((feature.key, feature.period) for feature in self.features)
        if len(pairs) != len(set(pairs)):
            raise ValueError("technical feature key/period pairs must be unique")
        expected = tuple(sorted(pairs, key=lambda pair: (_FEATURE_ORDER[pair[0]], pair[1])))
        if pairs != expected:
            raise ValueError("technical features are not in canonical order")
        return self
