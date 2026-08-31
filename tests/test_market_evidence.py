"""Adversarial Phase 8 deterministic market-evidence tests."""

import inspect
import socket
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal, localcontext
from typing import cast

import pytest
from pydantic import ValidationError

import app.evidence.engine as evidence_engine_module
from app.core.schemas import AssetClass, Instrument, MarketBar, Timeframe
from app.evidence import (
    MARKET_EVIDENCE_KEY_ORDER,
    AlignedTechnicalHistory,
    DeterministicMarketEvidenceEngine,
    EvidenceFeatureSource,
    EvidenceMeasurement,
    EvidenceMeasurementKey,
    EvidenceSnapshot,
    MarketEvidence,
    MarketEvidenceConfig,
    MarketEvidenceConfigurationError,
    MarketEvidenceInvalidInputError,
    MarketEvidenceKey,
    MarketEvidenceStatus,
)
from app.technical import (
    DeterministicTechnicalAnalysisEngine,
    TechnicalAnalysisConfig,
    TechnicalFeature,
    TechnicalFeatureKey,
    TechnicalFeatureStatus,
    TechnicalSnapshot,
)

_START = datetime(2025, 1, 1, tzinfo=UTC)
_INSTRUMENT = Instrument(symbol="NVDA", asset_class=AssetClass.EQUITY, exchange="XNAS")


def _bar(
    index: int,
    close: str,
    *,
    high: str | None = None,
    low: str | None = None,
    volume: str = "100",
    instrument: Instrument = _INSTRUMENT,
    timeframe: Timeframe = Timeframe.ONE_DAY,
) -> MarketBar:
    close_value = Decimal(close)
    high_value = Decimal(high) if high is not None else close_value
    low_value = Decimal(low) if low is not None else close_value
    return MarketBar(
        instrument=instrument,
        timeframe=timeframe,
        timestamp=_START + timedelta(days=index),
        open=close_value,
        high=high_value,
        low=low_value,
        close=close_value,
        volume=Decimal(volume),
    )


def _technical_config() -> TechnicalAnalysisConfig:
    return TechnicalAnalysisConfig(
        sma_periods=(1, 2, 20),
        ema_periods=(1, 2, 20),
        rsi_periods=(1, 2, 14),
        atr_periods=(),
        rolling_high_periods=(1, 2, 20),
        rolling_low_periods=(1, 2, 20),
        return_periods=(1, 2),
        volume_mean_periods=(1, 2, 20),
        volume_stddev_periods=(),
        volume_zscore_periods=(1, 2, 20),
    )


def _history(bars: list[MarketBar]) -> AlignedTechnicalHistory:
    snapshots = DeterministicTechnicalAnalysisEngine().analyze(bars, _technical_config())
    return AlignedTechnicalHistory(bars=tuple(bars), technical_snapshots=snapshots)


def _config(**changes: object) -> MarketEvidenceConfig:
    values: dict[str, object] = {
        "price_sma_period": 2,
        "trend_ema_period": 2,
        "trend_sma_period": 2,
        "return_period": 1,
        "rsi_period": 2,
        "breakout_high_period": 2,
        "breakdown_low_period": 2,
        "volume_mean_period": 2,
        "volume_zscore_period": 2,
    }
    values.update(changes)
    return MarketEvidenceConfig.model_validate(values)


def _evidence(snapshot: EvidenceSnapshot, key: MarketEvidenceKey) -> MarketEvidence:
    return next(item for item in snapshot.evidence if item.key is key)


def _feature(
    key: TechnicalFeatureKey,
    value: str | None,
    *,
    period: int = 1,
    status: TechnicalFeatureStatus = TechnicalFeatureStatus.AVAILABLE,
) -> TechnicalFeature:
    return TechnicalFeature(
        key=key,
        period=period,
        status=status,
        value=None if value is None else Decimal(value),
    )


