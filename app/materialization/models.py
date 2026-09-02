"""Immutable contracts for deterministic point-in-time bar materialization."""

from datetime import datetime

from pydantic import UUID4, ValidationError, ValidationInfo, field_validator, model_validator

from app.core.schemas import (
    CanonicalModel,
    Instrument,
    MarketBar,
    NonBlankStr,
    Timeframe,
    UtcDatetime,
)
from app.data.observations import ObservedMarketData, SourceIdentity


class BarSeriesRequest(CanonicalModel):
    """Explicit identity, source, event-time range, and knowledge-time boundary."""

    instrument: Instrument
    timeframe: Timeframe
    source: SourceIdentity
    as_of: UtcDatetime
    start: UtcDatetime | None = None
    end: UtcDatetime | None = None

    @model_validator(mode="after")
    def validate_event_range(self) -> "BarSeriesRequest":
        """Use one unambiguous half-open event-time interval."""
        if self.start is not None and self.end is not None and self.start >= self.end:
            raise ValueError("start must be earlier than end")
        return self


class MaterializedBar(CanonicalModel):
    """One selected bar and the exact observation that established its provenance."""

    bar: MarketBar
    observation_id: UUID4
    observed_at: UtcDatetime
    source: SourceIdentity
    source_record_id: NonBlankStr | None = None

    @classmethod
    def from_observation(cls, observation: ObservedMarketData) -> "MaterializedBar":
        """Construct provenance directly from one trusted bar observation."""
        if not isinstance(observation.payload, MarketBar):
            raise ValueError("materialized provenance requires a market bar observation")
        return cls(
            bar=observation.payload,
            observation_id=observation.observation_id,
            observed_at=observation.observed_at,
            source=observation.source,
            source_record_id=observation.source_record_id,
        )


class MaterializedBarHistory(CanonicalModel):
    """Complete deterministic result for one explicitly requested PIT bar series."""

    request: BarSeriesRequest
    bars: tuple[MaterializedBar, ...]
    inspected_observation_count: int
    eligible_bar_candidate_count: int

    @field_validator("bars", mode="before")
    @classmethod
    def require_tuple(cls, value: object, info: ValidationInfo) -> object:
        """Reject mutable containers and noncanonical Python objects."""
        if info.mode == "python" and not isinstance(value, tuple):
            raise ValueError("materialized bars must be supplied as a tuple")
        if not isinstance(value, (tuple, list)):
            raise ValueError("materialized bars must be a canonical collection")
        if info.mode == "python" and not all(isinstance(item, MaterializedBar) for item in value):
            raise ValueError("history must contain actual MaterializedBar objects")
        return value

    @field_validator(
        "inspected_observation_count", "eligible_bar_candidate_count", mode="before"
    )
    @classmethod
    def require_nonnegative_count(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("materialization counts must be nonnegative integers")
        return value

    @model_validator(mode="after")
    def validate_history(self) -> "MaterializedBarHistory":
        """Reject contradictory identity, provenance, chronology, range, or counts."""
        try:
            trusted_request = BarSeriesRequest.model_validate(
                self.request.model_dump(mode="python", round_trip=True, warnings="none")
            )
        except (ValidationError, AttributeError, TypeError, ValueError) as exc:
            raise ValueError("materialization request must be canonical") from exc
        object.__setattr__(self, "request", trusted_request)
        if self.eligible_bar_candidate_count < len(self.bars):
            raise ValueError("eligible candidate count cannot be smaller than selected bars")
        if self.inspected_observation_count < self.eligible_bar_candidate_count:
            raise ValueError("inspected count cannot be smaller than eligible candidate count")
        previous: datetime | None = None
        for item in self.bars:
            try:
                trusted = MaterializedBar.model_validate(
                    item.model_dump(mode="python", round_trip=True, warnings="none")
                )
            except (ValidationError, AttributeError, TypeError, ValueError) as exc:
                raise ValueError("materialized bar must be canonical") from exc
            bar = trusted.bar
            if bar.instrument != trusted_request.instrument:
                raise ValueError("materialized bar instrument must match request")
            if bar.timeframe is not trusted_request.timeframe:
                raise ValueError("materialized bar timeframe must match request")
            if trusted.source != trusted_request.source:
                raise ValueError("materialized bar source must match request")
            if trusted.observed_at > trusted_request.as_of:
                raise ValueError("materialized bar must be known by request as_of")
            if trusted_request.start is not None and bar.timestamp < trusted_request.start:
                raise ValueError("materialized bar precedes request start")
            if trusted_request.end is not None and bar.timestamp >= trusted_request.end:
                raise ValueError("materialized bar is not before request end")
            if previous is not None and bar.timestamp <= previous:
                raise ValueError("materialized bars must be in strict event-time order")
            previous = bar.timestamp
        return self
