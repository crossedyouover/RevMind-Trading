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
