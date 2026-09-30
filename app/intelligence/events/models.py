"""Immutable provider-neutral observations of externally reported global events."""

from enum import StrEnum
from uuid import UUID

from pydantic import UUID4, Field, ValidationInfo, field_validator, model_validator

from app.core.schemas import CanonicalModel, Instrument, NonBlankStr, UtcDatetime
from app.data.observations import SourceIdentity


class GlobalEventCategory(StrEnum):
    """Descriptive event families; never market direction or trading intent."""

    GEOPOLITICAL = "GEOPOLITICAL"
    SANCTIONS_EXPORT_CONTROL = "SANCTIONS_EXPORT_CONTROL"
    MARITIME_DISRUPTION = "MARITIME_DISRUPTION"
    AVIATION_DISRUPTION = "AVIATION_DISRUPTION"
    ENERGY_INFRASTRUCTURE = "ENERGY_INFRASTRUCTURE"
    NATURAL_DISASTER = "NATURAL_DISASTER"
    CYBER_INCIDENT = "CYBER_INCIDENT"
    MACRO_POLICY = "MACRO_POLICY"
    COUNTRY_INSTABILITY = "COUNTRY_INSTABILITY"
    SUPPLY_CHAIN_DISRUPTION = "SUPPLY_CHAIN_DISRUPTION"
    PREDICTION_MARKET = "PREDICTION_MARKET"
    OTHER = "OTHER"


def _instrument_key(instrument: Instrument) -> tuple[str, str, str, str]:
    return (
        instrument.asset_class.value,
        instrument.exchange or "",
        instrument.symbol,
        instrument.currency or "",
    )


class ObservedGlobalEvent(CanonicalModel):
    """One provider receipt with separate source-event and RevMind knowledge times."""

    observation_id: UUID4
    observed_at: UtcDatetime
    source: SourceIdentity
    category: GlobalEventCategory
    headline: NonBlankStr
    occurred_at: UtcDatetime | None = None
    published_at: UtcDatetime | None = None
    source_event_id: NonBlankStr | None = None
    source_revision_id: NonBlankStr | None = None
    source_description: NonBlankStr | None = None
    source_url: NonBlankStr | None = None
    countries: tuple[NonBlankStr, ...] = Field(default_factory=tuple)
    regions: tuple[NonBlankStr, ...] = Field(default_factory=tuple)
    instruments: tuple[Instrument, ...] = Field(default_factory=tuple)

    @field_validator("observation_id", mode="before")
    @classmethod
    def require_uuid(cls, value: object, info: ValidationInfo) -> object:
        if info.mode == "python" and not isinstance(value, UUID):
            raise ValueError("observation identity must be an actual UUID4")
        return value

    @field_validator("countries", "regions", "instruments", mode="before")
    @classmethod
    def require_tuple(cls, value: object, info: ValidationInfo) -> object:
        if info.mode == "python" and not isinstance(value, tuple):
            raise ValueError("global-event collections must be supplied as tuples")
        return value

    @model_validator(mode="after")
    def validate_provenance(self) -> "ObservedGlobalEvent":
        if self.source_revision_id is not None and self.source_event_id is None:
            raise ValueError("source_revision_id requires source_event_id")
        if any(
            current <= previous for previous, current in zip(self.countries, self.countries[1:])
        ):
            raise ValueError("countries must be unique and canonically ordered")
        if any(current <= previous for previous, current in zip(self.regions, self.regions[1:])):
            raise ValueError("regions must be unique and canonically ordered")
        keys = tuple(_instrument_key(item) for item in self.instruments)
        if any(current <= previous for previous, current in zip(keys, keys[1:])):
            raise ValueError("instruments must be unique and canonically ordered")
        return self


