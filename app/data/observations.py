"""Provider-neutral market-data observations and point-in-time eligibility.

Market payload timestamps are event time. ``observed_at`` is the system knowledge boundary:
downstream code evaluating time T must not receive observations whose ``observed_at`` is after T.
This module preserves repeated and multi-source observations without reconciling revisions.
"""

from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import UUID4, Field

from app.core.schemas import (
    CanonicalModel,
    MarketBar,
    MarketSnapshot,
    NonBlankStr,
    UtcDatetime,
)

type MarketPayload = MarketBar | MarketSnapshot


class SourceIdentity(CanonicalModel):
    """Minimal identity of the provider or feed that supplied an observation."""

    name: NonBlankStr


class ObservedMarketData(CanonicalModel):
    """Immutable receipt envelope for a canonical market payload.

    The payload's ``timestamp`` remains authoritative event time. Distinct observation IDs allow
    the same event to be retained from multiple sources or received repeatedly as a revision.
    """

    observation_id: UUID4 = Field(default_factory=uuid4)
    payload: MarketPayload
    observed_at: UtcDatetime
    source: SourceIdentity
    source_record_id: NonBlankStr | None = None

    @property
    def event_time(self) -> datetime:
        """Return authoritative event time from the canonical payload."""
        return self.payload.timestamp

    def is_available_at(self, evaluation_clock: datetime) -> bool:
        """Return whether this observation was known by an aware evaluation clock."""
        if evaluation_clock.tzinfo is None or evaluation_clock.utcoffset() is None:
            raise ValueError("evaluation clock must include timezone information")
        return self.observed_at <= evaluation_clock.astimezone(UTC)


class FakeMarketDataIngestion:
    """Deterministic in-memory observation collection for tests and local validation.

    Available observations are ordered by ``observed_at`` and then ``observation_id``. The helper
    preserves every supplied observation and performs no deduplication or revision reconciliation.
    """

    def __init__(self, observations: Iterable[ObservedMarketData] = ()) -> None:
        self._observations = tuple(observations)

    def available_at(self, evaluation_clock: datetime) -> list[ObservedMarketData]:
        """Return observations known at the evaluation clock in deterministic receipt order."""
        available = [
            observation
            for observation in self._observations
            if observation.is_available_at(evaluation_clock)
        ]
        return sorted(
            available,
            key=lambda observation: (observation.observed_at, observation.observation_id),
        )
