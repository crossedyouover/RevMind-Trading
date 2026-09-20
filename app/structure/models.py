"""Immutable contracts for descriptive swing and break-of-structure evidence."""

from decimal import Decimal
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import Field, model_validator

from app.core.schemas import CanonicalModel, Instrument, Timeframe, UtcDatetime

Span = Annotated[int, Field(strict=True, ge=1, le=100)]


class PivotKind(StrEnum):
    HIGH = "HIGH"
    LOW = "LOW"


class BreakDirection(StrEnum):
    UPWARD = "UPWARD"
    DOWNWARD = "DOWNWARD"


class LiquiditySide(StrEnum):
    ABOVE_HIGH = "ABOVE_HIGH"
    BELOW_LOW = "BELOW_LOW"


class GapDirection(StrEnum):
    UPWARD = "UPWARD"
    DOWNWARD = "DOWNWARD"


class GapStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"


class ZoneKind(StrEnum):
    SUPPORT = "SUPPORT"
    RESISTANCE = "RESISTANCE"


class ZoneStatus(StrEnum):
    UNTESTED = "UNTESTED"
    TESTED = "TESTED"
    BROKEN = "BROKEN"


class StructureConfig(CanonicalModel):
    left_span: Span
    right_span: Span


class SwingPivot(CanonicalModel):
    pivot_id: UUID
    instrument: Instrument
    timeframe: Timeframe
    kind: PivotKind
    price: Decimal
    occurred_at: UtcDatetime
    confirmed_at: UtcDatetime
    bar_index: int = Field(strict=True, ge=0)

    @model_validator(mode="after")
    def validate_timing(self) -> "SwingPivot":
        if self.confirmed_at < self.occurred_at:
            raise ValueError("pivot confirmation cannot precede occurrence")
        return self


class BreakOfStructure(CanonicalModel):
    break_id: UUID
    pivot_id: UUID
    instrument: Instrument
    timeframe: Timeframe
    direction: BreakDirection
    level: Decimal
    close: Decimal
    occurred_at: UtcDatetime
    evaluation_at: UtcDatetime
    break_index: int = Field(strict=True, ge=0)

    @model_validator(mode="after")
    def validate_break(self) -> "BreakOfStructure":
        if self.occurred_at > self.evaluation_at:
            raise ValueError("break cannot occur after evaluation")
        if self.direction is BreakDirection.UPWARD and self.close <= self.level:
            raise ValueError("upward break requires close above level")
        if self.direction is BreakDirection.DOWNWARD and self.close >= self.level:
            raise ValueError("downward break requires close below level")
        return self


class StructureResult(CanonicalModel):
    config: StructureConfig
    evaluation_at: UtcDatetime
    pivots: tuple[SwingPivot, ...]
    breaks: tuple[BreakOfStructure, ...]


class LiquidityLevel(CanonicalModel):
    level_id: UUID
    pivot_id: UUID
    instrument: Instrument
    timeframe: Timeframe
    side: LiquiditySide
    price: Decimal
    occurred_at: UtcDatetime
    confirmed_at: UtcDatetime


class LiquiditySweep(CanonicalModel):
    sweep_id: UUID
    level_id: UUID
    pivot_id: UUID
    instrument: Instrument
    timeframe: Timeframe
    side: LiquiditySide
    level: Decimal
    extreme: Decimal
    close: Decimal
    occurred_at: UtcDatetime
    evaluation_at: UtcDatetime
    bar_index: int = Field(strict=True, ge=0)

    @model_validator(mode="after")
    def validate_sweep(self) -> "LiquiditySweep":
        if self.occurred_at > self.evaluation_at:
            raise ValueError("sweep cannot occur after evaluation")
        if self.side is LiquiditySide.ABOVE_HIGH:
            if self.extreme <= self.level or self.close > self.level:
                raise ValueError("above-high sweep contradicts prices")
        elif self.extreme >= self.level or self.close < self.level:
            raise ValueError("below-low sweep contradicts prices")
        return self