def _manual_history(
    *,
    close: str = "10",
    volume: str = "100",
    sma: str = "10",
    ema: str = "10",
    return_value: str = "0",
    rsi: str = "50",
    high: str = "10",
    low: str = "10",
    mean: str = "100",
    zscore: str | None = "0",
    zscore_status: TechnicalFeatureStatus = TechnicalFeatureStatus.AVAILABLE,
) -> AlignedTechnicalHistory:
    bars = (_bar(0, close, volume=volume), _bar(1, close, volume=volume))
    snapshots = []
    for bar in bars:
        features = (
            _feature(TechnicalFeatureKey.SMA_CLOSE, sma),
            _feature(TechnicalFeatureKey.EMA_CLOSE, ema),
            _feature(TechnicalFeatureKey.RSI_CLOSE_WILDER, rsi),
            _feature(TechnicalFeatureKey.ROLLING_HIGHEST_HIGH, high),
            _feature(TechnicalFeatureKey.ROLLING_LOWEST_LOW, low),
            _feature(TechnicalFeatureKey.ARITHMETIC_RETURN, return_value),
            _feature(TechnicalFeatureKey.VOLUME_MEAN, mean),
            _feature(
                TechnicalFeatureKey.VOLUME_ZSCORE,
                zscore,
                status=zscore_status,
            ),
        )
        snapshots.append(
            TechnicalSnapshot(
                instrument=bar.instrument,
                timeframe=bar.timeframe,
                timestamp=bar.timestamp,
                features=features,
            )
        )
    return AlignedTechnicalHistory(bars=bars, technical_snapshots=tuple(snapshots))


def test_alignment_empty_single_gaps_and_immutability() -> None:
    assert AlignedTechnicalHistory(bars=(), technical_snapshots=()).bars == ()
    single = _history([_bar(0, "1")])
    assert len(single.bars) == 1
    gapped = _history([_bar(0, "1"), _bar(10, "2")])
    assert len(gapped.bars) == 2
    with pytest.raises(ValidationError):
        gapped.bars = ()
    with pytest.raises(ValidationError):
        AlignedTechnicalHistory.model_validate(
            {"bars": list(gapped.bars), "technical_snapshots": gapped.technical_snapshots}
        )


def test_alignment_rejects_count_timestamp_order_and_reordered_snapshots() -> None:
    history = _history([_bar(0, "1"), _bar(1, "2")])
    with pytest.raises(ValidationError, match="counts"):
        AlignedTechnicalHistory(
            bars=history.bars, technical_snapshots=history.technical_snapshots[:1]
        )
    with pytest.raises(ValidationError, match="timestamps must match"):
        AlignedTechnicalHistory(
            bars=history.bars,
            technical_snapshots=tuple(reversed(history.technical_snapshots)),
        )
    duplicate_bars = (history.bars[0], history.bars[0])
    duplicate_snapshots = (history.technical_snapshots[0], history.technical_snapshots[0])
    with pytest.raises(ValidationError, match="duplicate"):
        AlignedTechnicalHistory(bars=duplicate_bars, technical_snapshots=duplicate_snapshots)
    with pytest.raises(ValidationError, match="strictly increasing"):
        AlignedTechnicalHistory(
            bars=tuple(reversed(history.bars)),
            technical_snapshots=tuple(reversed(history.technical_snapshots)),
        )
    mismatched_snapshot = history.technical_snapshots[0].model_copy(
        update={
            "instrument": Instrument(
                symbol="NVDA", asset_class=AssetClass.EQUITY, exchange="XNYS"
            )
        }
    )
    with pytest.raises(ValidationError, match="instruments must match"):
        AlignedTechnicalHistory(
            bars=(history.bars[0],), technical_snapshots=(mismatched_snapshot,)
        )


@pytest.mark.parametrize(
    ("instrument", "timeframe", "message"),
    (
        (
            Instrument(symbol="NVDA", asset_class=AssetClass.EQUITY, exchange="XNYS"),
            Timeframe.ONE_DAY,
            "instrument",
        ),
        (
            Instrument(symbol="NVDA", asset_class=AssetClass.CRYPTO, exchange="XNAS"),
            Timeframe.ONE_DAY,
            "instrument",
        ),
        (_INSTRUMENT, Timeframe.ONE_HOUR, "timeframe"),
    ),
)
def test_alignment_rejects_identity_and_timeframe_mismatch(
    instrument: Instrument, timeframe: Timeframe, message: str
) -> None:
    first = _history([_bar(0, "1")])
    other = _history([_bar(1, "2", instrument=instrument, timeframe=timeframe)])
    with pytest.raises(ValidationError, match=message):
        AlignedTechnicalHistory(
            bars=(first.bars[0], other.bars[0]),
            technical_snapshots=(first.technical_snapshots[0], other.technical_snapshots[0]),
        )


