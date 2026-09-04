"""Immutable single-currency paper context with explicit knowledge boundaries."""

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

from app.core.schemas import AssetClass, CanonicalModel, Instrument, NonBlankStr, UtcDatetime
from app.data.observations import SourceIdentity


def _decimal(value: object, info: ValidationInfo) -> Decimal:
    if info.mode == "json" and isinstance(value, str):
        try:
            value = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("invalid Decimal string") from exc
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("expected a finite Decimal")
    return value


def _time(value: object, info: ValidationInfo) -> object:
    if isinstance(value, datetime):
        return value
    if info.mode == "json" and isinstance(value, str):
        return datetime.fromisoformat(value)
    raise ValueError("expected an aware datetime")


type Amount = Annotated[Decimal, BeforeValidator(_decimal)]
type NonnegativeAmount = Annotated[Amount, Field(ge=0)]
type Share = Annotated[Amount, Field(ge=0, le=1)]
type KnowledgeTime = Annotated[UtcDatetime, BeforeValidator(_time)]
type Currency = Annotated[str, Field(strict=True, pattern=r"^[A-Z]{3}$")]


def _rebuild(value: object) -> object:
    if isinstance(value, CanonicalModel):
        fields = type(value).model_fields
        if set(value.__dict__) - set(fields):
            raise ValueError("unknown nested fields")
        return type(value).model_validate({name: _rebuild(getattr(value, name)) for name in fields})
    if isinstance(value, tuple):
        return tuple(_rebuild(item) for item in value)
    if isinstance(value, (list, dict, set)):
        raise ValueError("Python nested inputs must be immutable canonical objects")
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
            raise ValueError("noncanonical nested state") from exc


def _supported(instrument: Instrument) -> None:
    if instrument.asset_class not in (AssetClass.EQUITY, AssetClass.ETF):
        raise ValueError("only equity and ETF shares are supported")
    currency = instrument.currency
    if currency is None or len(currency) != 3 or not all("A" <= c <= "Z" for c in currency):
        raise ValueError("instrument requires a canonical currency")


def _identity(instrument: Instrument) -> tuple[str, str, str, str]:
    return (
        instrument.asset_class.value,
        instrument.exchange or "",
        instrument.symbol,
        instrument.currency or "",
    )


class ObservedPositionMark(_Contract):
    observation_id: UUID4
    source: SourceIdentity
    instrument: Instrument
    price: NonnegativeAmount
    valued_at: KnowledgeTime
    observed_at: KnowledgeTime

    @model_validator(mode="after")
    def validate_mark(self) -> Self:
        _supported(self.instrument)
        if self.valued_at > self.observed_at:
            raise ValueError("mark valuation exceeds receipt time")
        return self


class PaperPosition(_Contract):
    instrument: Instrument
    quantity: Amount
    mark: ObservedPositionMark | None

    @model_validator(mode="after")
    def validate_position(self) -> Self:
        _supported(self.instrument)
        if self.mark is not None and self.mark.instrument != self.instrument:
            raise ValueError("mark must match complete position identity")
        return self


class PendingPaperAction(_Contract):
    action_id: UUID4
    instrument: Instrument
    remaining_quantity: Amount
    effective_at: KnowledgeTime
    observed_at: KnowledgeTime

    @model_validator(mode="after")
    def validate_action(self) -> Self:
        _supported(self.instrument)
        if self.remaining_quantity == 0:
            raise ValueError("pending quantity must be nonzero")
        if self.effective_at > self.observed_at:
            raise ValueError("action effective time exceeds receipt time")
        return self


class ObservedPaperAccountState(_Contract):
    observation_id: UUID4
    account_id: NonBlankStr
    source: SourceIdentity
    currency: Currency
    effective_at: KnowledgeTime
    observed_at: KnowledgeTime
    cash_balance: Amount
    positions: tuple[PaperPosition, ...]
    pending_actions: tuple[PendingPaperAction, ...]

    @model_validator(mode="after")
    def validate_account(self) -> Self:
        if self.effective_at > self.observed_at:
            raise ValueError("account effective time exceeds receipt time")
        keys = tuple(_identity(position.instrument) for position in self.positions)
        if any(b <= a for a, b in zip(keys, keys[1:])):
            raise ValueError("positions must be unique and canonically ordered")
        actions = tuple(action.action_id for action in self.pending_actions)
        if any(b <= a for a, b in zip(actions, actions[1:])):
            raise ValueError("pending actions must be unique and ordered by action_id")
        receipts = [self.observation_id]
        for position in self.positions:
            if position.instrument.currency != self.currency:
                raise ValueError("position currency differs from account")
            if position.mark is not None:
                receipts.append(position.mark.observation_id)
        if len(set(receipts)) != len(receipts):
            raise ValueError("account and mark receipt IDs must be unique")
        for action in self.pending_actions:
            if action.instrument.currency != self.currency:
                raise ValueError("action currency differs from account")
            if action.observed_at > self.observed_at:
                raise ValueError("action was not known at account receipt")
        return self


