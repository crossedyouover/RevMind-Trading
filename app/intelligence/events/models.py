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
