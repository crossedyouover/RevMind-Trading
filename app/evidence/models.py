"""Immutable contracts for deterministic Phase 8 market evidence."""

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, ClassVar

from pydantic import Field, ValidationInfo, field_validator, model_validator

from app.core.schemas import (
    CanonicalModel,
    FiniteDecimal,
    Instrument,
    MarketBar,
    Timeframe,
    UtcDatetime,
)
from app.technical.models import (
    TECHNICAL_FEATURE_KEY_ORDER,
    TechnicalFeatureKey,
    TechnicalFeatureStatus,
    TechnicalSnapshot,
)

MAX_EVIDENCE_PERIOD = 100_000
EvidencePeriod = Annotated[int, Field(strict=True, ge=1, le=MAX_EVIDENCE_PERIOD)]


class MarketEvidenceKey(StrEnum):
    """Closed Phase 8 evidence set; member order is canonical order."""

    PRICE_ABOVE_SMA = "PRICE_ABOVE_SMA"
    PRICE_BELOW_SMA = "PRICE_BELOW_SMA"
    EMA_ABOVE_SMA = "EMA_ABOVE_SMA"
    EMA_BELOW_SMA = "EMA_BELOW_SMA"
    POSITIVE_RETURN = "POSITIVE_RETURN"
    NEGATIVE_RETURN = "NEGATIVE_RETURN"
    RSI_OVERBOUGHT = "RSI_OVERBOUGHT"
    RSI_OVERSOLD = "RSI_OVERSOLD"
    RSI_MIDRANGE = "RSI_MIDRANGE"
    CLOSE_BREAKOUT_ABOVE_PRIOR_HIGH = "CLOSE_BREAKOUT_ABOVE_PRIOR_HIGH"
    CLOSE_BREAKDOWN_BELOW_PRIOR_LOW = "CLOSE_BREAKDOWN_BELOW_PRIOR_LOW"
    VOLUME_ABOVE_MEAN = "VOLUME_ABOVE_MEAN"
    VOLUME_ZSCORE_HIGH = "VOLUME_ZSCORE_HIGH"
    VOLUME_ZSCORE_LOW = "VOLUME_ZSCORE_LOW"


MARKET_EVIDENCE_KEY_ORDER: tuple[MarketEvidenceKey, ...] = tuple(MarketEvidenceKey)


class MarketEvidenceStatus(StrEnum):
    """Deterministic evaluation state for one evidence rule."""

    WARMING_UP = "WARMING_UP"
    UNDEFINED = "UNDEFINED"
    INACTIVE = "INACTIVE"
    ACTIVE = "ACTIVE"


class EvidenceMeasurementKey(StrEnum):
    """Closed set of non-technical operands and parameters."""

    CLOSE = "CLOSE"
    VOLUME = "VOLUME"
    THRESHOLD = "THRESHOLD"


_TECHNICAL_ORDER = {key: index for index, key in enumerate(TECHNICAL_FEATURE_KEY_ORDER)}
_MEASUREMENT_ORDER = {key: index for index, key in enumerate(EvidenceMeasurementKey)}


class AlignedTechnicalHistory(CanonicalModel):
    """Structurally aligned canonical bars and Phase 7 technical snapshots.

    Alignment proves structure only; it does not prove semantic or cryptographic lineage.
    """

    bars: tuple[MarketBar, ...]
    technical_snapshots: tuple[TechnicalSnapshot, ...]

    @field_validator("bars", "technical_snapshots", mode="before")
    @classmethod
    def require_tuples(cls, value: object) -> object:
        """Reject mutable containers and implicit sequence coercion."""
        if not isinstance(value, tuple):
            raise ValueError("aligned history fields must be tuples")
        return value

    @model_validator(mode="after")
    def validate_alignment(self) -> "AlignedTechnicalHistory":
        """Require exact identity, timeframe, timestamp, and chronological alignment."""
        if len(self.bars) != len(self.technical_snapshots):
            raise ValueError("bar and technical snapshot counts must match")
        previous_timestamp = None
        first_instrument = None
        first_timeframe = None
        for bar, snapshot in zip(self.bars, self.technical_snapshots, strict=True):
            if bar.instrument != snapshot.instrument:
                raise ValueError("bar and technical snapshot instruments must match")
            if bar.timeframe is not snapshot.timeframe:
                raise ValueError("bar and technical snapshot timeframes must match")
            if bar.timestamp != snapshot.timestamp:
                raise ValueError("bar and technical snapshot timestamps must match")
            if first_instrument is None:
                first_instrument = bar.instrument
                first_timeframe = bar.timeframe
            elif bar.instrument != first_instrument:
                raise ValueError("aligned history must contain one instrument")
            elif bar.timeframe is not first_timeframe:
                raise ValueError("aligned history must contain one timeframe")
            if previous_timestamp is not None and bar.timestamp <= previous_timestamp:
                if bar.timestamp == previous_timestamp:
                    raise ValueError("aligned history contains duplicate timestamps")
                raise ValueError("aligned history timestamps must be strictly increasing")
            previous_timestamp = bar.timestamp
        return self