def test_config_defaults_strict_periods_thresholds_relations_and_immutability() -> None:
    assert MarketEvidenceConfig() == MarketEvidenceConfig(
        price_sma_period=20,
        trend_ema_period=20,
        trend_sma_period=20,
        return_period=1,
        rsi_period=14,
        breakout_high_period=20,
        breakdown_low_period=20,
        volume_mean_period=20,
        volume_zscore_period=20,
        rsi_overbought=Decimal("70"),
        rsi_oversold=Decimal("30"),
        volume_zscore_high=Decimal("2"),
        volume_zscore_low=Decimal("-2"),
    )
    for invalid in (True, 1.0, "1", Decimal(1), 0, -1, 100_001):
        with pytest.raises(ValidationError):
            MarketEvidenceConfig(price_sma_period=cast(int, invalid))
    for invalid in (True, 1.0, "1", Decimal("NaN"), Decimal("Infinity")):
        with pytest.raises(ValidationError):
            MarketEvidenceConfig(rsi_overbought=cast(Decimal, invalid))
    for values in (
        {"rsi_oversold": Decimal("70"), "rsi_overbought": Decimal("70")},
        {"rsi_oversold": Decimal("-1")},
        {"rsi_overbought": Decimal("101")},
        {"volume_zscore_low": Decimal("0")},
        {"volume_zscore_high": Decimal("0")},
    ):
        with pytest.raises(ValidationError):
            MarketEvidenceConfig.model_validate(values)
    config = MarketEvidenceConfig()
    assert MarketEvidenceConfig.model_validate_json(config.model_dump_json()) == config
    assert MarketEvidenceConfig(
        rsi_oversold=Decimal("-0"), rsi_overbought=Decimal("1E+2")
    ).rsi_oversold == 0
    assert MarketEvidenceConfig(
        volume_zscore_low=Decimal("-1E-999"),
        volume_zscore_high=Decimal("1E+999"),
    ).volume_zscore_high == Decimal("1E+999")
    with pytest.raises(ValidationError):
        MarketEvidenceConfig.model_validate_json('{"rsi_overbought":70.0}')
    with pytest.raises(ValidationError):
        config.rsi_period = 2
    with pytest.raises(ValidationError):
        MarketEvidenceConfig.model_validate({"unexpected": 1})


def test_empty_history_and_invalid_engine_boundary() -> None:
    engine = DeterministicMarketEvidenceEngine()
    assert engine.analyze(AlignedTechnicalHistory(bars=(), technical_snapshots=()), _config()) == ()
    with pytest.raises(MarketEvidenceConfigurationError):
        engine.analyze(
            AlignedTechnicalHistory(bars=(), technical_snapshots=()),
            cast(MarketEvidenceConfig, object()),
        )
    with pytest.raises(MarketEvidenceInvalidInputError):
        engine.analyze(cast(AlignedTechnicalHistory, object()), _config())
    copied_config = _config().model_copy(update={"price_sma_period": 0})
    with pytest.raises(MarketEvidenceConfigurationError):
        engine.analyze(AlignedTechnicalHistory(bars=(), technical_snapshots=()), copied_config)
    valid_history = _history([_bar(0, "1"), _bar(1, "2")])
    copied_history = valid_history.model_copy(
        update={"bars": (valid_history.bars[0], valid_history.bars[0])}
    )
    with pytest.raises(MarketEvidenceInvalidInputError):
        engine.analyze(copied_history, _config())


def test_exact_feature_lookup_extra_features_and_no_period_substitution() -> None:
    history = _history([_bar(0, "1"), _bar(1, "2")])
    result = DeterministicMarketEvidenceEngine().analyze(history, _config())
    assert len(result) == 2
    with pytest.raises(MarketEvidenceInvalidInputError, match="SMA_CLOSE/3"):
        DeterministicMarketEvidenceEngine().analyze(history, _config(price_sma_period=3))
    with pytest.raises(MarketEvidenceInvalidInputError, match="VOLUME_ZSCORE/3"):
        DeterministicMarketEvidenceEngine().analyze(history, _config(volume_zscore_period=3))
    with pytest.raises(MarketEvidenceInvalidInputError, match="ROLLING_HIGHEST_HIGH/3"):
        DeterministicMarketEvidenceEngine().analyze(history, _config(breakout_high_period=3))
    without_sma = history.technical_snapshots[0].model_copy(
        update={
            "features": tuple(
                feature
                for feature in history.technical_snapshots[0].features
                if feature.key is not TechnicalFeatureKey.SMA_CLOSE
            )
        }
    )
    missing_key = AlignedTechnicalHistory(
        bars=history.bars,
        technical_snapshots=(without_sma, history.technical_snapshots[1]),
    )
    with pytest.raises(MarketEvidenceInvalidInputError, match="SMA_CLOSE"):
        DeterministicMarketEvidenceEngine().analyze(missing_key, _config())


