"""Known-answer and adversarial tests for the Phase 7 technical engine."""

import inspect
import socket
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal, localcontext
from typing import cast

import pytest
from pydantic import ValidationError

import app.technical.engine as engine_module
from app.core.schemas import AssetClass, Instrument, MarketBar, Timeframe
from app.technical import (
    DeterministicTechnicalAnalysisEngine,
    TechnicalAnalysisComputationError,
    TechnicalAnalysisConfig,
    TechnicalAnalysisConfigurationError,
    TechnicalAnalysisInvalidInputError,
    TechnicalFeature,
    TechnicalFeatureKey,
    TechnicalFeatureStatus,
    TechnicalSnapshot,
)
from app.technical.models import TECHNICAL_FEATURE_KEY_ORDER

_START = datetime(2026, 1, 1, tzinfo=UTC)
_INSTRUMENT = Instrument(
    symbol="NVDA", asset_class=AssetClass.EQUITY, exchange="XNAS", currency="USD"
)
_FIELDS = (
    "sma_periods", "ema_periods", "rsi_periods", "atr_periods",
    "rolling_high_periods", "rolling_low_periods", "return_periods",
    "volume_mean_periods", "volume_stddev_periods", "volume_zscore_periods",
)


def _bar(
    index: int,
    close: str,
    *,
    high: str | None = None,
    low: str | None = None,
    volume: str = "10",
    instrument: Instrument = _INSTRUMENT,
    timeframe: Timeframe = Timeframe.FIVE_MINUTES,
) -> MarketBar:
    close_value = Decimal(close)
    return MarketBar(
        instrument=instrument,
        timeframe=timeframe,
        timestamp=_START + timedelta(minutes=index * 5),
        open=close_value,
        high=Decimal(high) if high is not None else close_value,
        low=Decimal(low) if low is not None else close_value,
        close=close_value,
        volume=Decimal(volume),
    )


def _config(**enabled: tuple[int, ...]) -> TechnicalAnalysisConfig:
    values: dict[str, tuple[int, ...]] = {field: () for field in _FIELDS}
    values.update(enabled)
    return TechnicalAnalysisConfig.model_validate(values)


def _feature(
    snapshot: TechnicalSnapshot, key: TechnicalFeatureKey, period: int
) -> TechnicalFeature:
    return next(
        item for item in snapshot.features if item.key is key and item.period == period
    )


def _run(
    bars: list[MarketBar], config: TechnicalAnalysisConfig
) -> tuple[TechnicalSnapshot, ...]:
    return DeterministicTechnicalAnalysisEngine().analyze(bars, config)


def test_config_defaults_immutability_empty_family_and_all_empty() -> None:
    config = TechnicalAnalysisConfig()
    assert tuple(getattr(config, field) for field in _FIELDS) == (
        (20,), (20,), (14,), (14,), (20,), (20,), (1,), (20,), (20,), (20,)
    )
    assert TechnicalAnalysisConfig(sma_periods=()).sma_periods == ()
    with pytest.raises(ValidationError):
        config.sma_periods = (5,)
    with pytest.raises(ValidationError, match="at least one"):
        TechnicalAnalysisConfig.model_validate({field: () for field in _FIELDS})


@pytest.mark.parametrize(
    "periods", [(True,), (1.0,), ("1",), (0,), (-1,), (100_001,), (2, 2), (3, 2)]
)
def test_invalid_periods_are_rejected_without_coercion_or_sorting(
    periods: tuple[object, ...],
) -> None:
    with pytest.raises(ValidationError):
        TechnicalAnalysisConfig(sma_periods=cast(tuple[int, ...], periods))


def test_periods_require_tuple_and_engine_requires_config_model() -> None:
    with pytest.raises(ValidationError, match="tuple"):
        TechnicalAnalysisConfig(sma_periods=cast(tuple[int, ...], [1]))
    with pytest.raises(TechnicalAnalysisConfigurationError):
        DeterministicTechnicalAnalysisEngine().analyze([], cast(TechnicalAnalysisConfig, object()))


