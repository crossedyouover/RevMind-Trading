"""Strict contracts for the first offline capture slice."""

import hashlib
from datetime import datetime, timedelta
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

from app.core.schemas import (
    CanonicalModel,
    Instrument,
    MarketBar,
    NonBlankStr,
    Timeframe,
    UtcDatetime,
)
from app.data.observations import ObservedMarketData, SourceIdentity
from app.evidence.models import MarketEvidenceConfig
from app.regime.models import TrendRegimeConfig, TrendRegimeResult
from app.research.models import SingleSeriesResearchResult
from app.technical.models import TechnicalAnalysisConfig


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _time(value: object, info: ValidationInfo) -> object:
    if isinstance(value, datetime):
        return value
    if info.mode == "json" and isinstance(value, str):
        return datetime.fromisoformat(value)
    raise ValueError("explicit aware datetime required")


type Time = Annotated[UtcDatetime, BeforeValidator(_time)]
type Digest = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]
type Limit = Annotated[int, Field(strict=True, ge=1, le=10000)]


def _rebuild(value: object) -> object:
    if isinstance(value, Contract):
        return type(value).model_validate(value)
    if isinstance(value, CanonicalModel):
        fields = type(value).model_fields
        if set(value.__dict__) - set(fields):
            raise ValueError("unexpected nested fields")
        return type(value).model_validate({k: _rebuild(getattr(value, k)) for k in fields})
    if isinstance(value, tuple):
        return tuple(_rebuild(v) for v in value)
    if isinstance(value, (dict, list, set)):
        raise ValueError("immutable canonical nested values required")
    return value


