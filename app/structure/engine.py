"""Pure deterministic swing and break-of-structure evaluation."""

from datetime import datetime
from uuid import UUID, uuid5

from app.core.schemas import MarketBar
from app.structure.models import (
    BreakDirection,
    BreakOfStructure,
    FairValueGap,
    FairValueGapResult,
    GapDirection,
    GapStatus,
    LiquidityLevel,
    LiquidityResult,
    LiquiditySide,
    LiquiditySweep,
    PivotKind,
    StructureConfig,
    StructureResult,
    SupportResistanceResult,
    SupportResistanceZone,
    SwingPivot,
    ZoneConfig,
    ZoneKind,
    ZoneStatus,
)

_NAMESPACE = UUID("a67722dd-bb52-4e4e-a39c-96bdd75a1b07")


def _identity(*parts: object) -> UUID:
    return uuid5(_NAMESPACE, "|".join(str(part) for part in parts))


def evaluate_structure(
    bars: tuple[MarketBar, ...], config: StructureConfig, evaluation_at: datetime
) -> StructureResult:
    """Return descriptive evidence knowable at the explicit evaluation cutoff."""
    if evaluation_at.tzinfo is None or evaluation_at.utcoffset() is None:
        raise ValueError("evaluation time must include timezone information")
    if bars:
        first = bars[0]
        for index, bar in enumerate(bars):
            if bar.instrument != first.instrument or bar.timeframe != first.timeframe:
                raise ValueError("bars must share instrument and timeframe")
            if bar.timestamp > evaluation_at:
                raise ValueError("bar timestamp exceeds evaluation cutoff")
            if index and bar.timestamp <= bars[index - 1].timestamp:
                raise ValueError("bars must be strictly chronological")
    pivots: list[SwingPivot] = []
    left, right = config.left_span, config.right_span
    for index in range(left, len(bars) - right):
        bar = bars[index]
        earlier = bars[index - left : index]
        later = bars[index + 1 : index + right + 1]
        confirmed_at = later[-1].timestamp
        if bar.high > max(item.high for item in (*earlier, *later)):
            pivots.append(
                SwingPivot(
                    pivot_id=_identity(
                        "PIVOT", "HIGH", bar.instrument, bar.timeframe, bar.timestamp
                    ),
                    instrument=bar.instrument,
                    timeframe=bar.timeframe,
                    kind=PivotKind.HIGH,
                    price=bar.high,
                    occurred_at=bar.timestamp,
                    confirmed_at=confirmed_at,
                    bar_index=index,
                )
            )
        if bar.low < min(item.low for item in (*earlier, *later)):
            pivots.append(
                SwingPivot(
                    pivot_id=_identity(
                        "PIVOT", "LOW", bar.instrument, bar.timeframe, bar.timestamp
                    ),
                    instrument=bar.instrument,
                    timeframe=bar.timeframe,
                    kind=PivotKind.LOW,
                    price=bar.low,
                    occurred_at=bar.timestamp,
                    confirmed_at=confirmed_at,
                    bar_index=index,
                )
            )
    pivots.sort(key=lambda item: (item.confirmed_at, item.bar_index, item.kind.value))
    broken: set[UUID] = set()
    breaks: list[BreakOfStructure] = []
    for index, bar in enumerate(bars):
        known = [
            item for item in pivots if item.confirmed_at <= bar.timestamp and item.bar_index < index
        ]
        for kind, direction in (
            (PivotKind.HIGH, BreakDirection.UPWARD),
            (PivotKind.LOW, BreakDirection.DOWNWARD),
        ):
            candidates = [item for item in known if item.kind is kind]
            if not candidates:
                continue
            pivot = candidates[-1]
            crossed = (
                bar.close > pivot.price
                if direction is BreakDirection.UPWARD
                else bar.close < pivot.price
            )
            if crossed and pivot.pivot_id not in broken:
                broken.add(pivot.pivot_id)
                breaks.append(
                    BreakOfStructure(
                        break_id=_identity("BREAK", pivot.pivot_id, bar.timestamp),
                        pivot_id=pivot.pivot_id,
                        instrument=bar.instrument,
                        timeframe=bar.timeframe,
                        direction=direction,
                        level=pivot.price,
                        close=bar.close,
                        occurred_at=bar.timestamp,
                        evaluation_at=evaluation_at,
                        break_index=index,
                    )
                )
    return StructureResult(
        config=config, evaluation_at=evaluation_at, pivots=tuple(pivots), breaks=tuple(breaks)
    )