def test_known_price_trend_return_and_equality_answers() -> None:
    engine = DeterministicMarketEvidenceEngine()
    config = _config(
        price_sma_period=1,
        trend_ema_period=1,
        trend_sma_period=1,
        return_period=1,
        rsi_period=1,
        breakout_high_period=1,
        breakdown_low_period=1,
        volume_mean_period=1,
        volume_zscore_period=1,
    )
    scenarios = (
        ("11", "10", "12", "1", MarketEvidenceKey.PRICE_ABOVE_SMA, MarketEvidenceStatus.ACTIVE),
        ("9", "10", "8", "-1", MarketEvidenceKey.PRICE_BELOW_SMA, MarketEvidenceStatus.ACTIVE),
        ("10", "10", "11", "0", MarketEvidenceKey.EMA_ABOVE_SMA, MarketEvidenceStatus.ACTIVE),
        ("10", "10", "9", "0", MarketEvidenceKey.EMA_BELOW_SMA, MarketEvidenceStatus.ACTIVE),
        ("10", "10", "10", "1", MarketEvidenceKey.POSITIVE_RETURN, MarketEvidenceStatus.ACTIVE),
        ("10", "10", "10", "-1", MarketEvidenceKey.NEGATIVE_RETURN, MarketEvidenceStatus.ACTIVE),
    )
    for close, sma, ema, ret, key, expected in scenarios:
        output = engine.analyze(
            _manual_history(close=close, sma=sma, ema=ema, return_value=ret), config
        )[-1]
        assert _evidence(output, key).status is expected
    equal = engine.analyze(_manual_history(), config)[-1]
    for key in (
        MarketEvidenceKey.PRICE_ABOVE_SMA,
        MarketEvidenceKey.PRICE_BELOW_SMA,
        MarketEvidenceKey.EMA_ABOVE_SMA,
        MarketEvidenceKey.EMA_BELOW_SMA,
        MarketEvidenceKey.POSITIVE_RETURN,
        MarketEvidenceKey.NEGATIVE_RETURN,
    ):
        assert _evidence(equal, key).status is MarketEvidenceStatus.INACTIVE


@pytest.mark.parametrize(
    ("rsi", "active"),
    (
        ("29", MarketEvidenceKey.RSI_OVERSOLD),
        ("30", MarketEvidenceKey.RSI_OVERSOLD),
        ("50", MarketEvidenceKey.RSI_MIDRANGE),
        ("70", MarketEvidenceKey.RSI_OVERBOUGHT),
        ("71", MarketEvidenceKey.RSI_OVERBOUGHT),
    ),
)
def test_rsi_threshold_boundaries(rsi: str, active: MarketEvidenceKey) -> None:
    result = DeterministicMarketEvidenceEngine().analyze(
        _manual_history(rsi=rsi),
        _config(
            **{
                field: 1
                for field in (
                    "price_sma_period",
                    "trend_ema_period",
                    "trend_sma_period",
                    "return_period",
                    "rsi_period",
                    "breakout_high_period",
                    "breakdown_low_period",
                    "volume_mean_period",
                    "volume_zscore_period",
                )
            }
        ),
    )[-1]
    assert _evidence(result, active).status is MarketEvidenceStatus.ACTIVE


def test_volume_mean_zscore_boundaries_and_undefined() -> None:
    config = _config(
        **{
            field: 1
            for field in (
                "price_sma_period",
                "trend_ema_period",
                "trend_sma_period",
                "return_period",
                "rsi_period",
                "breakout_high_period",
                "breakdown_low_period",
                "volume_mean_period",
                "volume_zscore_period",
            )
        }
    )
    engine = DeterministicMarketEvidenceEngine()
    above = engine.analyze(_manual_history(volume="101", mean="100", zscore="2"), config)[-1]
    assert (
        _evidence(above, MarketEvidenceKey.VOLUME_ABOVE_MEAN).status is MarketEvidenceStatus.ACTIVE
    )
    assert (
        _evidence(above, MarketEvidenceKey.VOLUME_ZSCORE_HIGH).status is MarketEvidenceStatus.ACTIVE
    )
    below = engine.analyze(_manual_history(volume="99", mean="100", zscore="-2"), config)[-1]
    assert (
        _evidence(below, MarketEvidenceKey.VOLUME_ABOVE_MEAN).status
        is MarketEvidenceStatus.INACTIVE
    )
    assert (
        _evidence(below, MarketEvidenceKey.VOLUME_ZSCORE_LOW).status is MarketEvidenceStatus.ACTIVE
    )
    equal = engine.analyze(_manual_history(volume="100", mean="100", zscore="0"), config)[-1]
    assert (
        _evidence(equal, MarketEvidenceKey.VOLUME_ABOVE_MEAN).status
        is MarketEvidenceStatus.INACTIVE
    )
    undefined = engine.analyze(
        _manual_history(zscore=None, zscore_status=TechnicalFeatureStatus.UNDEFINED), config
    )[-1]
    assert (
        _evidence(undefined, MarketEvidenceKey.VOLUME_ZSCORE_HIGH).status
        is MarketEvidenceStatus.UNDEFINED
    )