class GlobalEventReceiptBatch(CanonicalModel):
    """One immutable source receipt containing canonically ordered global events."""

    source: SourceIdentity
    observed_at: UtcDatetime
    events: tuple[ObservedGlobalEvent, ...]

    @field_validator("events", mode="before")
    @classmethod
    def require_event_tuple(cls, value: object, info: ValidationInfo) -> object:
        if info.mode == "python" and not isinstance(value, tuple):
            raise ValueError("events must be supplied as a tuple")
        return value

    @model_validator(mode="after")
    def validate_receipt(self) -> "GlobalEventReceiptBatch":
        identities: set[UUID] = set()
        previous: tuple[object, UUID] | None = None
        for event in self.events:
            if event.source != self.source:
                raise ValueError("batch events must share the batch source")
            if event.observed_at != self.observed_at:
                raise ValueError("batch events must share the batch observed_at")
            if event.observation_id in identities:
                raise ValueError("batch observation identities must be unique")
            identities.add(event.observation_id)
            key = (event.observed_at, event.observation_id)
            if previous is not None and key <= previous:
                raise ValueError("batch events must be in canonical knowledge order")
            previous = key
        return self


class GlobalEventMaterializationRequest(CanonicalModel):
    """Explicit source, knowledge cutoff, and optional post-receipt filters."""

    as_of: UtcDatetime
    source: SourceIdentity
    category: GlobalEventCategory | None = None
    country: NonBlankStr | None = None
    region: NonBlankStr | None = None
    instrument: Instrument | None = None
    occurred_start: UtcDatetime | None = None
    occurred_end: UtcDatetime | None = None
    published_start: UtcDatetime | None = None
    published_end: UtcDatetime | None = None

    @model_validator(mode="after")
    def validate_ranges(self) -> "GlobalEventMaterializationRequest":
        if (
            self.occurred_start is not None
            and self.occurred_end is not None
            and self.occurred_start >= self.occurred_end
        ):
            raise ValueError("occurred_start must be earlier than occurred_end")
        if (
            self.published_start is not None
            and self.published_end is not None
            and self.published_start >= self.published_end
        ):
            raise ValueError("published_start must be earlier than published_end")
        return self


class MaterializedGlobalEventHistory(CanonicalModel):
    """Selected global-event revisions plus transparent receipt counts."""

    request: GlobalEventMaterializationRequest
    events: tuple[ObservedGlobalEvent, ...]
    inspected_event_count: int = Field(strict=True, ge=0)
    eligible_event_count: int = Field(strict=True, ge=0)

    @model_validator(mode="after")
    def validate_history(self) -> "MaterializedGlobalEventHistory":
        if self.inspected_event_count < self.eligible_event_count:
            raise ValueError("inspected count cannot be smaller than eligible count")
        if self.eligible_event_count < len(self.events):
            raise ValueError("eligible count cannot be smaller than selected events")
        source_ids: set[str] = set()
        for event in self.events:
            if event.source != self.request.source:
                raise ValueError("selected event source must match request")
            if event.observed_at > self.request.as_of:
                raise ValueError("selected event must be known by as_of")
            if not _matches_event_filters(event, self.request):
                raise ValueError("selected event must match request filters")
            if event.source_event_id is not None:
                if event.source_event_id in source_ids:
                    raise ValueError("selected source event revisions must be unique")
                source_ids.add(event.source_event_id)
        expected = tuple(sorted(self.events, key=_global_event_output_key))
        if self.events != expected:
            raise ValueError("selected events must be in canonical output order")
        return self


def _matches_event_filters(
    event: ObservedGlobalEvent, request: GlobalEventMaterializationRequest
) -> bool:
    if request.category is not None and event.category is not request.category:
        return False
    if request.country is not None and request.country not in event.countries:
        return False
    if request.region is not None and request.region not in event.regions:
        return False
    if request.instrument is not None and request.instrument not in event.instruments:
        return False
    if request.occurred_start is not None and (
        event.occurred_at is None or event.occurred_at < request.occurred_start
    ):
        return False
    if request.occurred_end is not None and (
        event.occurred_at is None or event.occurred_at >= request.occurred_end
    ):
        return False
    if request.published_start is not None and (
        event.published_at is None or event.published_at < request.published_start
    ):
        return False
    return not (
        request.published_end is not None
        and (event.published_at is None or event.published_at >= request.published_end)
    )


def _global_event_output_key(event: ObservedGlobalEvent) -> tuple[object, ...]:
    return (
        event.occurred_at is None,
        event.occurred_at or event.observed_at,
        event.published_at is None,
        event.published_at or event.observed_at,
        event.observed_at,
        event.observation_id,
    )
