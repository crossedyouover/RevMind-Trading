"""Forward reference-price measurement, never a simulated fill or P&L."""

from datetime import datetime
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    Underflow,
    localcontext,
)
from typing import Annotated, Literal, Self

from pydantic import BeforeValidator, ConfigDict, ValidationInfo, field_validator, model_validator

from app.core.schemas import CanonicalModel, UtcDatetime
from app.orchestration.models import HeadOfDeskResult
from app.portfolio.models import ObservedPositionMark


def _time(value: object, info: ValidationInfo) -> object:
    if isinstance(value, datetime):
        return value
    if info.mode == "json" and isinstance(value, str):
        return datetime.fromisoformat(value)
    raise ValueError("expected aware datetime")


class OutcomeMeasurement(CanonicalModel):
    model_config = ConfigDict(revalidate_instances="always")
    metric_version: Literal["REFERENCE_PRICE_RETURN_V1"] = "REFERENCE_PRICE_RETURN_V1"
    decision: HeadOfDeskResult
    mark: ObservedPositionMark
    as_of: Annotated[UtcDatetime, BeforeValidator(_time)]

    @field_validator("decision", "mark", mode="before")
    @classmethod
    def canonical(cls, value: object, info: ValidationInfo) -> object:
        if info.mode == "python":
            if isinstance(value, HeadOfDeskResult):
                return HeadOfDeskResult.model_validate(value)
            if isinstance(value, ObservedPositionMark):
                return ObservedPositionMark.model_validate(value)
            raise ValueError("actual canonical evidence required")
        return value

    @model_validator(mode="after")
    def validate_pit(self) -> Self:
        if self.mark.instrument != self.decision.request.proposal.instrument:
            raise ValueError("outcome instrument mismatch")
        if self.mark.valued_at < self.decision.request.evaluation_at:
            raise ValueError("outcome predates decision evaluation")
        if self.mark.observed_at > self.as_of:
            raise ValueError("future-known outcome mark")
        return self

    def reference_return(self) -> Decimal | None:
        trusted = OutcomeMeasurement.model_validate(self)
        reference = trusted.decision.request.proposal.reference_mark.price
        if reference == 0:
            return None
        context = Context(
            prec=50,
            rounding=ROUND_HALF_EVEN,
            Emin=-999999,
            Emax=999999,
            capitals=1,
            clamp=0,
            flags=[],
            traps=[InvalidOperation, DivisionByZero, Overflow, Underflow],
        )
        with localcontext(context):
            return (trusted.mark.price - reference) / reference

    def measurement_status(self) -> str:
        return "UNDEFINED" if self.reference_return() is None else "AVAILABLE"
