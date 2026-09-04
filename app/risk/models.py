"""Deterministic bounded paper checks; a pass never authorizes execution."""

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
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    UUID4,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from app.core.schemas import CanonicalModel, Instrument, NonBlankStr, UtcDatetime
from app.portfolio.models import (
    ObservedPositionMark,
    PaperPosition,
    PortfolioContextResult,
    PortfolioValuationStatus,
)


def _decimal(value: object, info: ValidationInfo) -> Decimal:
    if info.mode == "json" and isinstance(value, str):
        try:
            value = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("invalid Decimal") from exc
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("expected finite Decimal")
    return value


def _time(value: object, info: ValidationInfo) -> object:
    if isinstance(value, datetime):
        return value
    if info.mode == "json" and isinstance(value, str):
        return datetime.fromisoformat(value)
    raise ValueError("expected aware datetime")


type Amount = Annotated[Decimal, BeforeValidator(_decimal)]
type Positive = Annotated[Amount, Field(gt=0)]
type Nonnegative = Annotated[Amount, Field(ge=0)]
type Fraction = Annotated[Amount, Field(ge=0, le=1)]
type Time = Annotated[UtcDatetime, BeforeValidator(_time)]
type AgeLimit = Annotated[int, Field(strict=True, ge=0)]
type Currency = Annotated[str, Field(strict=True, pattern=r"^[A-Z]{3}$")]


def _rebuild(value: object) -> object:
    if isinstance(value, CanonicalModel):
        fields = type(value).model_fields
        if set(value.__dict__) - set(fields):
            raise ValueError("unexpected nested fields")
        return type(value).model_validate({name: _rebuild(getattr(value, name)) for name in fields})
    if isinstance(value, tuple):
        return tuple(_rebuild(item) for item in value)
    if isinstance(value, (dict, list, set)):
        raise ValueError("Python fields require immutable canonical values")
    return value


class _Contract(CanonicalModel):
    model_config = ConfigDict(revalidate_instances="always")

    @field_validator("*", mode="before")
    @classmethod
    def rebuild_children(cls, value: object, info: ValidationInfo) -> object:
        if info.mode == "json":
            return value
        try:
            return _rebuild(value)
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("invalid nested state") from exc


class PaperRiskProposal(_Contract):
    proposal_id: UUID4
    account_id: NonBlankStr
    instrument: Instrument
    quantity_change: Amount
    effective_at: Time
    observed_at: Time
    reference_mark: ObservedPositionMark

    @model_validator(mode="after")
    def validate_proposal(self) -> Self:
        if self.quantity_change == 0:
            raise ValueError("proposal quantity must be nonzero")
        if self.effective_at > self.observed_at:
            raise ValueError("proposal effective time exceeds receipt")
        if self.instrument != self.reference_mark.instrument:
            raise ValueError("proposal mark must match full identity")
        return self


class PaperRiskPolicy(_Contract):
    policy_id: NonBlankStr
    policy_version: NonBlankStr
    account_id: NonBlankStr
    currency: Currency
    max_abs_quantity_change: Positive
    max_proposal_notional: Positive
    max_gross_exposure: Positive
    max_instrument_exposure: Positive
    max_gross_exposure_share: Annotated[Positive, Field(le=1)]
    allow_short_positions: Annotated[bool, Field(strict=True)]
    min_equity_value: Positive
    min_cash_balance: Nonnegative
    max_account_age_us: AgeLimit
    max_mark_age_us: AgeLimit
    max_proposal_age_us: AgeLimit


class PaperRiskRequest(_Contract):
    context: PortfolioContextResult
    proposal: PaperRiskProposal
    policy: PaperRiskPolicy
    as_of: Time
    evaluation_at: Time

    @model_validator(mode="after")
    def validate_times(self) -> Self:
        if self.as_of > self.evaluation_at:
            raise ValueError("risk cutoff exceeds evaluation")
        return self


class PaperRiskStatus(StrEnum):
    VETO = "VETO"
    PASS_CHECKS = "PASS_CHECKS"


class PaperRiskReason(StrEnum):
    ACCOUNT_MISMATCH = "ACCOUNT_MISMATCH"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    FUTURE_KNOWLEDGE = "FUTURE_KNOWLEDGE"
    FUTURE_EVENT = "FUTURE_EVENT"
    STALE_ACCOUNT = "STALE_ACCOUNT"
    STALE_PROPOSAL = "STALE_PROPOSAL"
    STALE_MARK = "STALE_MARK"
    PENDING_ACTIONS = "PENDING_ACTIONS"
    INCOMPLETE_VALUATION = "INCOMPLETE_VALUATION"
    NONPOSITIVE_MARK = "NONPOSITIVE_MARK"
    REFERENCE_MARK_MISMATCH = "REFERENCE_MARK_MISMATCH"
    EQUITY_BELOW_MINIMUM = "EQUITY_BELOW_MINIMUM"
    QUANTITY_LIMIT = "QUANTITY_LIMIT"
    PROPOSAL_NOTIONAL_LIMIT = "PROPOSAL_NOTIONAL_LIMIT"
    CASH_FLOOR = "CASH_FLOOR"
    GROSS_EXPOSURE_LIMIT = "GROSS_EXPOSURE_LIMIT"
    INSTRUMENT_EXPOSURE_LIMIT = "INSTRUMENT_EXPOSURE_LIMIT"
    CONCENTRATION_LIMIT = "CONCENTRATION_LIMIT"
    SHORT_POSITION_DISALLOWED = "SHORT_POSITION_DISALLOWED"


class ProjectionConcentrationStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    ZERO_GROSS_EXPOSURE = "ZERO_GROSS_EXPOSURE"


def _context() -> Context:
    return Context(
        prec=50,
        rounding=ROUND_HALF_EVEN,
        Emin=-999999,
        Emax=999999,
        capitals=1,
        clamp=0,
        flags=[],
        traps=[InvalidOperation, DivisionByZero, Overflow, Underflow],
    )


def _identity(instrument: Instrument) -> tuple[str, str, str, str]:
    return (
        instrument.asset_class.value,
        instrument.exchange or "",
        instrument.symbol,
        instrument.currency or "",
    )


def _market_value(position: PaperPosition) -> Decimal:
    if position.quantity == 0:
        return Decimal(0)
    if position.mark is None:
        raise ValueError("projected nonzero position requires mark")
    with localcontext(_context()):
        return position.quantity * position.mark.price


class ProjectedPaperPosition(_Contract):
    position: PaperPosition
    market_value: Amount
    absolute_exposure: Nonnegative
    gross_exposure_share: Fraction | None

    @model_validator(mode="after")
    def validate_value(self) -> Self:
        value = _market_value(self.position)
        if self.market_value != value or self.absolute_exposure != value.copy_abs():
            raise ValueError("projected value contradicts position")
        return self


class PaperRiskProjection(_Contract):
    positions: tuple[ProjectedPaperPosition, ...]
    proposal_notional: Nonnegative
    projected_cash: Amount
    gross_exposure: Nonnegative
    concentration_status: ProjectionConcentrationStatus

    @model_validator(mode="after")
    def validate_projection(self) -> Self:
        keys = tuple(_identity(p.position.instrument) for p in self.positions)
        if any(b <= a for a, b in zip(keys, keys[1:])):
            raise ValueError("projection identity order must be strict")
        with localcontext(_context()):
            gross = Decimal(0)
            for p in self.positions:
                gross += p.absolute_exposure
            status = (
                ProjectionConcentrationStatus.AVAILABLE
                if gross > 0
                else ProjectionConcentrationStatus.ZERO_GROSS_EXPOSURE
            )
            if self.gross_exposure != gross or self.concentration_status != status:
                raise ValueError("projection gross or concentration is inconsistent")
            for p in self.positions:
                share = p.absolute_exposure / gross if gross > 0 else None
                if p.gross_exposure_share != share:
                    raise ValueError("projection share is inconsistent")
        return self