def test_every_configuration_family_rejects_all_frozen_coercion_attacks() -> None:
    invalid_values: tuple[object, ...] = (
        (True,), (False,), (1.0,), (Decimal("1"),), ("1",), (0,), (-1,),
        (100_001,), (1, 1), (2, 1), (1, 3, 2), [1], (value for value in (1,)), None,
    )
    for field in _FIELDS:
        for invalid in invalid_values:
            with pytest.raises(ValidationError):
                TechnicalAnalysisConfig.model_validate({field: invalid})


@pytest.mark.parametrize(
    ("status", "value"),
    [
        (TechnicalFeatureStatus.AVAILABLE, None),
        (TechnicalFeatureStatus.WARMING_UP, Decimal(1)),
        (TechnicalFeatureStatus.UNDEFINED, Decimal(1)),
    ],
)
def test_status_value_contract(status: TechnicalFeatureStatus, value: Decimal | None) -> None:
    with pytest.raises(ValidationError):
        TechnicalFeature(
            key=TechnicalFeatureKey.SMA_CLOSE, period=1, status=status, value=value
        )


def test_snapshot_rejects_duplicates_and_noncanonical_order() -> None:
    sma = TechnicalFeature(
        key=TechnicalFeatureKey.SMA_CLOSE,
        period=2,
        status=TechnicalFeatureStatus.WARMING_UP,
        value=None,
    )
    ema = sma.model_copy(update={"key": TechnicalFeatureKey.EMA_CLOSE})
    with pytest.raises(ValidationError, match="unique"):
        TechnicalSnapshot(
            instrument=_INSTRUMENT,
            timeframe=Timeframe.FIVE_MINUTES,
            timestamp=_START,
            features=(sma, sma),
        )
    with pytest.raises(ValidationError, match="canonical"):
        TechnicalSnapshot(
            instrument=_INSTRUMENT,
            timeframe=Timeframe.FIVE_MINUTES,
            timestamp=_START,
            features=(ema, sma),
        )


def test_sma_and_sma_seeded_ema_known_answers() -> None:
    result = _run(
        [_bar(i, str(i + 1)) for i in range(4)],
        _config(sma_periods=(3,), ema_periods=(3,)),
    )
    assert all(
        _feature(result[i], TechnicalFeatureKey.SMA_CLOSE, 3).status
        is TechnicalFeatureStatus.WARMING_UP
        for i in range(2)
    )
    assert _feature(result[2], TechnicalFeatureKey.SMA_CLOSE, 3).value == 2
    assert _feature(result[2], TechnicalFeatureKey.EMA_CLOSE, 3).value == 2
    assert _feature(result[3], TechnicalFeatureKey.EMA_CLOSE, 3).value == 3


def test_multiple_sma_periods_and_ema_seed_are_independently_verifiable() -> None:
    bars = [_bar(i, value) for i, value in enumerate(("10", "20", "30", "40", "50"))]
    result = _run(bars, _config(sma_periods=(1, 2, 3), ema_periods=(1, 3)))
    assert [_feature(result[-1], TechnicalFeatureKey.SMA_CLOSE, p).value for p in (1, 2, 3)] == [
        Decimal("50"), Decimal("45"), Decimal("40")
    ]
    assert _feature(result[1], TechnicalFeatureKey.EMA_CLOSE, 3).status is (
        TechnicalFeatureStatus.WARMING_UP
    )
    assert _feature(result[2], TechnicalFeatureKey.EMA_CLOSE, 3).value == 20
    assert _feature(result[3], TechnicalFeatureKey.EMA_CLOSE, 3).value == 30
    assert _feature(result[4], TechnicalFeatureKey.EMA_CLOSE, 3).value == 40
    assert _feature(result[4], TechnicalFeatureKey.EMA_CLOSE, 1).value == 50