class EvidenceFeatureSource(CanonicalModel):
    """Exact Phase 7 feature operand copied into evidence provenance."""

    timestamp: UtcDatetime
    key: TechnicalFeatureKey
    period: EvidencePeriod
    status: TechnicalFeatureStatus
    value: FiniteDecimal | None

    @field_validator("value", mode="before")
    @classmethod
    def require_exact_decimal_value(cls, value: object, info: ValidationInfo) -> object:
        """Reject Python coercion while accepting canonical Decimal JSON strings."""
        if value is None or isinstance(value, Decimal):
            return value
        if info.mode == "json" and isinstance(value, str):
            try:
                return Decimal(value)
            except ArithmeticError as exc:
                raise ValueError("feature-source values must be valid Decimals") from exc
        raise ValueError("feature-source values must be Decimal or None")

    @model_validator(mode="after")
    def validate_status_value(self) -> "EvidenceFeatureSource":
        """Preserve Phase 7 availability/value semantics."""
        if self.status is TechnicalFeatureStatus.AVAILABLE and self.value is None:
            raise ValueError("AVAILABLE feature sources require a value")
        if self.status is not TechnicalFeatureStatus.AVAILABLE and self.value is not None:
            raise ValueError("unavailable feature sources require value=None")
        return self


class EvidenceMeasurement(CanonicalModel):
    """Typed non-Phase-7 operand or threshold used by one evidence rule."""

    key: EvidenceMeasurementKey
    value: FiniteDecimal | tuple[FiniteDecimal, FiniteDecimal]

    @field_validator("value", mode="before")
    @classmethod
    def require_exact_decimal_value(cls, value: object, info: ValidationInfo) -> object:
        """Reject Python coercion while restoring canonical Decimal JSON strings."""
        if isinstance(value, Decimal):
            return value
        if isinstance(value, tuple):
            if len(value) == 2 and all(isinstance(item, Decimal) for item in value):
                return value
            raise ValueError("measurement bounds must contain exactly two Decimals")
        if info.mode == "json" and isinstance(value, str):
            try:
                return Decimal(value)
            except ArithmeticError as exc:
                raise ValueError("measurement values must be valid Decimals") from exc
        if info.mode == "json" and isinstance(value, list):
            if len(value) != 2 or not all(isinstance(item, str) for item in value):
                raise ValueError("JSON measurement bounds must contain two Decimal strings")
            try:
                return tuple(Decimal(item) for item in value)
            except ArithmeticError as exc:
                raise ValueError("measurement bounds must be valid Decimals") from exc
        raise ValueError("measurement values must be Decimal or a Decimal bounds tuple")

    @model_validator(mode="after")
    def validate_value_shape(self) -> "EvidenceMeasurement":
        """Reserve a two-value tuple for the genuine lower/upper threshold pair."""
        if isinstance(self.value, tuple):
            if self.key is not EvidenceMeasurementKey.THRESHOLD:
                raise ValueError("only THRESHOLD measurements may contain a bounds pair")
            if self.value[0] >= self.value[1]:
                raise ValueError("threshold bounds must be strictly increasing")
        return self


