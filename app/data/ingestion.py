"""Provider-independent coordination of canonical market-data observation writes."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import ValidationError, model_validator

from app.core.schemas import CanonicalModel, Instrument, MarketBar, MarketSnapshot, UtcDatetime
from app.data.market import BarRequest, MarketDataProvider, MarketDataUnavailableError
from app.data.observation_store import ObservationStore
from app.data.observations import ObservedMarketData, SourceIdentity


class IngestionError(Exception):
    """Raised when coordinator-owned observation composition cannot be trusted."""


class Clock(Protocol):
    """Explicit source of the RevMind receipt boundary."""

    def now(self) -> datetime:
        """Return the current timezone-aware instant."""
        ...


class SystemUtcClock:
    """Production UTC clock for market-data receipt boundaries."""

    def now(self) -> datetime:
        """Return the current aware UTC instant."""
        return datetime.now(UTC)


class IngestionResult(CanonicalModel):
    """Immutable result of one completed and persisted provider acquisition."""

    source: SourceIdentity
    observed_at: UtcDatetime
    observations: tuple[ObservedMarketData, ...]

    @model_validator(mode="after")
    def validate_shared_boundary(self) -> "IngestionResult":
        """Require every observation to share the declared source and receipt time."""
        for observation in self.observations:
            if observation.source != self.source:
                raise ValueError("observation source must match result source")
            if observation.observed_at != self.observed_at:
                raise ValueError("observation time must match result observation time")
        return self

    @property
    def count(self) -> int:
        """Return the number of observations persisted by the acquisition."""
        return len(self.observations)


class MarketDataIngestionCoordinator:
    """Fetch canonical payloads, assign receipt time, and append observations."""

    def __init__(
        self,
        provider: MarketDataProvider,
        store: ObservationStore,
        source: SourceIdentity,
        *,
        clock: Clock,
        observation_id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if not isinstance(provider, MarketDataProvider):
            raise TypeError("provider must implement MarketDataProvider")
        if not isinstance(source, SourceIdentity):
            raise TypeError("source must be SourceIdentity")
        if not callable(observation_id_factory):
            raise TypeError("observation_id_factory must be callable")
        self._provider = provider
        self._store = store
        self._source = source
        self._clock = clock
        self._observation_id_factory = observation_id_factory

    async def ingest_snapshot(self, instrument: Instrument) -> IngestionResult:
        """Fetch, envelope, atomically append, and return one canonical snapshot."""
        payload = await self._provider.get_snapshot(instrument)
        observed_at = self._receipt_time()
        if not isinstance(payload, MarketSnapshot) or payload.instrument != instrument:
            raise MarketDataUnavailableError("provider returned an unexpected snapshot")
        observation = self._observation(payload, observed_at)
        self._store.append(observation)
        return self._result(observed_at, (observation,))

    async def ingest_batch_snapshots(
        self, instruments: list[Instrument]
    ) -> IngestionResult:
        """Fetch and atomically append a complete positional snapshot batch."""
        requested_instruments = tuple(instruments)
        payloads = await self._provider.get_batch_snapshots(list(requested_instruments))
        observed_at = self._receipt_time()
        if not isinstance(payloads, list) or len(payloads) != len(requested_instruments):
            raise MarketDataUnavailableError("provider returned an invalid snapshot batch")
        for instrument, payload in zip(requested_instruments, payloads, strict=True):
            if not isinstance(payload, MarketSnapshot) or payload.instrument != instrument:
                raise MarketDataUnavailableError("provider returned an unexpected snapshot")
        observations = tuple(self._observation(payload, observed_at) for payload in payloads)
        if observations:
            self._store.append_many(observations)
        return self._result(observed_at, observations)

    async def ingest_bars(self, request: BarRequest) -> IngestionResult:
        """Fetch and atomically append canonical bars from one historical request."""
        payloads = await self._provider.get_bars(
            request.instrument,
            request.start,
            request.end,
            request.timeframe,
        )
        observed_at = self._receipt_time()
        if not isinstance(payloads, list):
            raise MarketDataUnavailableError("provider returned an invalid bar collection")
        previous_timestamp: datetime | None = None
        for payload in payloads:
            if (
                not isinstance(payload, MarketBar)
                or payload.instrument != request.instrument
                or payload.timeframe != request.timeframe
            ):
                raise MarketDataUnavailableError("provider returned an unexpected bar")
            if not request.start <= payload.timestamp < request.end:
                raise MarketDataUnavailableError(
                    "provider returned a bar outside the request range"
                )
            if previous_timestamp is not None and payload.timestamp <= previous_timestamp:
                raise MarketDataUnavailableError(
                    "provider returned bars outside canonical chronological order"
                )
            previous_timestamp = payload.timestamp
        observations = tuple(self._observation(payload, observed_at) for payload in payloads)
        if observations:
            self._store.append_many(observations)
        return self._result(observed_at, observations)

    def _receipt_time(self) -> datetime:
        value = self._clock.now()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise IngestionError("clock must return a timezone-aware datetime")
        return value.astimezone(UTC)

    def _observation(
        self,
        payload: MarketBar | MarketSnapshot,
        observed_at: datetime,
    ) -> ObservedMarketData:
        try:
            observation_id = self._observation_id_factory()
        except (TypeError, ValueError) as exc:
            raise IngestionError("observation ID factory failed") from exc
        if not isinstance(observation_id, UUID) or observation_id.version != 4:
            raise IngestionError("observation ID factory must return UUID4")
        try:
            return ObservedMarketData(
                observation_id=observation_id,
                payload=payload,
                observed_at=observed_at,
                source=self._source,
                source_record_id=None,
            )
        except (ValidationError, ValueError, TypeError) as exc:
            raise IngestionError("unable to construct market-data observation") from exc

    def _result(
        self,
        observed_at: datetime,
        observations: tuple[ObservedMarketData, ...],
    ) -> IngestionResult:
        try:
            return IngestionResult(
                source=self._source,
                observed_at=observed_at,
                observations=observations,
            )
        except (ValidationError, ValueError, TypeError) as exc:
            raise IngestionError("unable to construct ingestion result") from exc