def evaluate_liquidity(
    bars: tuple[MarketBar, ...], structure: StructureResult, evaluation_at: datetime
) -> LiquidityResult:
    """Derive independent pivot levels and one descriptive wick sweep per level."""
    if evaluation_at.tzinfo is None or evaluation_at.utcoffset() is None:
        raise ValueError("evaluation time must include timezone information")
    for index, bar in enumerate(bars):
        if bar.timestamp > evaluation_at:
            raise ValueError("bar timestamp exceeds evaluation cutoff")
        if index and bar.timestamp <= bars[index - 1].timestamp:
            raise ValueError("bars must be strictly chronological")
    levels = tuple(
        LiquidityLevel(
            level_id=_identity("LEVEL", pivot.pivot_id),
            pivot_id=pivot.pivot_id,
            instrument=pivot.instrument,
            timeframe=pivot.timeframe,
            side=(
                LiquiditySide.ABOVE_HIGH
                if pivot.kind is PivotKind.HIGH
                else LiquiditySide.BELOW_LOW
            ),
            price=pivot.price,
            occurred_at=pivot.occurred_at,
            confirmed_at=pivot.confirmed_at,
        )
        for pivot in structure.pivots
    )
    sweeps: list[LiquiditySweep] = []
    swept: set[UUID] = set()
    for index, bar in enumerate(bars):
        for level in levels:
            if level.level_id in swept or level.confirmed_at > bar.timestamp:
                continue
            if bar.instrument != level.instrument or bar.timeframe != level.timeframe:
                raise ValueError("bars and levels must share instrument and timeframe")
            above = level.side is LiquiditySide.ABOVE_HIGH
            extreme = bar.high if above else bar.low
            qualifies = (
                extreme > level.price and bar.close <= level.price
                if above
                else extreme < level.price and bar.close >= level.price
            )
            if qualifies:
                swept.add(level.level_id)
                sweeps.append(
                    LiquiditySweep(
                        sweep_id=_identity("SWEEP", level.level_id, bar.timestamp),
                        level_id=level.level_id,
                        pivot_id=level.pivot_id,
                        instrument=bar.instrument,
                        timeframe=bar.timeframe,
                        side=level.side,
                        level=level.price,
                        extreme=extreme,
                        close=bar.close,
                        occurred_at=bar.timestamp,
                        evaluation_at=evaluation_at,
                        bar_index=index,
                    )
                )
    return LiquidityResult(evaluation_at=evaluation_at, levels=levels, sweeps=tuple(sweeps))


def evaluate_fair_value_gaps(
    bars: tuple[MarketBar, ...], evaluation_at: datetime
) -> FairValueGapResult:
    """Evaluate strict three-bar gaps and their forward-only fill lifecycle."""
    if evaluation_at.tzinfo is None or evaluation_at.utcoffset() is None:
        raise ValueError("evaluation time must include timezone information")
    if bars:
        first = bars[0]
        for index, bar in enumerate(bars):
            if bar.instrument != first.instrument or bar.timeframe != first.timeframe:
                raise ValueError("bars must share instrument and timeframe")
            if bar.timestamp > evaluation_at:
                raise ValueError("bar timestamp exceeds evaluation cutoff")
            if index and bar.timestamp <= bars[index - 1].timestamp:
                raise ValueError("bars must be strictly chronological")
    gaps: list[FairValueGap] = []
    for index in range(2, len(bars)):
        first, middle, third = bars[index - 2], bars[index - 1], bars[index]
        if third.low > first.high:
            direction = GapDirection.UPWARD
            lower, upper = first.high, third.low
        elif third.high < first.low:
            direction = GapDirection.DOWNWARD
            lower, upper = third.high, first.low
        else:
            continue
        partial_at = None
        filled_at = None
        for later in bars[index + 1 :]:
            if direction is GapDirection.UPWARD:
                if filled_at is None and later.low <= lower:
                    filled_at = later.timestamp
                elif partial_at is None and later.low < upper:
                    partial_at = later.timestamp
            else:
                if filled_at is None and later.high >= upper:
                    filled_at = later.timestamp
                elif partial_at is None and later.high > lower:
                    partial_at = later.timestamp
            if filled_at is not None:
                break
        status = (
            GapStatus.FILLED
            if filled_at is not None
            else GapStatus.PARTIALLY_FILLED
            if partial_at is not None
            else GapStatus.ACTIVE
        )
        gaps.append(
            FairValueGap(
                gap_id=_identity(
                    "FVG", direction, first.timestamp, middle.timestamp, third.timestamp
                ),
                instrument=first.instrument,
                timeframe=first.timeframe,
                direction=direction,
                lower=lower,
                upper=upper,
                occurred_at=third.timestamp,
                evaluation_at=evaluation_at,
                first_bar_at=first.timestamp,
                middle_bar_at=middle.timestamp,
                third_bar_at=third.timestamp,
                status=status,
                partial_at=partial_at,
                filled_at=filled_at,
            )
        )
    return FairValueGapResult(evaluation_at=evaluation_at, gaps=tuple(gaps))


