from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.core.schemas import AssetClass, Instrument, MarketBar, Timeframe
from app.structure import (
    StructureConfig,
    ZoneConfig,
    ZoneKind,
    ZoneStatus,
    evaluate_structure,
    evaluate_support_resistance,
)

BASE = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
INSTRUMENT = Instrument(symbol="TEST", asset_class=AssetClass.EQUITY, exchange="XNAS")


def bars(values: list[tuple[str, str, str]]) -> tuple[MarketBar, ...]:
    return tuple(
        MarketBar(
            instrument=INSTRUMENT,
            timeframe=Timeframe.FIVE_MINUTES,
            timestamp=BASE + timedelta(minutes=5 * i),
            open=Decimal(c),
            high=Decimal(h),
            low=Decimal(low),
            close=Decimal(c),
            volume=Decimal("100"),
        )
        for i, (h, low, c) in enumerate(values)
    )


def test_resistance_zone_tests_then_breaks_with_stable_identity() -> None:
    series = bars(
        [
            ("10", "8", "9"),
            ("15", "9", "12"),
            ("11", "8", "10"),
            ("15.5", "14", "15"),
            ("16", "15", "16"),
        ]
    )
    structure = evaluate_structure(
        series, StructureConfig(left_span=1, right_span=1), series[-1].timestamp
    )
    result = evaluate_support_resistance(
        series, structure, ZoneConfig(half_width=Decimal("0.5")), series[-1].timestamp
    )
    zone = next(item for item in result.zones if item.kind is ZoneKind.RESISTANCE)
    assert zone.status is ZoneStatus.BROKEN
    assert zone.tested_at == series[3].timestamp
    assert zone.broken_at == series[4].timestamp
    assert (
        zone.zone_id
        == evaluate_support_resistance(series, structure, result.config, series[-1].timestamp)
        .zones[0]
        .zone_id
    )


def test_support_symmetry_strict_break_and_prefix_lifecycle() -> None:
    series = bars(
        [
            ("12", "10", "11"),
            ("11", "5", "8"),
            ("12", "9", "10"),
            ("9", "4.5", "5"),
            ("5", "4", "4"),
        ]
    )
    config = StructureConfig(left_span=1, right_span=1)
    early_structure = evaluate_structure(series[:4], config, series[3].timestamp)
    early = evaluate_support_resistance(
        series[:4], early_structure, ZoneConfig(half_width=Decimal("0.5")), series[3].timestamp
    )
    final_structure = evaluate_structure(series, config, series[-1].timestamp)
    final = evaluate_support_resistance(
        series, final_structure, ZoneConfig(half_width=Decimal("0.5")), series[-1].timestamp
    )
    low = next(item for item in final.zones if item.kind is ZoneKind.SUPPORT)
    assert low.status is ZoneStatus.BROKEN
    assert early.zones[0].zone_id == low.zone_id
    assert early.zones[0].status is ZoneStatus.TESTED


def test_invalid_width_negative_boundary_future_and_unordered_rejected() -> None:
    with pytest.raises(ValueError):
        ZoneConfig(half_width=Decimal("0"))
    series = bars([("2", "1", "1.5"), ("3", "0.1", "1"), ("2", "1", "1.5")])
    structure = evaluate_structure(
        series, StructureConfig(left_span=1, right_span=1), series[-1].timestamp
    )
    with pytest.raises(ValueError, match="negative"):
        evaluate_support_resistance(
            series, structure, ZoneConfig(half_width=Decimal("1")), series[-1].timestamp
        )
    with pytest.raises(ValueError, match="chronological"):
        evaluate_support_resistance(
            tuple(reversed(series)),
            structure,
            ZoneConfig(half_width=Decimal("0.1")),
            series[-1].timestamp,
        )
    with pytest.raises(ValueError, match="cutoff"):
        evaluate_support_resistance(
            series, structure, ZoneConfig(half_width=Decimal("0.1")), series[1].timestamp
        )