def test_wilder_rsi_seed_recursion_and_edges() -> None:
    result = _run(
        [_bar(i, value) for i, value in enumerate(("1", "2", "3", "2"))],
        _config(rsi_periods=(2,)),
    )
    assert _feature(result[1], TechnicalFeatureKey.RSI_CLOSE_WILDER, 2).status is (
        TechnicalFeatureStatus.WARMING_UP
    )
    assert _feature(result[2], TechnicalFeatureKey.RSI_CLOSE_WILDER, 2).value == 100
    assert _feature(result[3], TechnicalFeatureKey.RSI_CLOSE_WILDER, 2).value == 50
    extended = _run(
        [_bar(i, value) for i, value in enumerate(("1", "2", "3", "2", "3"))],
        _config(rsi_periods=(2,)),
    )
    assert _feature(extended[4], TechnicalFeatureKey.RSI_CLOSE_WILDER, 2).value == 75
    for closes, expected in ((('1', '2'), 100), (('2', '1'), 0), (('1', '1'), 50)):
        snapshots = _run(
            [_bar(i, close) for i, close in enumerate(closes)],
            _config(rsi_periods=(1,)),
        )
        assert _feature(snapshots[1], TechnicalFeatureKey.RSI_CLOSE_WILDER, 1).value == expected


def test_wilder_atr_gaps_seed_recursion_and_rolling_extrema() -> None:
    bars = [
        _bar(0, "1", high="2", low="0"),
        _bar(1, "3", high="4", low="2"),
        _bar(2, "0.5", high="1", low="0"),
    ]
    result = _run(
        bars,
        _config(atr_periods=(2,), rolling_high_periods=(2,), rolling_low_periods=(2,)),
    )
    assert _feature(result[0], TechnicalFeatureKey.ATR_WILDER, 2).status is (
        TechnicalFeatureStatus.WARMING_UP
    )
    assert _feature(result[1], TechnicalFeatureKey.ATR_WILDER, 2).value == Decimal("2.5")
    assert _feature(result[2], TechnicalFeatureKey.ATR_WILDER, 2).value == Decimal("2.75")
    assert _feature(result[1], TechnicalFeatureKey.ROLLING_HIGHEST_HIGH, 2).value == 4
    assert _feature(result[2], TechnicalFeatureKey.ROLLING_LOWEST_LOW, 2).value == 0


def test_rolling_extrema_evict_values_that_leave_each_window() -> None:
    bars = [
        _bar(0, "5", high="9", low="1"),
        _bar(1, "5", high="7", low="3"),
        _bar(2, "5", high="6", low="4"),
        _bar(3, "5", high="8", low="2"),
    ]
    result = _run(
        bars,
        _config(rolling_high_periods=(1, 3), rolling_low_periods=(1, 3)),
    )
    assert _feature(result[2], TechnicalFeatureKey.ROLLING_HIGHEST_HIGH, 3).value == 9
    assert _feature(result[3], TechnicalFeatureKey.ROLLING_HIGHEST_HIGH, 3).value == 8
    assert _feature(result[2], TechnicalFeatureKey.ROLLING_LOWEST_LOW, 3).value == 1
    assert _feature(result[3], TechnicalFeatureKey.ROLLING_LOWEST_LOW, 3).value == 2
    assert _feature(result[3], TechnicalFeatureKey.ROLLING_HIGHEST_HIGH, 1).value == 8
    assert _feature(result[3], TechnicalFeatureKey.ROLLING_LOWEST_LOW, 1).value == 2


