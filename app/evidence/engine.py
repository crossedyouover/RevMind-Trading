"""Stateless deterministic interpretation of aligned Phase 7 technical history."""

from collections.abc import Callable
from decimal import Decimal, DecimalException
from typing import Protocol

from pydantic import ValidationError

from app.evidence.models import (
    AlignedTechnicalHistory,
    EvidenceFeatureSource,
    EvidenceMeasurement,
    EvidenceMeasurementKey,
    EvidenceSnapshot,
    MarketEvidence,
    MarketEvidenceConfig,
    MarketEvidenceKey,
    MarketEvidenceStatus,
)
from app.technical.models import (
    TechnicalFeature,
    TechnicalFeatureKey,
    TechnicalFeatureStatus,
    TechnicalSnapshot,
)


class MarketEvidenceError(Exception):
    """Base exception for deterministic market-evidence analysis."""


class MarketEvidenceInvalidInputError(MarketEvidenceError):
    """Raised when aligned technical input is invalid or incomplete."""


class MarketEvidenceConfigurationError(MarketEvidenceError):
    """Raised when the engine receives an invalid configuration object."""


class MarketEvidenceComputationError(MarketEvidenceError):
    """Raised when valid input cannot produce trustworthy canonical evidence."""


class MarketEvidenceEngine(Protocol):
    """Narrow batch boundary for deterministic Phase 8 evidence interpretation."""

    def analyze(
        self,
        history: AlignedTechnicalHistory,
        config: MarketEvidenceConfig,
    ) -> tuple[EvidenceSnapshot, ...]:
        """Return one immutable evidence snapshot per aligned input pair."""
        ...


type _FeatureIndex = dict[tuple[TechnicalFeatureKey, int], TechnicalFeature]
type _Predicate = Callable[[tuple[Decimal, ...]], bool]


