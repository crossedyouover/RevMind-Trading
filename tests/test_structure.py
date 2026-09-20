from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.core.schemas import AssetClass, Instrument, MarketBar, Timeframe
from app.structure import BreakDirection, PivotKind, StructureConfig, evaluate_structure

BASE = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
INSTRUMENT = Instrument(symbol="TEST", asset_class=AssetClass.EQUITY, exchange="XNAS")


def bars(values: list[tuple[str, str, str]]) -> tuple[MarketBar, ...]:
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


def test_pivot_is_not_known_before_right_window_and_prefix_is_stable() -> None:
    series = bars([("10", "8", "9"), ("15", "9", "12"), ("11", "8", "10"), ("16", "10", "16")])
    config = StructureConfig(left_span=1, right_span=1)
    early = evaluate_structure(series[:2], config, series[1].timestamp)
    confirmed = evaluate_structure(series[:3], config, series[2].timestamp)
    extended = evaluate_structure(series, config, series[-1].timestamp)
    assert early.pivots == ()
    assert confirmed.pivots[0].kind is PivotKind.HIGH
    assert confirmed.pivots[0] == extended.pivots[0]
    assert extended.breaks[0].direction is BreakDirection.UPWARD


def test_equal_highs_do_not_form_pivot_and_wick_does_not_break() -> None:
    tied = bars([("10", "8", "9"), ("15", "9", "12"), ("15", "10", "14")])
    assert (
        evaluate_structure(
            tied, StructureConfig(left_span=1, right_span=1), tied[-1].timestamp
        ).pivots
        == ()
    )
    wick = bars([("10", "8", "9"), ("15", "9", "12"), ("11", "8", "10"), ("16", "10", "14")])
    result = evaluate_structure(
        wick, StructureConfig(left_span=1, right_span=1), wick[-1].timestamp
    )
    assert [pivot.kind for pivot in result.pivots].count(PivotKind.HIGH) == 1
    assert result.breaks == ()


def test_break_emits_once_and_serialization_is_deterministic() -> None:
    series = bars(
        [
            ("10", "8", "9"),
            ("15", "9", "12"),
            ("11", "8", "10"),
            ("16", "10", "16"),
            ("17", "11", "17"),
        ]
    )
    config = StructureConfig(left_span=1, right_span=1)
    first = evaluate_structure(series, config, series[-1].timestamp)
    second = evaluate_structure(series, config, series[-1].timestamp)
    assert len(first.breaks) == 1
    assert first.model_dump_json() == second.model_dump_json()


def test_rejects_future_or_unordered_bars() -> None:
    series = bars([("10", "8", "9"), ("11", "9", "10")])
    config = StructureConfig(left_span=1, right_span=1)
    with pytest.raises(ValueError, match="exceeds"):
        evaluate_structure(series, config, series[0].timestamp)
    with pytest.raises(ValueError, match="chronological"):
        evaluate_structure(tuple(reversed(series)), config, series[-1].timestamp)
