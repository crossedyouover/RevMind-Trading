"""Pure deterministic trend evidence over PIT-prepared canonical bars."""

from typing import Protocol

from pydantic import ValidationError

from app.regime.models import (
    TrendRegimeRequest,
    TrendRegimeResult,
    TrendRegimeSnapshot,
    _derive,
)
from app.technical.engine import (
    DeterministicTechnicalAnalysisEngine,
    TechnicalAnalysisEngine,
    TechnicalAnalysisError,
)
from app.technical.models import (
    TechnicalAnalysisConfig,
    TechnicalFeatureKey,
    TechnicalSnapshot,
)


class TrendRegimeError(Exception):
    """Base deterministic trend-evidence failure."""


class TrendRegimeInvalidInputError(TrendRegimeError):
    """PIT input or explicit configuration cannot be trusted."""


class TrendRegimeComputationError(TrendRegimeError):
    """The technical stage failed or supplied inconsistent operands."""


class TrendRegimeEngine(Protocol):
    def analyze(self, request: TrendRegimeRequest) -> TrendRegimeResult: ...


class DeterministicTrendRegimeEngine:
    """Compose one frozen technical call and exact sign comparisons, with no side effects."""

    def __init__(self, technical_engine: TechnicalAnalysisEngine | None = None) -> None:
        self._technical = (
            technical_engine
            if technical_engine is not None
            else DeterministicTechnicalAnalysisEngine()
        )

    def analyze(self, request: TrendRegimeRequest) -> TrendRegimeResult:
        if not isinstance(request, TrendRegimeRequest):
            raise TrendRegimeInvalidInputError("request must be a TrendRegimeRequest")
        try:
            trusted = TrendRegimeRequest(
                history=request.history, config=request.config, evaluation_at=request.evaluation_at
            )
        except (ValidationError, AttributeError, TypeError, ValueError) as exc:
            raise TrendRegimeInvalidInputError("request must be canonical and PIT-safe") from exc
        try:
            config = TechnicalAnalysisConfig(
                sma_periods=(trusted.config.sma_period,),
                return_periods=(trusted.config.return_period,),
                ema_periods=(),
                rsi_periods=(),
                atr_periods=(),
                rolling_high_periods=(),
                rolling_low_periods=(),
                volume_mean_periods=(),
                volume_stddev_periods=(),
                volume_zscore_periods=(),
            )
            bars = tuple(item.bar for item in trusted.history.bars)
            technical = self._technical.analyze(bars, config)
            if not isinstance(technical, tuple) or len(technical) != len(bars):
                raise ValueError("technical stage must return one immutable snapshot per bar")
            snapshots = []
            expected_keys = (
                (TechnicalFeatureKey.SMA_CLOSE, trusted.config.sma_period),
                (TechnicalFeatureKey.ARITHMETIC_RETURN, trusted.config.return_period),
            )
            for observation, supplied in zip(trusted.history.bars, technical, strict=True):
                if not isinstance(supplied, TechnicalSnapshot):
                    raise ValueError("technical stage must return canonical snapshot objects")
                snapshot = TechnicalSnapshot.model_validate(
                    supplied.model_dump(mode="python", round_trip=True, warnings="none")
                )
                bar = observation.bar
                if (
                    snapshot.instrument != bar.instrument
                    or snapshot.timeframe is not bar.timeframe
                    or snapshot.timestamp != bar.timestamp
                ):
                    raise ValueError("technical stage is not aligned to input bars")
                if (
                    tuple((feature.key, feature.period) for feature in snapshot.features)
                    != expected_keys
                ):
                    raise ValueError("technical stage must return exactly the configured operands")
                sma, arithmetic_return = snapshot.features
                status, regime = _derive(observation, sma, arithmetic_return)
                snapshots.append(
                    TrendRegimeSnapshot(
                        observation=observation,
                        sma=sma,
                        arithmetic_return=arithmetic_return,
                        status=status,
                        regime=regime,
                    )
                )
            return TrendRegimeResult(request=trusted, snapshots=tuple(snapshots))
        except (
            TechnicalAnalysisError,
            ValidationError,
            AttributeError,
            TypeError,
            ValueError,
        ) as exc:
            raise TrendRegimeComputationError("trend evidence stage failed") from exc