def test_return_known_answers_and_undefined_zero_denominator() -> None:
    result = _run(
        [_bar(i, value) for i, value in enumerate(("0", "2", "4", "2", "2"))],
        _config(return_periods=(1,)),
    )
    undefined = _feature(result[1], TechnicalFeatureKey.ARITHMETIC_RETURN, 1)
    assert undefined.status is TechnicalFeatureStatus.UNDEFINED and undefined.value is None
    assert _feature(result[2], TechnicalFeatureKey.ARITHMETIC_RETURN, 1).value == 1
    assert _feature(result[3], TechnicalFeatureKey.ARITHMETIC_RETURN, 1).value == Decimal("-0.5")
    assert _feature(result[4], TechnicalFeatureKey.ARITHMETIC_RETURN, 1).value == 0
    longer = _run(
        [_bar(i, value) for i, value in enumerate(("2", "100", "3"))],
        _config(return_periods=(2,)),
    )
    assert _feature(longer[1], TechnicalFeatureKey.ARITHMETIC_RETURN, 2).status is (
        TechnicalFeatureStatus.WARMING_UP
    )
    assert _feature(longer[2], TechnicalFeatureKey.ARITHMETIC_RETURN, 2).value == Decimal("0.5")


def test_volume_mean_population_stddev_and_zscore_known_answers() -> None:
    result = _run(
        [_bar(0, "1", volume="1"), _bar(1, "1", volume="3")],
        _config(
            volume_mean_periods=(2,),
            volume_stddev_periods=(2,),
            volume_zscore_periods=(2,),
        ),
    )
    assert all(item.status is TechnicalFeatureStatus.WARMING_UP for item in result[0].features)
    assert _feature(result[1], TechnicalFeatureKey.VOLUME_MEAN, 2).value == 2
    assert _feature(result[1], TechnicalFeatureKey.VOLUME_STDDEV_POPULATION, 2).value == 1
    assert _feature(result[1], TechnicalFeatureKey.VOLUME_ZSCORE, 2).value == 1


def test_population_stddev_resists_cancellation_and_zscore_sign_cases() -> None:
    volumes = (
        Decimal("1000000000000000000000000000001"),
        Decimal("1000000000000000000000000000002"),
        Decimal("1000000000000000000000000000003"),
    )
    bars = [_bar(i, "1", volume=str(value)) for i, value in enumerate(volumes)]
    result = _run(
        bars,
        _config(
            volume_mean_periods=(1, 2, 3),
            volume_stddev_periods=(1, 3),
            volume_zscore_periods=(1, 3),
        ),
    )
    expected_stddev = Decimal("0.81649658092772603273242802490196379732198249355223")
    assert _feature(result[2], TechnicalFeatureKey.VOLUME_MEAN, 3).value == volumes[1]
    assert _feature(result[2], TechnicalFeatureKey.VOLUME_STDDEV_POPULATION, 3).value == (
        expected_stddev
    )
    assert _feature(result[0], TechnicalFeatureKey.VOLUME_STDDEV_POPULATION, 1).value == 0
    assert _feature(result[0], TechnicalFeatureKey.VOLUME_ZSCORE, 1).status is (
        TechnicalFeatureStatus.UNDEFINED
    )
    above = _feature(result[2], TechnicalFeatureKey.VOLUME_ZSCORE, 3).value
    assert above == Decimal("1.2247448713915890490986420373529456959829737403283")
    equal_result = _run(
        [_bar(i, "1", volume=value) for i, value in enumerate(("1", "3", "2"))],
        _config(volume_zscore_periods=(3,)),
    )
    assert _feature(equal_result[2], TechnicalFeatureKey.VOLUME_ZSCORE, 3).value == 0
    below_result = _run(
        [_bar(i, "1", volume=value) for i, value in enumerate(("3", "2", "1"))],
        _config(volume_zscore_periods=(3,)),
    )
    assert _feature(below_result[2], TechnicalFeatureKey.VOLUME_ZSCORE, 3).value == Decimal(
        "-1.2247448713915890490986420373529456959829737403283"
    )


