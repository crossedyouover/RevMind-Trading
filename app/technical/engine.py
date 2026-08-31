"""Stateless Decimal reference engine for deterministic technical evidence."""

from collections import deque
from collections.abc import Sequence
from decimal import ROUND_HALF_EVEN, Context, Decimal, DecimalException, localcontext
from typing import Protocol

from app.core.schemas import MarketBar
from app.technical.models import (
    TECHNICAL_FEATURE_KEY_ORDER,
    TechnicalAnalysisConfig,
    TechnicalFeature,
    TechnicalFeatureKey,
    TechnicalFeatureStatus,
    TechnicalSnapshot,
)

_CALCULATION_CONTEXT = Context(
    prec=50, rounding=ROUND_HALF_EVEN, Emin=-999_999, Emax=999_999
)
_ZERO = Decimal(0)
_ONE = Decimal(1)
_FIFTY = Decimal(50)
_HUNDRED = Decimal(100)

type _Result = tuple[TechnicalFeatureStatus, Decimal | None]
type _Series = list[_Result]


class TechnicalAnalysisError(Exception):
    """Base error for deterministic technical analysis."""


class TechnicalAnalysisInvalidInputError(TechnicalAnalysisError):
    """Raised when supplied canonical bar history is inconsistent."""


class TechnicalAnalysisConfigurationError(TechnicalAnalysisError):
    """Raised when the engine receives an invalid configuration object."""


class TechnicalAnalysisComputationError(TechnicalAnalysisError):
    """Raised when a trustworthy finite Decimal result cannot be produced."""


class TechnicalAnalysisEngine(Protocol):
    """Narrow batch boundary for deterministic technical calculations."""

    def analyze(
        self, bars: Sequence[MarketBar], config: TechnicalAnalysisConfig
    ) -> tuple[TechnicalSnapshot, ...]:
        """Return one aligned immutable snapshot for every supplied bar."""
        ...