_RULE_FEATURE_KEYS: dict[MarketEvidenceKey, tuple[TechnicalFeatureKey, ...]] = {
    MarketEvidenceKey.PRICE_ABOVE_SMA: (TechnicalFeatureKey.SMA_CLOSE,),
    MarketEvidenceKey.PRICE_BELOW_SMA: (TechnicalFeatureKey.SMA_CLOSE,),
    MarketEvidenceKey.EMA_ABOVE_SMA: (
        TechnicalFeatureKey.SMA_CLOSE,
        TechnicalFeatureKey.EMA_CLOSE,
    ),
    MarketEvidenceKey.EMA_BELOW_SMA: (
        TechnicalFeatureKey.SMA_CLOSE,
        TechnicalFeatureKey.EMA_CLOSE,
    ),
    MarketEvidenceKey.POSITIVE_RETURN: (TechnicalFeatureKey.ARITHMETIC_RETURN,),
    MarketEvidenceKey.NEGATIVE_RETURN: (TechnicalFeatureKey.ARITHMETIC_RETURN,),
    MarketEvidenceKey.RSI_OVERBOUGHT: (TechnicalFeatureKey.RSI_CLOSE_WILDER,),
    MarketEvidenceKey.RSI_OVERSOLD: (TechnicalFeatureKey.RSI_CLOSE_WILDER,),
    MarketEvidenceKey.RSI_MIDRANGE: (TechnicalFeatureKey.RSI_CLOSE_WILDER,),
    MarketEvidenceKey.CLOSE_BREAKOUT_ABOVE_PRIOR_HIGH: (TechnicalFeatureKey.ROLLING_HIGHEST_HIGH,),
    MarketEvidenceKey.CLOSE_BREAKDOWN_BELOW_PRIOR_LOW: (TechnicalFeatureKey.ROLLING_LOWEST_LOW,),
    MarketEvidenceKey.VOLUME_ABOVE_MEAN: (TechnicalFeatureKey.VOLUME_MEAN,),
    MarketEvidenceKey.VOLUME_ZSCORE_HIGH: (TechnicalFeatureKey.VOLUME_ZSCORE,),
    MarketEvidenceKey.VOLUME_ZSCORE_LOW: (TechnicalFeatureKey.VOLUME_ZSCORE,),
}
_RULE_MEASUREMENTS: dict[MarketEvidenceKey, tuple[EvidenceMeasurementKey, ...]] = {
    MarketEvidenceKey.PRICE_ABOVE_SMA: (EvidenceMeasurementKey.CLOSE,),
    MarketEvidenceKey.PRICE_BELOW_SMA: (EvidenceMeasurementKey.CLOSE,),
    MarketEvidenceKey.EMA_ABOVE_SMA: (),
    MarketEvidenceKey.EMA_BELOW_SMA: (),
    MarketEvidenceKey.POSITIVE_RETURN: (),
    MarketEvidenceKey.NEGATIVE_RETURN: (),
    MarketEvidenceKey.RSI_OVERBOUGHT: (EvidenceMeasurementKey.THRESHOLD,),
    MarketEvidenceKey.RSI_OVERSOLD: (EvidenceMeasurementKey.THRESHOLD,),
    MarketEvidenceKey.RSI_MIDRANGE: (EvidenceMeasurementKey.THRESHOLD,),
    MarketEvidenceKey.CLOSE_BREAKOUT_ABOVE_PRIOR_HIGH: (EvidenceMeasurementKey.CLOSE,),
    MarketEvidenceKey.CLOSE_BREAKDOWN_BELOW_PRIOR_LOW: (EvidenceMeasurementKey.CLOSE,),
    MarketEvidenceKey.VOLUME_ABOVE_MEAN: (EvidenceMeasurementKey.VOLUME,),
    MarketEvidenceKey.VOLUME_ZSCORE_HIGH: (EvidenceMeasurementKey.THRESHOLD,),
    MarketEvidenceKey.VOLUME_ZSCORE_LOW: (EvidenceMeasurementKey.THRESHOLD,),
}