def test_period_one_semantics() -> None:
    bars = [
        _bar(0, "2", high="3", low="1", volume="7"),
        _bar(1, "4", high="5", low="3", volume="9"),
    ]
    result = _run(bars, TechnicalAnalysisConfig.model_validate({field: (1,) for field in _FIELDS}))
    first = result[0]
    expected = {
        TechnicalFeatureKey.SMA_CLOSE: 2,
        TechnicalFeatureKey.EMA_CLOSE: 2,
        TechnicalFeatureKey.ATR_WILDER: 2,
        TechnicalFeatureKey.ROLLING_HIGHEST_HIGH: 3,
        TechnicalFeatureKey.ROLLING_LOWEST_LOW: 1,
        TechnicalFeatureKey.VOLUME_MEAN: 7,
        TechnicalFeatureKey.VOLUME_STDDEV_POPULATION: 0,
    }
    for key, value in expected.items():
        assert _feature(first, key, 1).value == value
    assert _feature(first, TechnicalFeatureKey.VOLUME_ZSCORE, 1).status is (
        TechnicalFeatureStatus.UNDEFINED
    )
    assert _feature(first, TechnicalFeatureKey.ARITHMETIC_RETURN, 1).status is (
        TechnicalFeatureStatus.WARMING_UP
    )
    assert _feature(result[1], TechnicalFeatureKey.RSI_CLOSE_WILDER, 1).value == 100


def test_feature_order_is_explicit_then_period_ascending() -> None:
    config = TechnicalAnalysisConfig(sma_periods=(2, 5), ema_periods=(3, 4))
    pairs = [(item.key, item.period) for item in _run([_bar(0, "1")], config)[0].features]
    order = {key: index for index, key in enumerate(TECHNICAL_FEATURE_KEY_ORDER)}
    assert pairs == sorted(pairs, key=lambda pair: (order[pair[0]], pair[1]))


def test_empty_single_and_gapped_history_alignment() -> None:
    engine = DeterministicTechnicalAnalysisEngine()
    config = _config(sma_periods=(2,))
    assert engine.analyze([], config) == ()
    single = engine.analyze([_bar(0, "1")], config)
    assert len(single) == 1 and single[0].features[0].status is TechnicalFeatureStatus.WARMING_UP
    assert len(engine.analyze([_bar(0, "1"), _bar(100, "2")], config)) == 2


def test_malformed_history_is_rejected_without_repair() -> None:
    engine = DeterministicTechnicalAnalysisEngine()
    config = _config(sma_periods=(1,))
    with pytest.raises(TechnicalAnalysisInvalidInputError):
        engine.analyze(cast(list[MarketBar], [_bar(0, "1"), object()]), config)
    other = Instrument(symbol="NVDA", asset_class=AssetClass.EQUITY, exchange="XNYS")
    with pytest.raises(TechnicalAnalysisInvalidInputError, match="instruments"):
        engine.analyze([_bar(0, "1"), _bar(1, "2", instrument=other)], config)
    other_asset = Instrument(symbol="NVDA", asset_class=AssetClass.CRYPTO, exchange="XNAS")
    with pytest.raises(TechnicalAnalysisInvalidInputError, match="instruments"):
        engine.analyze([_bar(0, "1"), _bar(1, "2", instrument=other_asset)], config)
    with pytest.raises(TechnicalAnalysisInvalidInputError, match="timeframes"):
        engine.analyze([_bar(0, "1"), _bar(1, "2", timeframe=Timeframe.ONE_HOUR)], config)
    with pytest.raises(TechnicalAnalysisInvalidInputError, match="duplicate"):
        engine.analyze([_bar(0, "1"), _bar(0, "2")], config)
    with pytest.raises(TechnicalAnalysisInvalidInputError, match="increasing"):
        engine.analyze([_bar(1, "2"), _bar(0, "1")], config)