def _age_us(now: datetime, event: datetime) -> int:
    delta = now - event
    return (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds


def _prerequisites(request: PaperRiskRequest) -> tuple[PaperRiskReason, ...]:
    context, proposal, policy = request.context, request.proposal, request.policy
    account = context.request.account
    now = request.evaluation_at
    marks = tuple(p.mark for p in account.positions if p.mark is not None)
    held_marks = tuple(p.mark for p in account.positions if p.quantity != 0 and p.mark is not None)
    matching = next((p for p in account.positions if p.instrument == proposal.instrument), None)
    events = (
        account.effective_at,
        proposal.effective_at,
        proposal.reference_mark.valued_at,
        *(mark.valued_at for mark in marks),
        *(action.effective_at for action in account.pending_actions),
    )
    checks = (
        (
            PaperRiskReason.ACCOUNT_MISMATCH,
            not (proposal.account_id == account.account_id == policy.account_id),
        ),
        (
            PaperRiskReason.CURRENCY_MISMATCH,
            not (policy.currency == account.currency == proposal.instrument.currency),
        ),
        (
            PaperRiskReason.FUTURE_KNOWLEDGE,
            context.request.as_of > request.as_of
            or proposal.observed_at > request.as_of
            or proposal.reference_mark.observed_at > request.as_of,
        ),
        (
            PaperRiskReason.FUTURE_EVENT,
            context.request.evaluation_at > now or any(event > now for event in events),
        ),
        (
            PaperRiskReason.STALE_ACCOUNT,
            _age_us(now, account.effective_at) > policy.max_account_age_us,
        ),
        (
            PaperRiskReason.STALE_PROPOSAL,
            _age_us(now, proposal.effective_at) > policy.max_proposal_age_us,
        ),
        (
            PaperRiskReason.STALE_MARK,
            any(
                _age_us(now, mark.valued_at) > policy.max_mark_age_us
                for mark in (*held_marks, proposal.reference_mark)
            ),
        ),
        (PaperRiskReason.PENDING_ACTIONS, bool(account.pending_actions)),
        (
            PaperRiskReason.INCOMPLETE_VALUATION,
            context.valuation_status is PortfolioValuationStatus.INCOMPLETE,
        ),
        (
            PaperRiskReason.NONPOSITIVE_MARK,
            proposal.reference_mark.price <= 0 or any(mark.price <= 0 for mark in held_marks),
        ),
        (
            PaperRiskReason.REFERENCE_MARK_MISMATCH,
            matching is not None
            and matching.quantity != 0
            and matching.mark is not None
            and matching.mark.model_dump_json() != proposal.reference_mark.model_dump_json(),
        ),
        (
            PaperRiskReason.EQUITY_BELOW_MINIMUM,
            context.equity_value is not None and context.equity_value < policy.min_equity_value,
        ),
    )
    return tuple(reason for reason, failed in checks if failed)


def _project(request: PaperRiskRequest) -> PaperRiskProjection:
    proposal = request.proposal
    account = request.context.request.account
    with localcontext(_context()):
        positions: list[PaperPosition] = []
        inserted = False
        key = _identity(proposal.instrument)
        for existing in account.positions:
            existing_key = _identity(existing.instrument)
            if not inserted and key < existing_key:
                positions.append(
                    PaperPosition(
                        instrument=proposal.instrument,
                        quantity=proposal.quantity_change,
                        mark=proposal.reference_mark,
                    )
                )
                inserted = True
            if key == existing_key:
                positions.append(
                    PaperPosition(
                        instrument=proposal.instrument,
                        quantity=existing.quantity + proposal.quantity_change,
                        mark=proposal.reference_mark,
                    )
                )
                inserted = True
            else:
                positions.append(existing)
        if not inserted:
            positions.append(
                PaperPosition(
                    instrument=proposal.instrument,
                    quantity=proposal.quantity_change,
                    mark=proposal.reference_mark,
                )
            )
        values = tuple(_market_value(p) for p in positions)
        gross = Decimal(0)
        for value in values:
            gross += value.copy_abs()
        cost = proposal.quantity_change * proposal.reference_mark.price
        return PaperRiskProjection(
            positions=tuple(
                ProjectedPaperPosition(
                    position=p,
                    market_value=value,
                    absolute_exposure=value.copy_abs(),
                    gross_exposure_share=value.copy_abs() / gross if gross > 0 else None,
                )
                for p, value in zip(positions, values, strict=True)
            ),
            proposal_notional=cost.copy_abs(),
            projected_cash=account.cash_balance - cost,
            gross_exposure=gross,
            concentration_status=(
                ProjectionConcentrationStatus.AVAILABLE
                if gross > 0
                else ProjectionConcentrationStatus.ZERO_GROSS_EXPOSURE
            ),
        )


def _limits(
    request: PaperRiskRequest, projection: PaperRiskProjection
) -> tuple[PaperRiskReason, ...]:
    policy = request.policy
    checks = (
        (
            PaperRiskReason.QUANTITY_LIMIT,
            request.proposal.quantity_change.copy_abs() > policy.max_abs_quantity_change,
        ),
        (
            PaperRiskReason.PROPOSAL_NOTIONAL_LIMIT,
            projection.proposal_notional > policy.max_proposal_notional,
        ),
        (PaperRiskReason.CASH_FLOOR, projection.projected_cash < policy.min_cash_balance),
        (
            PaperRiskReason.GROSS_EXPOSURE_LIMIT,
            projection.gross_exposure > policy.max_gross_exposure,
        ),
        (
            PaperRiskReason.INSTRUMENT_EXPOSURE_LIMIT,
            any(p.absolute_exposure > policy.max_instrument_exposure for p in projection.positions),
        ),
        (
            PaperRiskReason.CONCENTRATION_LIMIT,
            any(
                p.gross_exposure_share is not None
                and p.gross_exposure_share > policy.max_gross_exposure_share
                for p in projection.positions
            ),
        ),
        (
            PaperRiskReason.SHORT_POSITION_DISALLOWED,
            not policy.allow_short_positions
            and any(p.position.quantity < 0 for p in projection.positions),
        ),
    )
    return tuple(reason for reason, failed in checks if failed)


def _evaluate(
    request: PaperRiskRequest,
) -> tuple[PaperRiskStatus, tuple[PaperRiskReason, ...], PaperRiskProjection | None]:
    reasons = _prerequisites(request)
    if reasons:
        return PaperRiskStatus.VETO, reasons, None
    projection = _project(request)
    reasons = _limits(request, projection)
    return (PaperRiskStatus.VETO if reasons else PaperRiskStatus.PASS_CHECKS), reasons, projection


class PaperRiskResult(_Contract):
    request: PaperRiskRequest
    schema_version: Literal[1] = 1
    status: PaperRiskStatus
    reasons: tuple[PaperRiskReason, ...]
    projection: PaperRiskProjection | None

    @field_validator("schema_version", mode="before")
    @classmethod
    def strict_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("schema version must be integer 1")
        return value

    @model_validator(mode="after")
    def validate_derivation(self) -> Self:
        status, reasons, projection = _evaluate(self.request)
        expected = None if projection is None else projection.model_dump_json()
        actual = None if self.projection is None else self.projection.model_dump_json()
        if self.status != status or self.reasons != reasons or actual != expected:
            raise ValueError("risk result contradicts retained evidence and policy")
        return self