class MarketEvidence(CanonicalModel):
    """One complete, typed deterministic market-evidence evaluation."""

    key: MarketEvidenceKey
    status: MarketEvidenceStatus
    feature_sources: tuple[EvidenceFeatureSource, ...]
    measurements: tuple[EvidenceMeasurement, ...]

    @model_validator(mode="after")
    def validate_provenance(self) -> "MarketEvidence":
        """Reject noncanonical, incomplete, duplicate, or contradictory provenance."""
        source_refs = tuple(
            (source.timestamp, source.key, source.period) for source in self.feature_sources
        )
        if len(source_refs) != len(set(source_refs)):
            raise ValueError("feature-source references must be unique")
        expected_sources = tuple(
            sorted(
                source_refs,
                key=lambda item: (item[0], _TECHNICAL_ORDER[item[1]], item[2]),
            )
        )
        if source_refs != expected_sources:
            raise ValueError("feature sources are not in canonical order")

        measurement_keys = tuple(item.key for item in self.measurements)
        if len(measurement_keys) != len(set(measurement_keys)):
            raise ValueError("measurement keys must be unique")
        expected_measurements = tuple(sorted(measurement_keys, key=_MEASUREMENT_ORDER.__getitem__))
        if measurement_keys != expected_measurements:
            raise ValueError("measurements are not in canonical order")

        expected_keys = _RULE_FEATURE_KEYS[self.key]
        actual_keys = tuple(source.key for source in self.feature_sources)
        first_bar_extreme = (
            self.key
            in {
                MarketEvidenceKey.CLOSE_BREAKOUT_ABOVE_PRIOR_HIGH,
                MarketEvidenceKey.CLOSE_BREAKDOWN_BELOW_PRIOR_LOW,
            }
            and not self.feature_sources
        )
        if actual_keys != expected_keys and not first_bar_extreme:
            raise ValueError("feature provenance is insufficient for the evidence rule")
        expected_measurement_keys = _RULE_MEASUREMENTS[self.key]
        if measurement_keys != expected_measurement_keys:
            raise ValueError("measurement provenance is insufficient for the evidence rule")

        statuses = tuple(source.status for source in self.feature_sources)
        if first_bar_extreme and self.status is not MarketEvidenceStatus.WARMING_UP:
            raise ValueError("source-free extrema evidence must be first-bar WARMING_UP")
        if self.status is MarketEvidenceStatus.WARMING_UP:
            if statuses and TechnicalFeatureStatus.WARMING_UP not in statuses:
                raise ValueError("WARMING_UP evidence requires a WARMING_UP dependency")
            if not statuses and not first_bar_extreme:
                raise ValueError("only first-bar extrema may warm up without a source")
        elif self.status is MarketEvidenceStatus.UNDEFINED:
            if TechnicalFeatureStatus.WARMING_UP in statuses:
                raise ValueError("UNDEFINED evidence cannot contain WARMING_UP dependencies")
            if TechnicalFeatureStatus.UNDEFINED not in statuses:
                raise ValueError("UNDEFINED evidence requires an UNDEFINED dependency")
        elif any(status is not TechnicalFeatureStatus.AVAILABLE for status in statuses):
            raise ValueError("ACTIVE and INACTIVE evidence require available dependencies")
        if self.status in {MarketEvidenceStatus.ACTIVE, MarketEvidenceStatus.INACTIVE}:
            if self._condition_is_true() is not (self.status is MarketEvidenceStatus.ACTIVE):
                raise ValueError("ACTIVE/INACTIVE status contradicts the evidence operands")
        return self

    def _condition_is_true(self) -> bool:
        """Re-evaluate a fully available rule to keep model state self-consistent."""
        values = tuple(source.value for source in self.feature_sources)
        if any(value is None for value in values):
            raise ValueError("evaluated evidence requires finite source values")
        decimals = tuple(value for value in values if value is not None)

        def scalar_measurement() -> Decimal:
            value = self.measurements[0].value
            if isinstance(value, tuple):
                raise ValueError("this evidence rule requires a scalar measurement")
            return value

        if self.key is MarketEvidenceKey.PRICE_ABOVE_SMA:
            return scalar_measurement() > decimals[0]
        if self.key is MarketEvidenceKey.PRICE_BELOW_SMA:
            return scalar_measurement() < decimals[0]
        if self.key is MarketEvidenceKey.EMA_ABOVE_SMA:
            return decimals[1] > decimals[0]
        if self.key is MarketEvidenceKey.EMA_BELOW_SMA:
            return decimals[1] < decimals[0]
        if self.key is MarketEvidenceKey.POSITIVE_RETURN:
            return decimals[0] > Decimal(0)
        if self.key is MarketEvidenceKey.NEGATIVE_RETURN:
            return decimals[0] < Decimal(0)
        if self.key is MarketEvidenceKey.RSI_OVERBOUGHT:
            return decimals[0] >= scalar_measurement()
        if self.key is MarketEvidenceKey.RSI_OVERSOLD:
            return decimals[0] <= scalar_measurement()
        if self.key is MarketEvidenceKey.RSI_MIDRANGE:
            bounds = self.measurements[0].value
            if not isinstance(bounds, tuple):
                raise ValueError("RSI_MIDRANGE requires lower and upper threshold bounds")
            return bounds[0] < decimals[0] < bounds[1]
        if self.key is MarketEvidenceKey.CLOSE_BREAKOUT_ABOVE_PRIOR_HIGH:
            return scalar_measurement() > decimals[0]
        if self.key is MarketEvidenceKey.CLOSE_BREAKDOWN_BELOW_PRIOR_LOW:
            return scalar_measurement() < decimals[0]
        if self.key is MarketEvidenceKey.VOLUME_ABOVE_MEAN:
            return scalar_measurement() > decimals[0]
        if self.key is MarketEvidenceKey.VOLUME_ZSCORE_HIGH:
            return decimals[0] >= scalar_measurement()
        if self.key is MarketEvidenceKey.VOLUME_ZSCORE_LOW:
            return decimals[0] <= scalar_measurement()
        raise ValueError("unsupported market evidence key")