def test_permanent_no_lookahead_and_append_invariants() -> None:
    common = [
        _bar(i, str(i + 10), high=str(i + 11), low=str(i + 9), volume=str(i + 100))
        for i in range(25)
    ]
    upward = common + [_bar(25, "10000", high="20000", low="9000", volume="999999")]
    downward = common + [_bar(25, "1", high="2", low="0", volume="1")]
    engine = DeterministicTechnicalAnalysisEngine()
    baseline = engine.analyze(common, TechnicalAnalysisConfig())
    assert engine.analyze(upward, TechnicalAnalysisConfig())[:25] == baseline
    assert engine.analyze(downward, TechnicalAnalysisConfig())[:25] == baseline


def test_prefix_invariance_and_stateless_call_order() -> None:
    history = [
        _bar(i, str(i + 2), high=str(i + 3), low=str(i + 1), volume=str(100 + i * i))
        for i in range(30)
    ]
    other = [_bar(i, str(100 - i), volume=str(500 - i)) for i in range(30)]
    engine = DeterministicTechnicalAnalysisEngine()
    config = TechnicalAnalysisConfig()
    complete = engine.analyze(history, config)
    for length in (1, 14, 15, 20, 21, 30):
        assert engine.analyze(history[:length], config) == complete[:length]
    first = engine.analyze(history, config)
    engine.analyze(other, config)
    second = engine.analyze(history, config)
    assert first == second == DeterministicTechnicalAnalysisEngine().analyze(history, config)


def test_decimal_context_independence_precision_repeatability_and_no_mutation() -> None:
    bars = [_bar(i, value) for i, value in enumerate(("1.0000000000000000001", "2", "3"))]
    original = tuple(bars)
    config = _config(sma_periods=(3,), ema_periods=(3,))
    engine = DeterministicTechnicalAnalysisEngine()
    baseline = engine.analyze(bars, config)
    with localcontext() as context:
        context.prec = 6
        context.rounding = ROUND_DOWN
        assert engine.analyze(bars, config) == baseline
    assert engine.analyze(bars, config) == baseline
    assert tuple(bars) == original
    assert _feature(baseline[-1], TechnicalFeatureKey.SMA_CLOSE, 3).value == Decimal(
        "2.0000000000000000000333333333333333333333333333333"
    )


def test_models_nested_values_and_json_are_immutable_and_deterministic() -> None:
    snapshot = _run([_bar(0, "1")], _config(sma_periods=(1,)))[0]
    assert snapshot.model_dump_json() == snapshot.model_dump_json()
    assert TechnicalSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot
    with pytest.raises(ValidationError):
        snapshot.timestamp = _START + timedelta(days=1)
    with pytest.raises(ValidationError):
        snapshot.features[0].value = Decimal(9)
    with pytest.raises(ValidationError):
        TechnicalFeature(
            key=TechnicalFeatureKey.SMA_CLOSE,
            period=1,
            status=TechnicalFeatureStatus.AVAILABLE,
            value=Decimal("Infinity"),
        )
    with pytest.raises(ValidationError):
        TechnicalAnalysisConfig.model_validate({"unexpected": "forbidden"})


def test_decimal_overflow_fails_closed_as_computation_error() -> None:
    bars = [_bar(0, "1", volume="0"), _bar(1, "1", volume="1e999999")]
    with pytest.raises(TechnicalAnalysisComputationError):
        _run(bars, _config(volume_stddev_periods=(2,)))


def test_no_clock_network_random_storage_or_replay_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def network_trap(*args: object, **kwargs: object) -> None:
        raise AssertionError("network used")

    monkeypatch.setattr(socket, "create_connection", network_trap)
    assert _run([_bar(0, "1")], _config(sma_periods=(1,)))[0].features[0].value == 1
    source = inspect.getsource(engine_module)
    forbidden = (
        "datetime.now", "time.time", "import random", "import secrets", "sqlite3",
        "app.data.replay", "observation_store", "ObservedMarketData", "MarketDataProvider",
    )
    assert not any(term in source for term in forbidden)