def test_status_precedence_warming_over_undefined() -> None:
    history = _manual_history()
    snapshot = history.technical_snapshots[-1]
    replaced = []
    for feature in snapshot.features:
        if feature.key is TechnicalFeatureKey.SMA_CLOSE:
            replaced.append(_feature(feature.key, None, status=TechnicalFeatureStatus.UNDEFINED))
        elif feature.key is TechnicalFeatureKey.EMA_CLOSE:
            replaced.append(_feature(feature.key, None, status=TechnicalFeatureStatus.WARMING_UP))
        else:
            replaced.append(feature)
    changed = snapshot.model_copy(update={"features": tuple(replaced)})
    altered = AlignedTechnicalHistory(
        bars=history.bars,
        technical_snapshots=(history.technical_snapshots[0], changed),
    )
    config = _config(
        **{
            field: 1
            for field in (
                "price_sma_period",
                "trend_ema_period",
                "trend_sma_period",
                "return_period",
                "rsi_period",
                "breakout_high_period",
                "breakdown_low_period",
                "volume_mean_period",
                "volume_zscore_period",
            )
        }
    )
    output = DeterministicMarketEvidenceEngine().analyze(altered, config)[-1]
    assert (
        _evidence(output, MarketEvidenceKey.PRICE_ABOVE_SMA).status
        is MarketEvidenceStatus.UNDEFINED
    )
    assert (
        _evidence(output, MarketEvidenceKey.EMA_ABOVE_SMA).status is MarketEvidenceStatus.WARMING_UP
    )


@pytest.mark.parametrize(
    ("feature_key", "evidence_keys", "source_index"),
    (
        (
            TechnicalFeatureKey.SMA_CLOSE,
            (MarketEvidenceKey.PRICE_ABOVE_SMA, MarketEvidenceKey.PRICE_BELOW_SMA),
            1,
        ),
        (
            TechnicalFeatureKey.EMA_CLOSE,
            (MarketEvidenceKey.EMA_ABOVE_SMA, MarketEvidenceKey.EMA_BELOW_SMA),
            1,
        ),
        (
            TechnicalFeatureKey.ARITHMETIC_RETURN,
            (MarketEvidenceKey.POSITIVE_RETURN, MarketEvidenceKey.NEGATIVE_RETURN),
            1,
        ),
        (
            TechnicalFeatureKey.RSI_CLOSE_WILDER,
            (
                MarketEvidenceKey.RSI_OVERBOUGHT,
                MarketEvidenceKey.RSI_OVERSOLD,
                MarketEvidenceKey.RSI_MIDRANGE,
            ),
            1,
        ),
        (
            TechnicalFeatureKey.ROLLING_HIGHEST_HIGH,
            (MarketEvidenceKey.CLOSE_BREAKOUT_ABOVE_PRIOR_HIGH,),
            0,
        ),
        (
            TechnicalFeatureKey.ROLLING_LOWEST_LOW,
            (MarketEvidenceKey.CLOSE_BREAKDOWN_BELOW_PRIOR_LOW,),
            0,
        ),
        (
            TechnicalFeatureKey.VOLUME_MEAN,
            (MarketEvidenceKey.VOLUME_ABOVE_MEAN,),
            1,
        ),
        (
            TechnicalFeatureKey.VOLUME_ZSCORE,
            (MarketEvidenceKey.VOLUME_ZSCORE_HIGH, MarketEvidenceKey.VOLUME_ZSCORE_LOW),
            1,
        ),
    ),
)
@pytest.mark.parametrize(
    ("technical_status", "evidence_status"),
    (
        (TechnicalFeatureStatus.WARMING_UP, MarketEvidenceStatus.WARMING_UP),
        (TechnicalFeatureStatus.UNDEFINED, MarketEvidenceStatus.UNDEFINED),
    ),
)
def test_every_dependency_propagates_unavailable_status(
    feature_key: TechnicalFeatureKey,
    evidence_keys: tuple[MarketEvidenceKey, ...],
    source_index: int,
    technical_status: TechnicalFeatureStatus,
    evidence_status: MarketEvidenceStatus,
) -> None:
    history = _manual_history()
    snapshots = list(history.technical_snapshots)
    target = snapshots[source_index]
    snapshots[source_index] = target.model_copy(
        update={
            "features": tuple(
                feature.model_copy(update={"status": technical_status, "value": None})
                if feature.key is feature_key
                else feature
                for feature in target.features
            )
        }
    )
    altered = AlignedTechnicalHistory(
        bars=history.bars, technical_snapshots=tuple(snapshots)
    )
    config = _config(
        **{
            field: 1
            for field in (
                "price_sma_period",
                "trend_ema_period",
                "trend_sma_period",
                "return_period",
                "rsi_period",
                "breakout_high_period",
                "breakdown_low_period",
                "volume_mean_period",
                "volume_zscore_period",
            )
        }
    )
    output = DeterministicMarketEvidenceEngine().analyze(altered, config)[1]
    for evidence_key in evidence_keys:
        assert _evidence(output, evidence_key).status is evidence_status