def evaluate_support_resistance(
    bars: tuple[MarketBar, ...],
    structure: StructureResult,
    config: ZoneConfig,
    evaluation_at: datetime,
) -> SupportResistanceResult:
    """Derive independent fixed-width zones and forward-only lifecycle evidence."""
    if evaluation_at.tzinfo is None or evaluation_at.utcoffset() is None:
        raise ValueError("evaluation time must include timezone information")
    if structure.evaluation_at > evaluation_at:
        raise ValueError("structure result exceeds evaluation cutoff")
    if bars:
        first = bars[0]
        for index, bar in enumerate(bars):
            if bar.instrument != first.instrument or bar.timeframe != first.timeframe:
                raise ValueError("bars must share instrument and timeframe")
            if bar.timestamp > evaluation_at:
                raise ValueError("bar timestamp exceeds evaluation cutoff")
            if index and bar.timestamp <= bars[index - 1].timestamp:
                raise ValueError("bars must be strictly chronological")
    zones: list[SupportResistanceZone] = []
    for pivot in structure.pivots:
        if pivot.confirmed_at > evaluation_at:
            raise ValueError("pivot confirmation exceeds evaluation cutoff")
        if bars and (
            pivot.instrument != bars[0].instrument or pivot.timeframe != bars[0].timeframe
        ):
            raise ValueError("bars and pivots must share instrument and timeframe")
        lower, upper = pivot.price - config.half_width, pivot.price + config.half_width
        if lower < 0:
            raise ValueError("zone lower boundary cannot be negative")
        tested_at = broken_at = None
        tested_index = broken_index = None
        for index, bar in enumerate(bars):
            if bar.timestamp <= pivot.confirmed_at:
                continue
            if tested_at is None and bar.high >= lower and bar.low <= upper:
                tested_at, tested_index = bar.timestamp, index
            broken = bar.close > upper if pivot.kind is PivotKind.HIGH else bar.close < lower
            if broken:
                broken_at, broken_index = bar.timestamp, index
                break
        status = (
            ZoneStatus.BROKEN
            if broken_at
            else ZoneStatus.TESTED
            if tested_at
            else ZoneStatus.UNTESTED
        )
        kind = ZoneKind.RESISTANCE if pivot.kind is PivotKind.HIGH else ZoneKind.SUPPORT
        zones.append(
            SupportResistanceZone(
                zone_id=_identity("ZONE", pivot.pivot_id, config.half_width),
                pivot_id=pivot.pivot_id,
                instrument=pivot.instrument,
                timeframe=pivot.timeframe,
                kind=kind,
                center=pivot.price,
                lower=lower,
                upper=upper,
                half_width=config.half_width,
                confirmed_at=pivot.confirmed_at,
                evaluation_at=evaluation_at,
                status=status,
                tested_at=tested_at,
                tested_bar_index=tested_index,
                broken_at=broken_at,
                broken_bar_index=broken_index,
            )
        )
    zones.sort(key=lambda item: (item.confirmed_at, str(item.pivot_id)))
    return SupportResistanceResult(config=config, evaluation_at=evaluation_at, zones=tuple(zones))
