from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.core.schemas import AssetClass, Instrument, MarketBar, Timeframe
from app.structure import GapDirection, GapStatus, evaluate_fair_value_gaps

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


def test_upward_gap_transitions_partial_then_filled() -> None:
    series = bars(
        [
            ("10", "8", "9"),
            ("12", "10", "11"),
            ("15", "13", "14"),
            ("14", "12", "13"),
            ("11", "9", "10"),
        ]
    )
    result = evaluate_fair_value_gaps(series, series[-1].timestamp)
    gap = result.gaps[0]
    assert gap.direction is GapDirection.UPWARD
    assert gap.lower == Decimal("10") and gap.upper == Decimal("13")
    assert gap.status is GapStatus.FILLED
    assert gap.partial_at == series[3].timestamp
    assert gap.filled_at == series[4].timestamp


def test_downward_gap_and_equality_boundary() -> None:
    downward = bars([("15", "13", "14"), ("13", "11", "12"), ("10", "8", "9")])
    result = evaluate_fair_value_gaps(downward, downward[-1].timestamp)
    assert result.gaps[0].direction is GapDirection.DOWNWARD
    assert result.gaps[0].status is GapStatus.ACTIVE
    equal = bars([("10", "8", "9"), ("11", "9", "10"), ("12", "10", "11")])
    assert evaluate_fair_value_gaps(equal, equal[-1].timestamp).gaps == ()


def test_gap_is_deterministic_and_rejects_future_input() -> None:
    series = bars([("10", "8", "9"), ("12", "10", "11"), ("15", "13", "14")])
    first = evaluate_fair_value_gaps(series, series[-1].timestamp)
    second = evaluate_fair_value_gaps(series, series[-1].timestamp)
    assert first.model_dump_json() == second.model_dump_json()
    with pytest.raises(ValueError, match="exceeds"):
        evaluate_fair_value_gaps(series, series[1].timestamp)