def test_breakout_breakdown_use_prior_snapshot_not_current_extreme() -> None:
    config = _config(
        **{
            field: 1
            for field in (
                "price_sma_period",
                "trend_ema_period",
                "trend_sma_period",
                "return_period",
                "rsi_period",
                "breakout_high_period",
                "breakdown_low_period",
                "volume_mean_period",
                "volume_zscore_period",
            )
        }
    )
    engine = DeterministicMarketEvidenceEngine()
    first = engine.analyze(_manual_history(), config)[0]
    assert (
        _evidence(first, MarketEvidenceKey.CLOSE_BREAKOUT_ABOVE_PRIOR_HIGH).status
        is MarketEvidenceStatus.WARMING_UP
    )
    assert not _evidence(first, MarketEvidenceKey.CLOSE_BREAKOUT_ABOVE_PRIOR_HIGH).feature_sources
    breakout = engine.analyze(_manual_history(close="11", high="10", low="10"), config)[-1]
    assert (
        _evidence(breakout, MarketEvidenceKey.CLOSE_BREAKOUT_ABOVE_PRIOR_HIGH).status
        is MarketEvidenceStatus.ACTIVE
    )
    breakdown = engine.analyze(_manual_history(close="9", high="10", low="10"), config)[-1]
    assert (
        _evidence(breakdown, MarketEvidenceKey.CLOSE_BREAKDOWN_BELOW_PRIOR_LOW).status
        is MarketEvidenceStatus.ACTIVE
    )
    equality = engine.analyze(_manual_history(close="10", high="10", low="10"), config)[-1]
    assert (
        _evidence(equality, MarketEvidenceKey.CLOSE_BREAKOUT_ABOVE_PRIOR_HIGH).status
        is MarketEvidenceStatus.INACTIVE
    )
    assert (
        _evidence(equality, MarketEvidenceKey.CLOSE_BREAKDOWN_BELOW_PRIOR_LOW).status
        is MarketEvidenceStatus.INACTIVE
    )
    source = _evidence(breakout, MarketEvidenceKey.CLOSE_BREAKOUT_ABOVE_PRIOR_HIGH).feature_sources[
        0
    ]
    assert source.timestamp == breakout.timestamp - timedelta(days=1)
    history = _manual_history(close="11", high="10", low="10")
    current = history.technical_snapshots[1]
    radically_current = current.model_copy(
        update={
            "features": tuple(
                feature.model_copy(update={"value": Decimal("1000000")})
                if feature.key is TechnicalFeatureKey.ROLLING_HIGHEST_HIGH
                else feature.model_copy(update={"value": Decimal("0")})
                if feature.key is TechnicalFeatureKey.ROLLING_LOWEST_LOW
                else feature
                for feature in current.features
            )
        }
    )
    changed = AlignedTechnicalHistory(
        bars=history.bars,
        technical_snapshots=(history.technical_snapshots[0], radically_current),
    )
    changed_output = engine.analyze(changed, config)[-1]
    assert (
        _evidence(changed_output, MarketEvidenceKey.CLOSE_BREAKOUT_ABOVE_PRIOR_HIGH).status
        is MarketEvidenceStatus.ACTIVE
    )


