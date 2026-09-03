"""Provider-neutral immutable contracts for point-in-time catalyst facts."""

from pydantic import UUID4, Field, ValidationInfo, field_validator, model_validator

from app.core.schemas import (
    CanonicalModel,
    CatalystSourceType,
    Instrument,
    NonBlankStr,
    UtcDatetime,
)
from app.data.observations import SourceIdentity


def _instrument_key(instrument: Instrument) -> tuple[str, str, str, str]:
    return (
        instrument.asset_class.value,
        instrument.exchange or "",
        instrument.symbol,
        instrument.currency or "",
    )


class ObservedCatalystFact(CanonicalModel):
    """One source-supplied catalyst receipt with separate event and knowledge time."""

    observation_id: UUID4
    headline: NonBlankStr
    source: SourceIdentity
    source_type: CatalystSourceType
    observed_at: UtcDatetime
    published_at: UtcDatetime | None = None
    source_record_id: NonBlankStr | None = None
    url: NonBlankStr | None = None
    source_summary: NonBlankStr | None = None
    instruments: tuple[Instrument, ...] = Field(default_factory=tuple)

    @field_validator("instruments", mode="before")
    @classmethod
    def require_tuple(cls, value: object, info: ValidationInfo) -> object:
        if info.mode == "python" and not isinstance(value, tuple):
            raise ValueError("catalyst instruments must be supplied as a tuple")
        return value

    @model_validator(mode="after")
    def validate_instruments(self) -> "ObservedCatalystFact":
        keys = tuple(_instrument_key(item) for item in self.instruments)
        if any(current <= previous for previous, current in zip(keys, keys[1:])):
            raise ValueError("catalyst instruments must be unique and canonically ordered")
        return self


class CatalystMaterializationRequest(CanonicalModel):
    """Explicit source, PIT cutoff, and optional catalyst filters."""

    as_of: UtcDatetime
    source: SourceIdentity
    instrument: Instrument | None = None
    source_type: CatalystSourceType | None = None
    published_start: UtcDatetime | None = None
    published_end: UtcDatetime | None = None

    @model_validator(mode="after")
    def validate_range(self) -> "CatalystMaterializationRequest":
        if (
            self.published_start is not None
            and self.published_end is not None
            and self.published_start >= self.published_end
        ):
            raise ValueError("published_start must be earlier than published_end")
        return self


class MaterializedCatalystHistory(CanonicalModel):
    """Selected catalyst facts plus transparent materialization counts."""

    request: CatalystMaterializationRequest
    facts: tuple[ObservedCatalystFact, ...]
    inspected_fact_count: int
    eligible_fact_count: int

    @field_validator("inspected_fact_count", "eligible_fact_count", mode="before")
    @classmethod
    def strict_count(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("catalyst counts must be nonnegative integers")
        return value

    @model_validator(mode="after")
    def validate_history(self) -> "MaterializedCatalystHistory":
        if self.inspected_fact_count < self.eligible_fact_count:
            raise ValueError("inspected count cannot be smaller than eligible count")
        if self.eligible_fact_count < len(self.facts):
            raise ValueError("eligible count cannot be smaller than selected facts")
        record_ids: set[str] = set()
        for fact in self.facts:
            if fact.source != self.request.source:
                raise ValueError("selected fact source must match request")
            if fact.observed_at > self.request.as_of:
                raise ValueError("selected fact must be known by as_of")
            if (
                self.request.instrument is not None
                and self.request.instrument not in fact.instruments
            ):
                raise ValueError("selected fact instrument must match request")
            if (
                self.request.source_type is not None
                and fact.source_type is not self.request.source_type
            ):
                raise ValueError("selected fact authority must match request")
            if self.request.published_start is not None and (
                fact.published_at is None or fact.published_at < self.request.published_start
            ):
                raise ValueError("selected fact precedes publication range")
            if self.request.published_end is not None and (
                fact.published_at is None or fact.published_at >= self.request.published_end
            ):
                raise ValueError("selected fact exceeds publication range")
            if fact.source_record_id is not None:
                if fact.source_record_id in record_ids:
                    raise ValueError("selected source record revisions must be unique")
                record_ids.add(fact.source_record_id)
        expected = tuple(
            sorted(
                self.facts,
                key=lambda item: (
                    item.published_at is None,
                    item.published_at or item.observed_at,
                    item.observed_at,
                    item.observation_id,
                ),
            )
        )
        if self.facts != expected:
            raise ValueError("selected catalyst facts must be in canonical output order")
        return self