def _json_periods(value: object) -> object:
    """Bridge legacy period-array JSON without relaxing Python contract validation."""
    if isinstance(value, dict):
        return {
            k: tuple(v)
            if k in TechnicalAnalysisConfig.model_fields and isinstance(v, list)
            else _json_periods(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_json_periods(v) for v in value]
    return value


class Contract(CanonicalModel):
    model_config = ConfigDict(revalidate_instances="always")

    @field_validator("*", mode="before")
    @classmethod
    def rebuild(cls, value: object, info: ValidationInfo) -> object:
        return _json_periods(value) if info.mode == "json" else _rebuild(value)


class Session(Contract):
    start: Time
    end: Time

    @model_validator(mode="after")
    def bounds(self) -> Self:
        if self.start >= self.end:
            raise ValueError("empty session")
        for value in (self.start, self.end):
            if value.second or value.microsecond:
                raise ValueError("one-minute session alignment required")
        return self


class CapturePolicy(Contract):
    version: NonBlankStr
    source: SourceIdentity
    instrument: Instrument
    provider_binding: Literal["OFFLINE_BATCH_V1"]
    timeframe: Literal[Timeframe.ONE_MINUTE]
    calendar_version: NonBlankStr
    sessions: tuple[Session, ...]
    require_every_interval: Annotated[bool, Field(strict=True)]
    finalization_delay_us: Annotated[int, Field(strict=True, ge=0, le=86400000000)]
    max_bar_age_us: Annotated[int, Field(strict=True, ge=0, le=86400000000)]
    max_range_minutes: Limit
    max_observations: Limit
    page_size: Limit
    max_pages: Limit
    max_artifact_bytes: Annotated[int, Field(strict=True, ge=1024, le=10000000)]
    technical_config: TechnicalAnalysisConfig
    evidence_config: MarketEvidenceConfig
    trend_config: TrendRegimeConfig

    @model_validator(mode="after")
    def validate_sessions(self) -> Self:
        if not self.sessions or len(self.sessions) > 128:
            raise ValueError("explicit bounded sessions required")
        if any(b.start < a.end for a, b in zip(self.sessions, self.sessions[1:])):
            raise ValueError("sessions must be ordered and nonoverlapping")
        return self

    def digest(self) -> str:
        return digest(self.model_dump_json())


class CycleRequest(Contract):
    schema_version: Literal["CAPTURE_V1"]
    mode: Literal["CAPTURE_RESEARCH"]
    cycle_id: UUID4
    policy: CapturePolicy
    policy_digest: Digest
    scheduled_at: Time
    start: Time
    end: Time
    bars: tuple[MarketBar, ...]

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        p = self.policy
        if p.digest() != self.policy_digest:
            raise ValueError("policy digest mismatch")
        if not self.start < self.end <= self.start + timedelta(minutes=p.max_range_minutes):
            raise ValueError("invalid bounded range")
        if any(t.second or t.microsecond for t in (self.start, self.end)):
            raise ValueError("one-minute range alignment required")
        if self.end + timedelta(microseconds=p.finalization_delay_us) > self.scheduled_at:
            raise ValueError("requested bars are not closed at scheduled time")
        if len(self.bars) > p.max_observations:
            raise ValueError("batch cap exceeded")
        expected = frozenset(self.expected_times())
        previous = None
        for bar in self.bars:
            if bar.instrument != p.instrument or bar.timeframe != p.timeframe:
                raise ValueError("batch scope mismatch")
            if not self.start <= bar.timestamp < self.end:
                raise ValueError("bar outside range")
            if bar.timestamp not in expected:
                raise ValueError("bar outside declared session grid")
            if previous is not None and bar.timestamp <= previous:
                raise ValueError("bars must be strictly ordered; no repair")
            previous = bar.timestamp
        return self

    def expected_times(self) -> tuple[datetime, ...]:
        count = (self.end - self.start) // timedelta(minutes=1)
        return tuple(
            t
            for i in range(count)
            if any(
                s.start <= (t := self.start + timedelta(minutes=i)) < s.end
                for s in self.policy.sessions
            )
        )


class SealedInputs(Contract):
    request: CycleRequest
    algorithm_version: Literal["CAPTURE_RESEARCH_V1"]
    acquired_ids: tuple[UUID4, ...]
    receipt_at: Time
    committed_at: Time
    as_of: Time
    sealed_at: Time
    observations: tuple[ObservedMarketData, ...]
    replay_pages: Limit

    @model_validator(mode="after")
    def validate_seal(self) -> Self:
        if not self.request.scheduled_at <= self.receipt_at <= self.committed_at <= self.as_of:
            raise ValueError("invalid capture chronology")
        if self.sealed_at < self.as_of:
            raise ValueError("seal precedes cutoff")
        if len(self.observations) > self.request.policy.max_observations:
            raise ValueError("observation cap exceeded")
        if self.replay_pages > self.request.policy.max_pages:
            raise ValueError("page cap exceeded")
        keys = tuple((o.observed_at, o.observation_id) for o in self.observations)
        if any(b <= a for a, b in zip(keys, keys[1:])):
            raise ValueError("noncanonical knowledge order")
        if any(o.observed_at > self.as_of for o in self.observations):
            raise ValueError("future-known observation")
        by_id = {o.observation_id: o for o in self.observations}
        if len(by_id) != len(self.observations) or len(set(self.acquired_ids)) != len(
            self.acquired_ids
        ):
            raise ValueError("duplicate observation identities")
        if len(self.acquired_ids) != len(self.request.bars):
            raise ValueError("incomplete acquired identity binding")
        for oid, bar in zip(self.acquired_ids, self.request.bars, strict=True):
            o = by_id.get(oid)
            if (
                o is None
                or o.payload != bar
                or o.observed_at != self.receipt_at
                or o.source != self.request.policy.source
            ):
                raise ValueError("acquired observation not retained exactly")
        return self


class CycleResult(Contract):
    cycle_id: UUID4
    sealed_digest: Digest
    completed_at: Time
    research: SingleSeriesResearchResult
    trend: TrendRegimeResult

    @model_validator(mode="after")
    def aligned(self) -> Self:
        if self.research.request.history != self.trend.request.history:
            raise ValueError("research and trend history mismatch")
        if self.trend.request.evaluation_at != self.research.request.history.request.as_of:
            raise ValueError("evaluation cutoff mismatch")
        if self.completed_at < self.trend.request.evaluation_at:
            raise ValueError("completion precedes evaluation")
        return self