def test_complete_canonical_order_provenance_and_decimal_fidelity() -> None:
    config = _config(
        **{
            field: 1
            for field in (
                "price_sma_period",
                "trend_ema_period",
                "trend_sma_period",
                "return_period",
                "rsi_period",
                "breakout_high_period",
                "breakdown_low_period",
                "volume_mean_period",
                "volume_zscore_period",
            )
        }
    )
    snapshot = DeterministicMarketEvidenceEngine().analyze(
        _manual_history(close="10.0000000000000000001"), config
    )[-1]
    assert tuple(item.key for item in snapshot.evidence) == MARKET_EVIDENCE_KEY_ORDER
    price = _evidence(snapshot, MarketEvidenceKey.PRICE_ABOVE_SMA)
    assert price.measurements[0].value == Decimal("10.0000000000000000001")
    trend = _evidence(snapshot, MarketEvidenceKey.EMA_ABOVE_SMA)
    assert tuple(source.key for source in trend.feature_sources) == (
        TechnicalFeatureKey.SMA_CLOSE,
        TechnicalFeatureKey.EMA_CLOSE,
    )


def test_model_invariants_reject_duplicates_order_and_contradictions() -> None:
    source = EvidenceFeatureSource(
        timestamp=_START,
        key=TechnicalFeatureKey.SMA_CLOSE,
        period=1,
        status=TechnicalFeatureStatus.AVAILABLE,
        value=Decimal(1),
    )
    close = EvidenceMeasurement(key=EvidenceMeasurementKey.CLOSE, value=Decimal(1))
    with pytest.raises(ValidationError):
        MarketEvidence(
            key=MarketEvidenceKey.PRICE_ABOVE_SMA,
            status=MarketEvidenceStatus.ACTIVE,
            feature_sources=(source, source),
            measurements=(close,),
        )
    with pytest.raises(ValidationError, match="contradicts"):
        MarketEvidence(
            key=MarketEvidenceKey.PRICE_ABOVE_SMA,
            status=MarketEvidenceStatus.ACTIVE,
            feature_sources=(source,),
            measurements=(close,),
        )
    ema = EvidenceFeatureSource(
        timestamp=_START,
        key=TechnicalFeatureKey.EMA_CLOSE,
        period=1,
        status=TechnicalFeatureStatus.AVAILABLE,
        value=Decimal(2),
    )
    with pytest.raises(ValidationError, match="canonical order"):
        MarketEvidence(
            key=MarketEvidenceKey.EMA_ABOVE_SMA,
            status=MarketEvidenceStatus.ACTIVE,
            feature_sources=(ema, source),
            measurements=(),
        )
    threshold = EvidenceMeasurement(
        key=EvidenceMeasurementKey.THRESHOLD, value=Decimal(2)
    )
    with pytest.raises(ValidationError, match="unique"):
        MarketEvidence(
            key=MarketEvidenceKey.VOLUME_ZSCORE_HIGH,
            status=MarketEvidenceStatus.ACTIVE,
            feature_sources=(
                source.model_copy(update={"key": TechnicalFeatureKey.VOLUME_ZSCORE}),
            ),
            measurements=(threshold, threshold),
        )
    with pytest.raises(ValidationError):
        MarketEvidence(
            key=MarketEvidenceKey.PRICE_ABOVE_SMA,
            status=MarketEvidenceStatus.UNDEFINED,
            feature_sources=(source,),
            measurements=(close,),
        )
    warming = source.model_copy(update={"status": TechnicalFeatureStatus.WARMING_UP, "value": None})
    with pytest.raises(ValidationError):
        MarketEvidence(
            key=MarketEvidenceKey.PRICE_ABOVE_SMA,
            status=MarketEvidenceStatus.ACTIVE,
            feature_sources=(warming,),
            measurements=(close,),
        )
    with pytest.raises(ValidationError):
        EvidenceMeasurement(key=EvidenceMeasurementKey.CLOSE, value=(Decimal(1), Decimal(2)))
    with pytest.raises(ValidationError):
        EvidenceFeatureSource(
            timestamp=_START,
            key=TechnicalFeatureKey.SMA_CLOSE,
            period=1,
            status=TechnicalFeatureStatus.AVAILABLE,
            value=None,
        )