class DeterministicTechnicalAnalysisEngine:
    """Pure, reusable Phase 7 reference implementation."""

    def analyze(
        self, bars: Sequence[MarketBar], config: TechnicalAnalysisConfig
    ) -> tuple[TechnicalSnapshot, ...]:
        """Calculate configured features without sorting, repair, or future access."""
        if not isinstance(config, TechnicalAnalysisConfig):
            raise TechnicalAnalysisConfigurationError(
                "config must be a validated TechnicalAnalysisConfig"
            )
        if not isinstance(bars, Sequence):
            raise TechnicalAnalysisInvalidInputError("bars must be a sequence of MarketBar")
        history = tuple(bars)
        self._validate_history(history)
        if not history:
            return ()
        try:
            with localcontext(_CALCULATION_CONTEXT):
                calculated = self._calculate(history, config)
                return self._snapshots(history, config, calculated)
        except DecimalException as exc:
            raise TechnicalAnalysisComputationError(
                "technical calculation could not produce a finite Decimal result"
            ) from exc

    @staticmethod
    def _validate_history(bars: tuple[object, ...]) -> None:
        if not bars:
            return
        if not all(isinstance(bar, MarketBar) for bar in bars):
            raise TechnicalAnalysisInvalidInputError("history must contain only MarketBar")
        typed_bars = tuple(bar for bar in bars if isinstance(bar, MarketBar))
        first = typed_bars[0]
        previous = first.timestamp
        for bar in typed_bars[1:]:
            if bar.instrument != first.instrument:
                raise TechnicalAnalysisInvalidInputError("history contains mixed instruments")
            if bar.timeframe != first.timeframe:
                raise TechnicalAnalysisInvalidInputError("history contains mixed timeframes")
            if bar.timestamp <= previous:
                if bar.timestamp == previous:
                    raise TechnicalAnalysisInvalidInputError(
                        "history contains duplicate timestamps"
                    )
                raise TechnicalAnalysisInvalidInputError(
                    "history timestamps must be strictly increasing"
                )
            previous = bar.timestamp

    def _calculate(
        self, bars: tuple[MarketBar, ...], config: TechnicalAnalysisConfig
    ) -> dict[tuple[TechnicalFeatureKey, int], _Series]:
        closes = tuple(bar.close for bar in bars)
        highs = tuple(bar.high for bar in bars)
        lows = tuple(bar.low for bar in bars)
        volumes = tuple(bar.volume for bar in bars)
        results: dict[tuple[TechnicalFeatureKey, int], _Series] = {}
        for period in config.sma_periods:
            results[(TechnicalFeatureKey.SMA_CLOSE, period)] = self._sma(closes, period)
        for period in config.ema_periods:
            results[(TechnicalFeatureKey.EMA_CLOSE, period)] = self._ema(closes, period)
        for period in config.rsi_periods:
            results[(TechnicalFeatureKey.RSI_CLOSE_WILDER, period)] = self._rsi(closes, period)
        true_ranges = self._true_ranges(bars)
        for period in config.atr_periods:
            results[(TechnicalFeatureKey.ATR_WILDER, period)] = self._wilder_average(
                true_ranges, period
            )
        for period in config.rolling_high_periods:
            results[(TechnicalFeatureKey.ROLLING_HIGHEST_HIGH, period)] = self._rolling_extreme(
                highs, period, highest=True
            )
        for period in config.rolling_low_periods:
            results[(TechnicalFeatureKey.ROLLING_LOWEST_LOW, period)] = self._rolling_extreme(
                lows, period, highest=False
            )
        for period in config.return_periods:
            results[(TechnicalFeatureKey.ARITHMETIC_RETURN, period)] = self._returns(
                closes, period
            )
        for period in config.volume_mean_periods:
            results[(TechnicalFeatureKey.VOLUME_MEAN, period)] = self._sma(volumes, period)
        for period in config.volume_stddev_periods:
            results[(TechnicalFeatureKey.VOLUME_STDDEV_POPULATION, period)] = (
                self._volume_stddev(volumes, period)
            )
        for period in config.volume_zscore_periods:
            results[(TechnicalFeatureKey.VOLUME_ZSCORE, period)] = self._volume_zscore(
                volumes, period
            )
        return results

    @staticmethod
    def _warming(length: int) -> _Series:
        return [(TechnicalFeatureStatus.WARMING_UP, None) for _ in range(length)]

    @staticmethod
    def _available(value: Decimal) -> _Result:
        if not value.is_finite():
            raise TechnicalAnalysisComputationError(
                "technical calculation produced a non-finite Decimal"
            )
        return (TechnicalFeatureStatus.AVAILABLE, value)

    def _sma(self, values: tuple[Decimal, ...], period: int) -> _Series:
        output = self._warming(len(values))
        rolling_sum = _ZERO
        divisor = Decimal(period)
        for index, value in enumerate(values):
            rolling_sum += value
            if index >= period:
                rolling_sum -= values[index - period]
            if index >= period - 1:
                output[index] = self._available(rolling_sum / divisor)
        return output

    def _ema(self, values: tuple[Decimal, ...], period: int) -> _Series:
        output = self._warming(len(values))
        seed_index = period - 1
        if seed_index >= len(values):
            return output
        seed = sum(values[:period], start=_ZERO) / Decimal(period)
        output[seed_index] = self._available(seed)
        alpha = Decimal(2) / Decimal(period + 1)
        previous = seed
        for index in range(period, len(values)):
            previous = alpha * values[index] + (_ONE - alpha) * previous
            output[index] = self._available(previous)
        return output

    def _rsi(self, closes: tuple[Decimal, ...], period: int) -> _Series:
        output = self._warming(len(closes))
        if len(closes) <= period:
            return output
        gains: list[Decimal] = []
        losses: list[Decimal] = []
        for previous, current in zip(closes, closes[1:], strict=False):
            change = current - previous
            gains.append(max(change, _ZERO))
            losses.append(max(-change, _ZERO))
        divisor = Decimal(period)
        average_gain = sum(gains[:period], start=_ZERO) / divisor
        average_loss = sum(losses[:period], start=_ZERO) / divisor
        output[period] = self._available(self._rsi_value(average_gain, average_loss))
        for index in range(period + 1, len(closes)):
            delta_index = index - 1
            average_gain = (
                average_gain * Decimal(period - 1) + gains[delta_index]
            ) / divisor
            average_loss = (
                average_loss * Decimal(period - 1) + losses[delta_index]
            ) / divisor
            output[index] = self._available(self._rsi_value(average_gain, average_loss))
        return output

    @staticmethod
    def _rsi_value(average_gain: Decimal, average_loss: Decimal) -> Decimal:
        if average_gain == _ZERO and average_loss == _ZERO:
            return _FIFTY
        if average_loss == _ZERO:
            return _HUNDRED
        if average_gain == _ZERO:
            return _ZERO
        relative_strength = average_gain / average_loss
        return _HUNDRED - _HUNDRED / (_ONE + relative_strength)

    @staticmethod
    def _true_ranges(bars: tuple[MarketBar, ...]) -> tuple[Decimal, ...]:
        ranges: list[Decimal] = []
        for index, bar in enumerate(bars):
            if index == 0:
                ranges.append(bar.high - bar.low)
            else:
                previous_close = bars[index - 1].close
                ranges.append(
                    max(
                        bar.high - bar.low,
                        abs(bar.high - previous_close),
                        abs(bar.low - previous_close),
                    )
                )
        return tuple(ranges)

    def _wilder_average(self, values: tuple[Decimal, ...], period: int) -> _Series:
        output = self._warming(len(values))
        seed_index = period - 1
        if seed_index >= len(values):
            return output
        divisor = Decimal(period)
        previous = sum(values[:period], start=_ZERO) / divisor
        output[seed_index] = self._available(previous)
        for index in range(period, len(values)):
            previous = (previous * Decimal(period - 1) + values[index]) / divisor
            output[index] = self._available(previous)
        return output

    def _rolling_extreme(
        self, values: tuple[Decimal, ...], period: int, *, highest: bool
    ) -> _Series:
        output = self._warming(len(values))
        candidates: deque[int] = deque()
        for index, value in enumerate(values):
            while candidates and candidates[0] <= index - period:
                candidates.popleft()
            while candidates and (
                values[candidates[-1]] <= value
                if highest
                else values[candidates[-1]] >= value
            ):
                candidates.pop()
            candidates.append(index)
            if index >= period - 1:
                output[index] = self._available(values[candidates[0]])
        return output

    def _returns(self, closes: tuple[Decimal, ...], period: int) -> _Series:
        output = self._warming(len(closes))
        for index in range(period, len(closes)):
            denominator = closes[index - period]
            if denominator == _ZERO:
                output[index] = (TechnicalFeatureStatus.UNDEFINED, None)
            else:
                output[index] = self._available(closes[index] / denominator - _ONE)
        return output

    def _volume_stddev(self, volumes: tuple[Decimal, ...], period: int) -> _Series:
        output = self._warming(len(volumes))
        divisor = Decimal(period)
        for index in range(period - 1, len(volumes)):
            window = volumes[index - period + 1 : index + 1]
            mean = sum(window, start=_ZERO) / divisor
            variance = sum(((value - mean) ** 2 for value in window), start=_ZERO) / divisor
            output[index] = self._available(variance.sqrt())
        return output

    def _volume_zscore(self, volumes: tuple[Decimal, ...], period: int) -> _Series:
        output = self._warming(len(volumes))
        divisor = Decimal(period)
        for index in range(period - 1, len(volumes)):
            window = volumes[index - period + 1 : index + 1]
            mean = sum(window, start=_ZERO) / divisor
            variance = sum(((value - mean) ** 2 for value in window), start=_ZERO) / divisor
            standard_deviation = variance.sqrt()
            if standard_deviation == _ZERO:
                output[index] = (TechnicalFeatureStatus.UNDEFINED, None)
            else:
                output[index] = self._available(
                    (volumes[index] - mean) / standard_deviation
                )
        return output

    @staticmethod
    def _configured_periods(
        config: TechnicalAnalysisConfig,
    ) -> tuple[tuple[TechnicalFeatureKey, tuple[int, ...]], ...]:
        periods = {
            TechnicalFeatureKey.SMA_CLOSE: config.sma_periods,
            TechnicalFeatureKey.EMA_CLOSE: config.ema_periods,
            TechnicalFeatureKey.RSI_CLOSE_WILDER: config.rsi_periods,
            TechnicalFeatureKey.ATR_WILDER: config.atr_periods,
            TechnicalFeatureKey.ROLLING_HIGHEST_HIGH: config.rolling_high_periods,
            TechnicalFeatureKey.ROLLING_LOWEST_LOW: config.rolling_low_periods,
            TechnicalFeatureKey.ARITHMETIC_RETURN: config.return_periods,
            TechnicalFeatureKey.VOLUME_MEAN: config.volume_mean_periods,
            TechnicalFeatureKey.VOLUME_STDDEV_POPULATION: config.volume_stddev_periods,
            TechnicalFeatureKey.VOLUME_ZSCORE: config.volume_zscore_periods,
        }
        return tuple((key, periods[key]) for key in TECHNICAL_FEATURE_KEY_ORDER)

    def _snapshots(
        self,
        bars: tuple[MarketBar, ...],
        config: TechnicalAnalysisConfig,
        calculated: dict[tuple[TechnicalFeatureKey, int], _Series],
    ) -> tuple[TechnicalSnapshot, ...]:
        snapshots: list[TechnicalSnapshot] = []
        configured = self._configured_periods(config)
        for index, bar in enumerate(bars):
            features: list[TechnicalFeature] = []
            for key, periods in configured:
                for period in periods:
                    status, value = calculated[(key, period)][index]
                    features.append(
                        TechnicalFeature(
                            key=key, period=period, status=status, value=value
                        )
                    )
            snapshots.append(
                TechnicalSnapshot(
                    instrument=bar.instrument,
                    timeframe=bar.timeframe,
                    timestamp=bar.timestamp,
                    features=tuple(features),
                )
            )
        return tuple(snapshots)