class PortfolioContextRequest(_Contract):
    account: ObservedPaperAccountState
    as_of: KnowledgeTime
    evaluation_at: KnowledgeTime

    @model_validator(mode="after")
    def validate_pit(self) -> Self:
        if self.as_of > self.evaluation_at:
            raise ValueError("knowledge cutoff exceeds evaluation")
        if self.account.observed_at > self.as_of:
            raise ValueError("account is future-known")
        for position in self.account.positions:
            if position.mark is not None and position.mark.observed_at > self.as_of:
                raise ValueError("mark is future-known, including on zero positions")
        # Effective/valuation times are bounded by their own receipts in the child contracts.
        return self


class PositionValuationStatus(StrEnum):
    ZERO_POSITION = "ZERO_POSITION"
    MISSING_MARK = "MISSING_MARK"
    VALUED = "VALUED"


class PortfolioValuationStatus(StrEnum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"


class ConcentrationStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    ZERO_GROSS_EXPOSURE = "ZERO_GROSS_EXPOSURE"
    INCOMPLETE_VALUATION = "INCOMPLETE_VALUATION"


def _context() -> Context:
    """Specify every setting; never inherit caller or mutable DefaultContext state."""
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


def _values(position: PaperPosition) -> tuple[PositionValuationStatus, Decimal | None]:
    if position.quantity == 0:
        return PositionValuationStatus.ZERO_POSITION, Decimal(0)
    if position.mark is None:
        return PositionValuationStatus.MISSING_MARK, None
    with localcontext(_context()):
        return PositionValuationStatus.VALUED, position.quantity * position.mark.price


class PositionValuation(_Contract):
    position: PaperPosition
    status: PositionValuationStatus
    market_value: Amount | None
    absolute_exposure: NonnegativeAmount | None
    gross_exposure_share: Share | None

    @model_validator(mode="after")
    def validate_values(self) -> Self:
        status, value = _values(self.position)
        exposure = None if value is None else value.copy_abs()
        if (self.status, self.market_value, self.absolute_exposure) != (status, value, exposure):
            raise ValueError("position valuation contradicts input")
        if value is None and self.gross_exposure_share is not None:
            raise ValueError("missing mark cannot have concentration")
        return self


type Calculation = tuple[
    tuple[PositionValuation, ...],
    PortfolioValuationStatus,
    ConcentrationStatus,
    Decimal | None,
    Decimal | None,
    Decimal | None,
]


def _calculate(request: PortfolioContextRequest) -> Calculation:
    with localcontext(_context()):
        values = tuple(_values(position) for position in request.account.positions)
        complete = all(value is not None for _, value in values)
        net = gross = equity = None
        concentration = ConcentrationStatus.INCOMPLETE_VALUATION
        if complete:
            net = Decimal(0)
            gross = Decimal(0)
            for _, value in values:
                assert value is not None
                net += value
                gross += value.copy_abs()
            equity = request.account.cash_balance + net
            concentration = (
                ConcentrationStatus.AVAILABLE
                if gross > 0
                else ConcentrationStatus.ZERO_GROSS_EXPOSURE
            )
        valuations = tuple(
            PositionValuation(
                position=position,
                status=status,
                market_value=value,
                absolute_exposure=None if value is None else value.copy_abs(),
                gross_exposure_share=(
                    value.copy_abs() / gross
                    if value is not None and gross is not None and gross > 0
                    else None
                ),
            )
            for position, (status, value) in zip(request.account.positions, values, strict=True)
        )
        return (
            valuations,
            PortfolioValuationStatus.COMPLETE if complete else PortfolioValuationStatus.INCOMPLETE,
            concentration,
            net,
            gross,
            equity,
        )


class PortfolioContextResult(_Contract):
    request: PortfolioContextRequest
    schema_version: Literal[1] = 1
    valuations: tuple[PositionValuation, ...]
    valuation_status: PortfolioValuationStatus
    concentration_status: ConcentrationStatus
    net_market_value: Amount | None
    gross_exposure: NonnegativeAmount | None
    equity_value: Amount | None

    @field_validator("schema_version", mode="before")
    @classmethod
    def strict_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("schema version must be integer 1")
        return value

    @model_validator(mode="after")
    def validate_derivation(self) -> Self:
        valuations, status, concentration, net, gross, equity = _calculate(self.request)
        if tuple(v.model_dump_json() for v in self.valuations) != tuple(
            v.model_dump_json() for v in valuations
        ):
            raise ValueError("valuations must preserve exact ordered provenance and calculations")
        if (
            self.valuation_status,
            self.concentration_status,
            self.net_market_value,
            self.gross_exposure,
            self.equity_value,
        ) != (status, concentration, net, gross, equity):
            raise ValueError("aggregate context contradicts retained request")
        return self
