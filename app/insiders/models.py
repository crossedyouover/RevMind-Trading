"""Immutable source assertions for PIT insider transaction research, not trade advice."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import (
    UUID4,
    BeforeValidator,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from app.core.schemas import (
    CanonicalModel,
    Instrument,
    NonBlankStr,
    NonNegativeDecimal,
    UtcDatetime,
)
from app.data.observations import SourceIdentity


def _calendar_date(value: object, info: ValidationInfo) -> object:
    if type(value) is date:
        return value
    if info.mode == "json" and isinstance(value, str) and len(value) == 10:
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("transaction date must be YYYY-MM-DD") from exc
        if parsed.isoformat() == value:
            return parsed
    raise ValueError("transaction date must be a calendar date, not a timestamp")


def _aware_timestamp(value: object, info: ValidationInfo) -> object:
    if isinstance(value, datetime):
        return value
    if info.mode == "json" and isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("JSON timestamp must be an ISO datetime, not an epoch string") from exc
    raise ValueError("timestamp must be an aware datetime, not a date or epoch number")


def _exact_decimal(value: object, info: ValidationInfo) -> object:
    if isinstance(value, Decimal) or (info.mode == "json" and isinstance(value, str)):
        return value
    raise ValueError("reported values must be Decimal, not coerced numbers")


def _instrument(value: object) -> object:
    if isinstance(value, Instrument):
        return Instrument.model_validate(
            value.model_dump(mode="python", round_trip=True, warnings="none")
        )
    return value


def _source(value: object) -> object:
    if isinstance(value, SourceIdentity):
        return SourceIdentity.model_validate(
            value.model_dump(mode="python", round_trip=True, warnings="none")
        )
    return value


type TransactionDate = Annotated[date, BeforeValidator(_calendar_date)]
type KnowledgeTimestamp = Annotated[UtcDatetime, BeforeValidator(_aware_timestamp)]
type ReportedValue = Annotated[NonNegativeDecimal, BeforeValidator(_exact_decimal)]
type CheckedInstrument = Annotated[Instrument, BeforeValidator(_instrument)]
type CheckedSource = Annotated[SourceIdentity, BeforeValidator(_source)]
type ReceiptCount = Annotated[int, Field(strict=True, ge=0)]


class ObservedInsiderTransaction(CanonicalModel):
    """One complete source receipt; dates and monetary assertions are never inferred."""

    observation_id: UUID4
    observed_at: KnowledgeTimestamp
    source: CheckedSource
    instrument: CheckedInstrument
    reporting_person: NonBlankStr
    transaction_code: NonBlankStr
    reporting_role: NonBlankStr | None = None
    transaction_date: TransactionDate | None = None
    filed_at: KnowledgeTimestamp | None = None
    quantity: ReportedValue | None = None
    unit_price: ReportedValue | None = None
    reported_total_value: ReportedValue | None = None
    source_transaction_id: NonBlankStr | None = None
    source_filing_id: NonBlankStr | None = None
    source_revision_id: NonBlankStr | None = None
    source_url: NonBlankStr | None = None

    @field_validator("observation_id", mode="before")
    @classmethod
    def require_uuid(cls, value: object, info: ValidationInfo) -> object:
        if info.mode == "python" and not isinstance(value, UUID):
            raise ValueError("observation identity must be an actual UUID4")
        return value


class InsiderMaterializationRequest(CanonicalModel):
    """Explicit source and knowledge cutoff, with optional post-revision filters."""

    as_of: KnowledgeTimestamp
    source: CheckedSource
    instrument: CheckedInstrument | None = None
    transaction_start: TransactionDate | None = None
    transaction_end: TransactionDate | None = None
    filing_start: KnowledgeTimestamp | None = None
    filing_end: KnowledgeTimestamp | None = None

    @model_validator(mode="after")
    def validate_ranges(self) -> "InsiderMaterializationRequest":
        if (
            self.transaction_start is not None
            and self.transaction_end is not None
            and self.transaction_start >= self.transaction_end
        ):
            raise ValueError("transaction_start must be earlier than transaction_end")
        if (
            self.filing_start is not None
            and self.filing_end is not None
            and self.filing_start >= self.filing_end
        ):
            raise ValueError("filing_start must be earlier than filing_end")
        return self


def _matches(fact: ObservedInsiderTransaction, request: InsiderMaterializationRequest) -> bool:
    """Apply only post-revision filters; source and knowability are validated separately."""
    if request.instrument is not None and fact.instrument != request.instrument:
        return False
    if request.transaction_start is not None and (
        fact.transaction_date is None or fact.transaction_date < request.transaction_start
    ):
        return False
    if request.transaction_end is not None and (
        fact.transaction_date is None or fact.transaction_date >= request.transaction_end
    ):
        return False
    if request.filing_start is not None and (
        fact.filed_at is None or fact.filed_at < request.filing_start
    ):
        return False
    if request.filing_end is not None and (
        fact.filed_at is None or fact.filed_at >= request.filing_end
    ):
        return False
    return True


class MaterializedInsiderHistory(CanonicalModel):
    """Selected full receipts, not proof of source authenticity or input completeness."""

    request: InsiderMaterializationRequest
    facts: tuple[ObservedInsiderTransaction, ...]
    inspected_receipt_count: ReceiptCount
    source_receipt_count: ReceiptCount
    revision_winner_count: ReceiptCount
    matching_winner_count: ReceiptCount

    @field_validator("facts", mode="before")
    @classmethod
    def require_facts(cls, value: object, info: ValidationInfo) -> object:
        if info.mode == "python":
            if not isinstance(value, tuple):
                raise ValueError("selected facts must be an immutable tuple")
            if not all(isinstance(item, ObservedInsiderTransaction) for item in value):
                raise ValueError("selected facts must be actual insider observation objects")
        return value

    @model_validator(mode="after")
    def validate_selection(self) -> "MaterializedInsiderHistory":
        request = InsiderMaterializationRequest.model_validate(
            self.request.model_dump(mode="python", round_trip=True, warnings="none")
        )
        facts = tuple(
            ObservedInsiderTransaction.model_validate(
                fact.model_dump(mode="python", round_trip=True, warnings="none")
            )
            for fact in self.facts
        )
        if not (
            len(facts)
            == self.matching_winner_count
            <= self.revision_winner_count
            <= self.source_receipt_count
            <= self.inspected_receipt_count
        ):
            raise ValueError("receipt and winner counts must be consistent")
        if self.source_receipt_count > 0 and self.revision_winner_count == 0:
            raise ValueError("nonempty source receipts require at least one revision winner")
        filters = (
            request.instrument,
            request.transaction_start,
            request.transaction_end,
            request.filing_start,
            request.filing_end,
        )
        if all(value is None for value in filters):
            if self.matching_winner_count != self.revision_winner_count:
                raise ValueError("unfiltered output must retain every revision winner")
        previous: tuple[datetime, UUID] | None = None
        identifiers: set[UUID] = set()
        transaction_ids: set[str] = set()
        for fact in facts:
            key = (fact.observed_at, fact.observation_id)
            if previous is not None and key <= previous:
                raise ValueError("selected facts must be in strict knowledge order")
            if fact.observation_id in identifiers:
                raise ValueError("selected observation IDs must be unique")
            if fact.source_transaction_id is not None:
                if fact.source_transaction_id in transaction_ids:
                    raise ValueError("selected keyed transaction winners must be unique")
                transaction_ids.add(fact.source_transaction_id)
            if fact.source != request.source:
                raise ValueError("selected source must match the request")
            if fact.observed_at > request.as_of:
                raise ValueError("selected fact must be known by as_of")
            if not _matches(fact, request):
                raise ValueError("selected fact must satisfy every request filter")
            identifiers.add(fact.observation_id)
            previous = key
        object.__setattr__(self, "request", request)
        object.__setattr__(self, "facts", facts)
        return self