class DeterministicMarketEvidenceEngine:
    """Pure, batch-only Phase 8 reference implementation."""

    def analyze(
        self,
        history: AlignedTechnicalHistory,
        config: MarketEvidenceConfig,
    ) -> tuple[EvidenceSnapshot, ...]:
        """Interpret evidence without sorting, repair, recalculation, or future access."""
        if not isinstance(config, MarketEvidenceConfig):
            raise MarketEvidenceConfigurationError(
                "config must be a validated MarketEvidenceConfig"
            )
        if not isinstance(history, AlignedTechnicalHistory):
            raise MarketEvidenceInvalidInputError(
                "history must be a validated AlignedTechnicalHistory"
            )
        try:
            config = MarketEvidenceConfig.model_validate(
                config.model_dump(mode="python", round_trip=True)
            )
        except (ValidationError, TypeError, ValueError) as exc:
            raise MarketEvidenceConfigurationError(
                "config must be a validated MarketEvidenceConfig"
            ) from exc
        try:
            history = AlignedTechnicalHistory.model_validate(
                history.model_dump(mode="python", round_trip=True)
            )
        except (ValidationError, AttributeError, TypeError, ValueError) as exc:
            raise MarketEvidenceInvalidInputError(
                "history must be a validated AlignedTechnicalHistory"
            ) from exc
        self._verify_alignment(history)
        indexes = tuple(self._index_features(snapshot) for snapshot in history.technical_snapshots)
        self._preflight(indexes, config)
        try:
            return tuple(
                self._build_snapshot(history, indexes, config, index)
                for index in range(len(history.bars))
            )
        except (ValidationError, DecimalException, ArithmeticError) as exc:
            raise MarketEvidenceComputationError(
                "failed to construct trustworthy canonical market evidence"
            ) from exc

    @staticmethod
    def _verify_alignment(history: AlignedTechnicalHistory) -> None:
        """Defend against bypassed model validation without altering supplied history."""
        if len(history.bars) != len(history.technical_snapshots):
            raise MarketEvidenceInvalidInputError("aligned history counts differ")
        previous = None
        instrument = None
        timeframe = None
        for bar, snapshot in zip(history.bars, history.technical_snapshots, strict=True):
            if bar.instrument != snapshot.instrument or bar.timestamp != snapshot.timestamp:
                raise MarketEvidenceInvalidInputError("bar/snapshot alignment is invalid")
            if bar.timeframe is not snapshot.timeframe:
                raise MarketEvidenceInvalidInputError("bar/snapshot timeframes differ")
            if instrument is not None and bar.instrument != instrument:
                raise MarketEvidenceInvalidInputError("history mixes instruments")
            if timeframe is not None and bar.timeframe is not timeframe:
                raise MarketEvidenceInvalidInputError("history mixes timeframes")
            if previous is not None and bar.timestamp <= previous:
                raise MarketEvidenceInvalidInputError(
                    "history timestamps are not strictly increasing"
                )
            instrument, timeframe, previous = bar.instrument, bar.timeframe, bar.timestamp

    @staticmethod
    def _index_features(snapshot: TechnicalSnapshot) -> _FeatureIndex:
        """Build an exact key/period index without substitution."""
        return {(feature.key, feature.period): feature for feature in snapshot.features}

    @classmethod
    def _preflight(
        cls,
        indexes: tuple[_FeatureIndex, ...],
        config: MarketEvidenceConfig,
    ) -> None:
        """Validate every exact dependency before producing any output."""
        current_requirements = (
            (TechnicalFeatureKey.SMA_CLOSE, config.price_sma_period),
            (TechnicalFeatureKey.EMA_CLOSE, config.trend_ema_period),
            (TechnicalFeatureKey.SMA_CLOSE, config.trend_sma_period),
            (TechnicalFeatureKey.ARITHMETIC_RETURN, config.return_period),
            (TechnicalFeatureKey.RSI_CLOSE_WILDER, config.rsi_period),
            (TechnicalFeatureKey.VOLUME_MEAN, config.volume_mean_period),
            (TechnicalFeatureKey.VOLUME_ZSCORE, config.volume_zscore_period),
        )
        for index, feature_index in enumerate(indexes):
            for reference in current_requirements:
                cls._require_feature(feature_index, reference, index)
            if index > 0:
                cls._require_feature(
                    indexes[index - 1],
                    (TechnicalFeatureKey.ROLLING_HIGHEST_HIGH, config.breakout_high_period),
                    index - 1,
                )
                cls._require_feature(
                    indexes[index - 1],
                    (TechnicalFeatureKey.ROLLING_LOWEST_LOW, config.breakdown_low_period),
                    index - 1,
                )

    @staticmethod
    def _require_feature(
        index: _FeatureIndex,
        reference: tuple[TechnicalFeatureKey, int],
        snapshot_index: int,
    ) -> TechnicalFeature:
        try:
            return index[reference]
        except KeyError as exc:
            key, period = reference
            raise MarketEvidenceInvalidInputError(
                f"snapshot {snapshot_index} lacks required feature {key.value}/{period}"
            ) from exc

    @classmethod
    def _build_snapshot(
        cls,
        history: AlignedTechnicalHistory,
        indexes: tuple[_FeatureIndex, ...],
        config: MarketEvidenceConfig,
        index: int,
    ) -> EvidenceSnapshot:
        bar = history.bars[index]
        snapshot = history.technical_snapshots[index]
        features = indexes[index]

        price_sma = cls._source(
            snapshot,
            cls._require_feature(
                features, (TechnicalFeatureKey.SMA_CLOSE, config.price_sma_period), index
            ),
        )
        trend_ema = cls._source(
            snapshot,
            cls._require_feature(
                features, (TechnicalFeatureKey.EMA_CLOSE, config.trend_ema_period), index
            ),
        )
        trend_sma = cls._source(
            snapshot,
            cls._require_feature(
                features, (TechnicalFeatureKey.SMA_CLOSE, config.trend_sma_period), index
            ),
        )
        arithmetic_return = cls._source(
            snapshot,
            cls._require_feature(
                features,
                (TechnicalFeatureKey.ARITHMETIC_RETURN, config.return_period),
                index,
            ),
        )
        rsi = cls._source(
            snapshot,
            cls._require_feature(
                features, (TechnicalFeatureKey.RSI_CLOSE_WILDER, config.rsi_period), index
            ),
        )
        volume_mean = cls._source(
            snapshot,
            cls._require_feature(
                features, (TechnicalFeatureKey.VOLUME_MEAN, config.volume_mean_period), index
            ),
        )
        volume_zscore = cls._source(
            snapshot,
            cls._require_feature(
                features,
                (TechnicalFeatureKey.VOLUME_ZSCORE, config.volume_zscore_period),
                index,
            ),
        )

        close = cls._measurement(EvidenceMeasurementKey.CLOSE, bar.close)
        volume = cls._measurement(EvidenceMeasurementKey.VOLUME, bar.volume)
        overbought = cls._measurement(EvidenceMeasurementKey.THRESHOLD, config.rsi_overbought)
        oversold = cls._measurement(EvidenceMeasurementKey.THRESHOLD, config.rsi_oversold)
        rsi_bounds = EvidenceMeasurement(
            key=EvidenceMeasurementKey.THRESHOLD,
            value=(config.rsi_oversold, config.rsi_overbought),
        )
        zscore_high = cls._measurement(EvidenceMeasurementKey.THRESHOLD, config.volume_zscore_high)
        zscore_low = cls._measurement(EvidenceMeasurementKey.THRESHOLD, config.volume_zscore_low)

        evidence = [
            cls._evaluate(
                MarketEvidenceKey.PRICE_ABOVE_SMA,
                (price_sma,),
                (close,),
                lambda values: bar.close > values[0],
            ),
            cls._evaluate(
                MarketEvidenceKey.PRICE_BELOW_SMA,
                (price_sma,),
                (close,),
                lambda values: bar.close < values[0],
            ),
            cls._evaluate(
                MarketEvidenceKey.EMA_ABOVE_SMA,
                cls._canonical_sources((trend_ema, trend_sma)),
                (),
                lambda values: values[1] > values[0],
            ),
            cls._evaluate(
                MarketEvidenceKey.EMA_BELOW_SMA,
                cls._canonical_sources((trend_ema, trend_sma)),
                (),
                lambda values: values[1] < values[0],
            ),
            cls._evaluate(
                MarketEvidenceKey.POSITIVE_RETURN,
                (arithmetic_return,),
                (),
                lambda values: values[0] > Decimal(0),
            ),
            cls._evaluate(
                MarketEvidenceKey.NEGATIVE_RETURN,
                (arithmetic_return,),
                (),
                lambda values: values[0] < Decimal(0),
            ),
            cls._evaluate(
                MarketEvidenceKey.RSI_OVERBOUGHT,
                (rsi,),
                (overbought,),
                lambda values: values[0] >= config.rsi_overbought,
            ),
            cls._evaluate(
                MarketEvidenceKey.RSI_OVERSOLD,
                (rsi,),
                (oversold,),
                lambda values: values[0] <= config.rsi_oversold,
            ),
            cls._evaluate(
                MarketEvidenceKey.RSI_MIDRANGE,
                (rsi,),
                (rsi_bounds,),
                lambda values: config.rsi_oversold < values[0] < config.rsi_overbought,
            ),
        ]

        if index == 0:
            evidence.extend(
                (
                    cls._first_extreme(MarketEvidenceKey.CLOSE_BREAKOUT_ABOVE_PRIOR_HIGH, close),
                    cls._first_extreme(MarketEvidenceKey.CLOSE_BREAKDOWN_BELOW_PRIOR_LOW, close),
                )
            )
        else:
            prior_snapshot = history.technical_snapshots[index - 1]
            prior_high = cls._source(
                prior_snapshot,
                cls._require_feature(
                    indexes[index - 1],
                    (TechnicalFeatureKey.ROLLING_HIGHEST_HIGH, config.breakout_high_period),
                    index - 1,
                ),
            )
            prior_low = cls._source(
                prior_snapshot,
                cls._require_feature(
                    indexes[index - 1],
                    (TechnicalFeatureKey.ROLLING_LOWEST_LOW, config.breakdown_low_period),
                    index - 1,
                ),
            )
            evidence.extend(
                (
                    cls._evaluate(
                        MarketEvidenceKey.CLOSE_BREAKOUT_ABOVE_PRIOR_HIGH,
                        (prior_high,),
                        (close,),
                        lambda values: bar.close > values[0],
                    ),
                    cls._evaluate(
                        MarketEvidenceKey.CLOSE_BREAKDOWN_BELOW_PRIOR_LOW,
                        (prior_low,),
                        (close,),
                        lambda values: bar.close < values[0],
                    ),
                )
            )

        evidence.extend(
            (
                cls._evaluate(
                    MarketEvidenceKey.VOLUME_ABOVE_MEAN,
                    (volume_mean,),
                    (volume,),
                    lambda values: bar.volume > values[0],
                ),
                cls._evaluate(
                    MarketEvidenceKey.VOLUME_ZSCORE_HIGH,
                    (volume_zscore,),
                    (zscore_high,),
                    lambda values: values[0] >= config.volume_zscore_high,
                ),
                cls._evaluate(
                    MarketEvidenceKey.VOLUME_ZSCORE_LOW,
                    (volume_zscore,),
                    (zscore_low,),
                    lambda values: values[0] <= config.volume_zscore_low,
                ),
            )
        )
        return EvidenceSnapshot(
            instrument=bar.instrument,
            timeframe=bar.timeframe,
            timestamp=bar.timestamp,
            evidence=tuple(evidence),
        )

    @staticmethod
    def _source(
        snapshot: TechnicalSnapshot,
        feature: TechnicalFeature,
    ) -> EvidenceFeatureSource:
        return EvidenceFeatureSource(
            timestamp=snapshot.timestamp,
            key=feature.key,
            period=feature.period,
            status=feature.status,
            value=feature.value,
        )

    @staticmethod
    def _measurement(
        key: EvidenceMeasurementKey,
        value: Decimal,
    ) -> EvidenceMeasurement:
        return EvidenceMeasurement(key=key, value=value)

    @staticmethod
    def _canonical_sources(
        sources: tuple[EvidenceFeatureSource, ...],
    ) -> tuple[EvidenceFeatureSource, ...]:
        technical_order = {
            key: position
            for position, key in enumerate(
                (
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
            )
        }
        return tuple(
            sorted(
                sources,
                key=lambda source: (
                    source.timestamp,
                    technical_order[source.key],
                    source.period,
                ),
            )
        )

    @staticmethod
    def _evaluate(
        key: MarketEvidenceKey,
        sources: tuple[EvidenceFeatureSource, ...],
        measurements: tuple[EvidenceMeasurement, ...],
        predicate: _Predicate,
    ) -> MarketEvidence:
        statuses = tuple(source.status for source in sources)
        if TechnicalFeatureStatus.WARMING_UP in statuses:
            status = MarketEvidenceStatus.WARMING_UP
        elif TechnicalFeatureStatus.UNDEFINED in statuses:
            status = MarketEvidenceStatus.UNDEFINED
        else:
            values = tuple(source.value for source in sources)
            if any(value is None for value in values):
                raise MarketEvidenceComputationError(
                    "available feature source unexpectedly lacks a value"
                )
            decimal_values = tuple(value for value in values if value is not None)
            status = (
                MarketEvidenceStatus.ACTIVE
                if predicate(decimal_values)
                else MarketEvidenceStatus.INACTIVE
            )
        return MarketEvidence(
            key=key,
            status=status,
            feature_sources=sources,
            measurements=measurements,
        )

    @staticmethod
    def _first_extreme(
        key: MarketEvidenceKey,
        close: EvidenceMeasurement,
    ) -> MarketEvidence:
        return MarketEvidence(
            key=key,
            status=MarketEvidenceStatus.WARMING_UP,
            feature_sources=(),
            measurements=(close,),
        )
