"""Bounded on-demand read-only market-data activation for the local dashboard."""

import os
from collections.abc import Callable
from datetime import datetime
from typing import Literal
from uuid import uuid4

from pydantic import ValidationError

from app.core.schemas import CanonicalModel, MarketSnapshot, UtcDatetime
from app.dashboard.settings import AlpacaFeed, DataMode, SettingsStore
from app.data.ingestion import Clock, MarketDataIngestionCoordinator, SystemUtcClock
from app.data.market import (
    InstrumentNotFoundError,
    InvalidMarketDataRequestError,
    MarketDataUnavailableError,
    ProviderRateLimitError,
)
from app.data.observation_store import ObservationStoreError, SQLiteObservationStore
from app.data.observations import SourceIdentity
from app.data.providers.alpaca import AlpacaInstrumentBinding, AlpacaMarketDataProvider
from app.data.providers.alpaca.config import AlpacaMarketDataSettings


class LiveProbeError(Exception):
    """Safe dashboard-facing error with no provider response or credential content."""


class ProbeQuote(CanonicalModel):
    symbol: str
    exchange: str
    asset_class: str
    currency: str
    last_price: str
    event_at: UtcDatetime


class ProbeReport(CanonicalModel):
    schema_version: Literal[1] = 1
    status: Literal["CONNECTED_READ_ONLY"]
    source: str
    feed: AlpacaFeed
    observed_at: UtcDatetime
    quotes: tuple[ProbeQuote, ...]


class ProbeState(CanonicalModel):
    schema_version: Literal[1] = 1
    status: Literal["NOT_TESTED", "CONNECTED_READ_ONLY", "FAILED"]
    checked_at: UtcDatetime | None = None
    source: str | None = None
    feed: AlpacaFeed | None = None
    observation_count: int = 0
    reason: str | None = None


ProviderFactory = Callable[
    [AlpacaMarketDataSettings, tuple[AlpacaInstrumentBinding, ...]],
    AlpacaMarketDataProvider,
]


class DashboardLiveData:
    """Use frozen provider and ingestion boundaries for one explicit snapshot batch."""

    def __init__(
        self,
        settings: SettingsStore,
        *,
        clock: Clock | None = None,
        provider_factory: ProviderFactory = AlpacaMarketDataProvider,
    ) -> None:
        self._settings = settings
        self._clock = clock or SystemUtcClock()
        self._provider_factory = provider_factory
        self._status_path = settings.directory / "alpaca-status.json"
        self._observations_path = settings.directory / "market-observations.db"

    def state(self) -> ProbeState:
        if not self._status_path.exists():
            return ProbeState(status="NOT_TESTED")
        payload = self._status_path.read_bytes()
        if len(payload) > 8_192:
            raise LiveProbeError("Saved connection status is invalid.")
        try:
            return ProbeState.model_validate_json(payload)
        except ValidationError as exc:
            raise LiveProbeError("Saved connection status is invalid.") from exc

    def invalidate(self) -> None:
        """Make every settings save require a fresh explicit connection test."""
        self._write_state(ProbeState(status="NOT_TESTED"))

    async def probe(self) -> ProbeReport:
        selected = self._settings.load()
        if selected.data_mode is not DataMode.ALPACA:
            raise LiveProbeError("Select Alpaca market data and save settings first.")
        provider_settings = self._settings.alpaca_provider_settings()
        if not provider_settings.available:
            raise LiveProbeError("Save both Alpaca credential fields first.")
        bindings = tuple(
            AlpacaInstrumentBinding(instrument=item.to_instrument(), provider_symbol=item.symbol)
            for item in selected.watchlist
        )
        source = SourceIdentity(name=f"ALPACA_{selected.alpaca_feed.value}")
        provider: AlpacaMarketDataProvider | None = None
        self._settings.directory.mkdir(parents=True, exist_ok=True)
        try:
            provider = self._provider_factory(provider_settings, bindings)
            with SQLiteObservationStore(self._observations_path) as store:
                coordinator = MarketDataIngestionCoordinator(
                    provider,
                    store,
                    source,
                    clock=self._clock,
                    observation_id_factory=uuid4,
                )
                result = await coordinator.ingest_batch_snapshots(
                    [binding.instrument for binding in bindings]
                )
            quotes = tuple(self._quote(observation.payload) for observation in result.observations)
            report = ProbeReport(
                status="CONNECTED_READ_ONLY",
                source=source.name,
                feed=selected.alpaca_feed,
                observed_at=result.observed_at,
                quotes=quotes,
            )
            self._write_state(
                ProbeState(
                    status="CONNECTED_READ_ONLY",
                    checked_at=result.observed_at,
                    source=source.name,
                    feed=selected.alpaca_feed,
                    observation_count=len(quotes),
                )
            )
            return report
        except ProviderRateLimitError as exc:
            self._record_failure("RATE_LIMITED", selected.alpaca_feed)
            raise LiveProbeError("Alpaca rate limit reached; wait before retrying.") from exc
        except InstrumentNotFoundError as exc:
            self._record_failure("INSTRUMENT_NOT_FOUND", selected.alpaca_feed)
            raise LiveProbeError(
                "A configured watchlist symbol was not available from Alpaca."
            ) from exc
        except InvalidMarketDataRequestError as exc:
            self._record_failure("REQUEST_REJECTED", selected.alpaca_feed)
            raise LiveProbeError("Alpaca rejected the bounded market-data request.") from exc
        except (MarketDataUnavailableError, ObservationStoreError) as exc:
            self._record_failure("UNAVAILABLE_OR_UNAUTHORIZED", selected.alpaca_feed)
            raise LiveProbeError(
                "Alpaca data was unavailable or unauthorized; verify the regenerated keys and feed."
            ) from exc
        except (ValidationError, ValueError, TypeError, OSError) as exc:
            self._record_failure("LOCAL_VALIDATION_FAILED", selected.alpaca_feed)
            raise LiveProbeError("The live-data response failed local validation.") from exc
        finally:
            if provider is not None:
                await provider.aclose()

    @staticmethod
    def _quote(payload: object) -> ProbeQuote:
        if not isinstance(payload, MarketSnapshot):
            raise LiveProbeError("Alpaca returned an unexpected market-data type.")
        instrument = payload.instrument
        if instrument.exchange is None or instrument.currency is None:
            raise LiveProbeError("Alpaca returned an incomplete instrument identity.")
        return ProbeQuote(
            symbol=instrument.symbol,
            exchange=instrument.exchange,
            asset_class=instrument.asset_class.value,
            currency=instrument.currency,
            last_price=str(payload.last_price),
            event_at=payload.timestamp,
        )

    def _record_failure(self, reason: str, feed: AlpacaFeed) -> None:
        value = self._clock.now()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise LiveProbeError("The local receipt clock is invalid.")
        self._write_state(ProbeState(status="FAILED", checked_at=value, feed=feed, reason=reason))

    def _write_state(self, state: ProbeState) -> None:
        self._settings.directory.mkdir(parents=True, exist_ok=True)
        temporary = self._status_path.with_name(f".{self._status_path.name}.{uuid4()}.tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(state.model_dump_json(indent=2).encode())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._status_path)
        finally:
            temporary.unlink(missing_ok=True)
