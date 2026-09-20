"""Pure deterministic swing and break-of-structure evaluation."""

from datetime import datetime
from uuid import UUID, uuid5

from app.core.schemas import MarketBar
from app.structure.models import (
    BreakDirection,
    BreakOfStructure,
    PivotKind,
    StructureConfig,
    StructureResult,
    SwingPivot,
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