class LiquidityResult(CanonicalModel):
    evaluation_at: UtcDatetime
    levels: tuple[LiquidityLevel, ...]
    sweeps: tuple[LiquiditySweep, ...]


class FairValueGap(CanonicalModel):
    gap_id: UUID
    instrument: Instrument
    timeframe: Timeframe
    direction: GapDirection
    lower: Decimal
    upper: Decimal
    occurred_at: UtcDatetime
    evaluation_at: UtcDatetime
    first_bar_at: UtcDatetime
    middle_bar_at: UtcDatetime
    third_bar_at: UtcDatetime
    status: GapStatus
    partial_at: UtcDatetime | None = None
    filled_at: UtcDatetime | None = None

    @model_validator(mode="after")
    def validate_gap(self) -> "FairValueGap":
        if self.lower >= self.upper:
            raise ValueError("gap lower boundary must be below upper boundary")
        if not self.first_bar_at < self.middle_bar_at < self.third_bar_at:
            raise ValueError("formation bars must be strictly chronological")
        if self.occurred_at != self.third_bar_at or self.occurred_at > self.evaluation_at:
            raise ValueError("gap occurrence contradicts formation or evaluation")
        if self.status is GapStatus.ACTIVE and (self.partial_at or self.filled_at):
            raise ValueError("active gap cannot have transition times")
        if self.status is GapStatus.PARTIALLY_FILLED and self.partial_at is None:
            raise ValueError("partially filled gap requires partial time")
        if self.status is GapStatus.FILLED and self.filled_at is None:
            raise ValueError("filled gap requires fill time")
        if self.partial_at and self.partial_at < self.occurred_at:
            raise ValueError("partial fill cannot predate formation")
        if self.filled_at and self.filled_at < self.occurred_at:
            raise ValueError("fill cannot predate formation")
        return self


class FairValueGapResult(CanonicalModel):
    evaluation_at: UtcDatetime
    gaps: tuple[FairValueGap, ...]


class ZoneConfig(CanonicalModel):
    half_width: Decimal = Field(gt=0, allow_inf_nan=False)


class SupportResistanceZone(CanonicalModel):
    zone_id: UUID
    pivot_id: UUID
    instrument: Instrument
    timeframe: Timeframe
    kind: ZoneKind
    center: Decimal
    lower: Decimal = Field(ge=0, allow_inf_nan=False)
    upper: Decimal = Field(ge=0, allow_inf_nan=False)
    half_width: Decimal = Field(gt=0, allow_inf_nan=False)
    confirmed_at: UtcDatetime
    evaluation_at: UtcDatetime
    status: ZoneStatus
    tested_at: UtcDatetime | None = None
    tested_bar_index: int | None = Field(default=None, strict=True, ge=0)
    broken_at: UtcDatetime | None = None
    broken_bar_index: int | None = Field(default=None, strict=True, ge=0)

    @model_validator(mode="after")
    def validate_zone(self) -> "SupportResistanceZone":
        if self.lower >= self.upper or self.center - self.lower != self.half_width:
            raise ValueError("zone boundaries contradict center and half_width")
        if self.upper - self.center != self.half_width:
            raise ValueError("zone boundaries contradict center and half_width")
        if self.confirmed_at > self.evaluation_at:
            raise ValueError("zone cannot be confirmed after evaluation")
        if (self.tested_at is None) != (self.tested_bar_index is None):
            raise ValueError("test time and index must appear together")
        if (self.broken_at is None) != (self.broken_bar_index is None):
            raise ValueError("break time and index must appear together")
        if self.status is ZoneStatus.UNTESTED and (self.tested_at or self.broken_at):
            raise ValueError("untested zone cannot contain lifecycle events")
        if self.status is ZoneStatus.TESTED and self.tested_at is None:
            raise ValueError("tested zone requires a test event")
        if self.status is ZoneStatus.BROKEN and self.broken_at is None:
            raise ValueError("broken zone requires a break event")
        return self


class SupportResistanceResult(CanonicalModel):
    config: ZoneConfig
    evaluation_at: UtcDatetime
    zones: tuple[SupportResistanceZone, ...]