def test_snapshot_rejects_missing_duplicate_reordered_and_future_sources() -> None:
    snapshot = DeterministicMarketEvidenceEngine().analyze(
        _history([_bar(0, "1")]), MarketEvidenceConfig()
    )[0]
    for evidence in (
        snapshot.evidence[:-1],
        snapshot.evidence + (snapshot.evidence[-1],),
        tuple(reversed(snapshot.evidence)),
    ):
        with pytest.raises(ValidationError):
            EvidenceSnapshot(
                instrument=snapshot.instrument,
                timeframe=snapshot.timeframe,
                timestamp=snapshot.timestamp,
                evidence=evidence,
            )
    future_source = EvidenceFeatureSource(
        timestamp=snapshot.timestamp + timedelta(days=1),
        key=TechnicalFeatureKey.SMA_CLOSE,
        period=20,
        status=TechnicalFeatureStatus.WARMING_UP,
        value=None,
    )
    changed = snapshot.evidence[0].model_copy(update={"feature_sources": (future_source,)})
    with pytest.raises(ValidationError, match="future"):
        EvidenceSnapshot(
            instrument=snapshot.instrument,
            timeframe=snapshot.timeframe,
            timestamp=snapshot.timestamp,
            evidence=(changed,) + snapshot.evidence[1:],
        )


def test_json_round_trip_determinism_and_immutability() -> None:
    snapshot = DeterministicMarketEvidenceEngine().analyze(
        _history([_bar(0, "1")]), MarketEvidenceConfig()
    )[0]
    serialized = snapshot.model_dump_json()
    assert serialized == snapshot.model_dump_json()
    assert EvidenceSnapshot.model_validate_json(serialized) == snapshot
    with pytest.raises(ValidationError):
        snapshot.evidence = ()
    with pytest.raises(ValidationError):
        snapshot.evidence[0].status = MarketEvidenceStatus.ACTIVE


@pytest.mark.parametrize("value", (1.25, "1.25"))
def test_python_provenance_rejects_decimal_coercion(value: object) -> None:
    with pytest.raises(ValidationError):
        EvidenceMeasurement(
            key=EvidenceMeasurementKey.CLOSE,
            value=cast(Decimal, value),
        )
    with pytest.raises(ValidationError):
        EvidenceFeatureSource(
            timestamp=_START,
            key=TechnicalFeatureKey.SMA_CLOSE,
            period=1,
            status=TechnicalFeatureStatus.AVAILABLE,
            value=cast(Decimal, value),
        )


def test_no_lookahead_prefix_reuse_and_decimal_context_independence() -> None:
    common = [
        _bar(i, str(i + 10), high=str(i + 11), low=str(i + 9), volume=str(100 + i))
        for i in range(25)
    ]
    history_a = _history(common + [_bar(25, "10000", high="20000", low="9000", volume="999999")])
    history_b = _history(common + [_bar(25, "1", high="2", low="0", volume="1")])
    engine = DeterministicMarketEvidenceEngine()
    config = MarketEvidenceConfig()
    result_a = engine.analyze(history_a, config)
    original_history_a = history_a.model_copy(deep=True)
    result_b = engine.analyze(history_b, config)
    assert result_a[:25] == result_b[:25]
    for length in (1, 14, 15, 20, 21, 25):
        prefix = AlignedTechnicalHistory(
            bars=history_a.bars[:length],
            technical_snapshots=history_a.technical_snapshots[:length],
        )
        assert engine.analyze(prefix, config) == result_a[:length]
    baseline = engine.analyze(history_a, config)
    engine.analyze(history_b, config)
    assert engine.analyze(history_a, config) == baseline
    assert DeterministicMarketEvidenceEngine().analyze(history_a, config) == baseline
    with localcontext() as context:
        context.prec = 3
        context.rounding = ROUND_DOWN
        assert engine.analyze(history_a, config) == baseline
    assert history_a == original_history_a


def test_no_clock_network_random_storage_replay_or_strategy_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def network_trap(*args: object, **kwargs: object) -> None:
        raise AssertionError("network used")

    monkeypatch.setattr(socket, "create_connection", network_trap)
    result = DeterministicMarketEvidenceEngine().analyze(
        _history([_bar(0, "1")]), MarketEvidenceConfig()
    )
    assert len(result) == 1
    source = inspect.getsource(evidence_engine_module)
    forbidden = (
        "datetime.now",
        "time.time",
        "random",
        "secrets",
        "sqlite3",
        "app.data",
        "replay",
        "provider",
        "observation",
        "Scanner",
        "Hunter",
        "app.llm",
        "app.risk",
        "portfolio",
        "broker",
        "execution",
        "TradingView",
        "requests",
        "httpx",
        "socket",
        "setup",
        "confidence",
    )
    assert not any(term in source for term in forbidden)
