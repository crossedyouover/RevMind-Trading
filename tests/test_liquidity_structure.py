from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.core.schemas import AssetClass, Instrument, MarketBar, Timeframe
from app.structure import LiquiditySide, StructureConfig, evaluate_liquidity, evaluate_structure

BASE = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
INSTRUMENT = Instrument(symbol="TEST", asset_class=AssetClass.EQUITY, exchange="XNAS")


def _bars(values: list[tuple[str, str, str]]) -> tuple[MarketBar, ...]:
    return tuple(
        MarketBar(
            instrument=INSTRUMENT,
            timeframe=Timeframe.FIVE_MINUTES,
            timestamp=BASE + timedelta(minutes=5 * index),
            open=Decimal(close),
            high=Decimal(high),
            low=Decimal(low),
            close=Decimal(close),
            volume=Decimal("100"),
        )
        for index, (high, low, close) in enumerate(values)
    )


def _evaluate(values: list[tuple[str, str, str]]):
    series = _bars(values)
    structure = evaluate_structure(
        series, StructureConfig(left_span=1, right_span=1), series[-1].timestamp
    )
    return series, evaluate_liquidity(series, structure, series[-1].timestamp)


def test_wick_beyond_and_close_inside_emits_one_sweep() -> None:
    _, result = _evaluate(
        [
            ("10", "8", "9"),
            ("15", "9", "12"),
            ("11", "8", "10"),
            ("16", "10", "14"),
            ("17", "11", "13"),
        ]
    )
    upward = [item for item in result.sweeps if item.side is LiquiditySide.ABOVE_HIGH]
    assert len(upward) == 1
    assert upward[0].level == Decimal("15")


def test_close_through_and_touch_are_not_sweeps() -> None:
    _, through = _evaluate(
        [("10", "8", "9"), ("15", "9", "12"), ("11", "8", "10"), ("16", "10", "16")]
    )
    assert through.sweeps == ()
    _, touch = _evaluate(
        [("10", "8", "9"), ("15", "9", "12"), ("11", "8", "10"), ("15", "10", "14")]
    )
    assert touch.sweeps == ()


def test_prefix_and_serialization_are_deterministic() -> None:
    values = [
        ("10", "8", "9"),
        ("15", "9", "12"),
        ("11", "8", "10"),
        ("16", "10", "14"),
        ("17", "11", "13"),
    ]
    prefix_series, prefix = _evaluate(values[:4])
    full_series, full = _evaluate(values)
    assert [item.model_dump(exclude={"evaluation_at"}) for item in prefix.sweeps] == [
        item.model_dump(exclude={"evaluation_at"}) for item in full.sweeps[: len(prefix.sweeps)]
    ]
    structure = evaluate_structure(
        full_series, StructureConfig(left_span=1, right_span=1), full_series[-1].timestamp
    )
    repeated = evaluate_liquidity(full_series, structure, full_series[-1].timestamp)
    assert full.model_dump_json() == repeated.model_dump_json()
    assert prefix_series[-1].timestamp < full_series[-1].timestamp