class EvidenceSnapshot(CanonicalModel):
    """Complete canonical Phase 8 evidence state aligned to one market bar."""

    instrument: Instrument
    timeframe: Timeframe
    timestamp: UtcDatetime
    evidence: tuple[MarketEvidence, ...]

    @model_validator(mode="after")
    def validate_complete_evidence(self) -> "EvidenceSnapshot":
        """Require all 14 keys exactly once, in frozen order, without future sources."""
        keys = tuple(item.key for item in self.evidence)
        if keys != MARKET_EVIDENCE_KEY_ORDER:
            raise ValueError("evidence must contain the complete canonical 14-key set")
        if any(
            source.timestamp > self.timestamp
            for item in self.evidence
            for source in item.feature_sources
        ):
            raise ValueError("evidence feature sources cannot come from the future")
        return self


class MarketEvidenceConfig(CanonicalModel):
    """Strict periods and thresholds for deterministic evidence interpretation."""

    price_sma_period: EvidencePeriod = 20
    trend_ema_period: EvidencePeriod = 20
    trend_sma_period: EvidencePeriod = 20
    return_period: EvidencePeriod = 1
    rsi_period: EvidencePeriod = 14
    breakout_high_period: EvidencePeriod = 20
    breakdown_low_period: EvidencePeriod = 20
    volume_mean_period: EvidencePeriod = 20
    volume_zscore_period: EvidencePeriod = 20
    rsi_overbought: FiniteDecimal = Decimal("70")
    rsi_oversold: FiniteDecimal = Decimal("30")
    volume_zscore_high: FiniteDecimal = Decimal("2")
    volume_zscore_low: FiniteDecimal = Decimal("-2")

    _PERIOD_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "price_sma_period",
            "trend_ema_period",
            "trend_sma_period",
            "return_period",
            "rsi_period",
            "breakout_high_period",
            "breakdown_low_period",
            "volume_mean_period",
            "volume_zscore_period",
        }
    )
    _THRESHOLD_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "rsi_overbought",
            "rsi_oversold",
            "volume_zscore_high",
            "volume_zscore_low",
        }
    )

    @field_validator(*_PERIOD_FIELDS, mode="before")
    @classmethod
    def require_strict_period(cls, value: object) -> object:
        """Reject coercion before constrained-integer validation."""
        if type(value) is not int:
            raise ValueError("evidence periods must be strict integers")
        return value

    @field_validator(*_THRESHOLD_FIELDS, mode="before")
    @classmethod
    def require_decimal_threshold(cls, value: object, info: ValidationInfo) -> object:
        """Accept Decimal in Python and canonical Decimal strings only while reading JSON."""
        if info.mode == "json" and isinstance(value, str):
            try:
                value = Decimal(value)
            except ArithmeticError as exc:
                raise ValueError("evidence thresholds must be valid Decimals") from exc
        if not isinstance(value, Decimal):
            raise ValueError("evidence thresholds must be Decimal values")
        if not value.is_finite():
            raise ValueError("evidence thresholds must be finite")
        return value

    @model_validator(mode="after")
    def validate_threshold_relations(self) -> "MarketEvidenceConfig":
        """Require non-overlapping RSI and signed z-score thresholds."""
        if not Decimal(0) <= self.rsi_oversold < self.rsi_overbought <= Decimal(100):
            raise ValueError("RSI thresholds must satisfy 0 <= oversold < overbought <= 100")
        if not self.volume_zscore_low < Decimal(0) < self.volume_zscore_high:
            raise ValueError("z-score thresholds must satisfy low < 0 < high")
        return self
