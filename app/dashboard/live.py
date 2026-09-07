"""Bounded on-demand read-only market-data activation for the local dashboard."""

import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

from pydantic import ValidationError

from app.core.schemas import CanonicalModel, Instrument, MarketSnapshot, Timeframe, UtcDatetime
from app.dashboard.settings import AlpacaFeed, DataMode, SettingsStore
from app.data.ingestion import Clock, MarketDataIngestionCoordinator, SystemUtcClock
from app.data.market import (
    BarRequest,
    InstrumentNotFoundError,
    InvalidMarketDataRequestError,
    MarketDataUnavailableError,
    ProviderRateLimitError,
)
from app.data.observation_store import ObservationStoreError, SQLiteObservationStore
from app.data.observations import ObservedMarketData, SourceIdentity
from app.data.providers.alpaca import AlpacaInstrumentBinding, AlpacaMarketDataProvider
from app.data.providers.alpaca.config import AlpacaMarketDataSettings
from app.evidence.models import MarketEvidenceConfig
from app.materialization.engine import DeterministicBarMaterializationEngine
from app.materialization.models import BarSeriesRequest
from app.regime.engine import DeterministicTrendRegimeEngine
from app.regime.models import TrendRegimeConfig, TrendRegimeRequest
from app.research.engine import DeterministicSingleSeriesResearchEngine
from app.research.models import SingleSeriesResearchRequest
from app.setups.models import SetupStatus
from app.technical.models import TechnicalAnalysisConfig


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


class ResearchPoint(CanonicalModel):
    event_at: UtcDatetime
    close: str


class MarketResearchRow(CanonicalModel):
    symbol: str
    exchange: str
    timeframe: Timeframe
    observed_at: UtcDatetime
    bar_count: int
    latest_bar_at: UtcDatetime | None
    latest_close: str | None
    trend_status: str
    trend: str | None
    active_setups: tuple[str, ...]
    action: Literal["REVIEW", "WAIT", "NO_DATA"]
    explanation: str
    chart: tuple[ResearchPoint, ...]


class MarketResearchReport(CanonicalModel):
    schema_version: Literal[1] = 1
    status: Literal["COMPLETE_READ_ONLY"]
    source: str
    feed: AlpacaFeed
    requested_start: UtcDatetime
    requested_end: UtcDatetime
    completed_at: UtcDatetime
    rows: tuple[MarketResearchRow, ...]


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

    async def research(self) -> MarketResearchReport:
        """Acquire completed historical bars and run deterministic read-only research."""
        selected = self._settings.load()
        if selected.data_mode is not DataMode.ALPACA:
            raise LiveProbeError("Select Alpaca market data and save settings first.")
        provider_settings = self._settings.alpaca_provider_settings()
        if not provider_settings.available:
            raise LiveProbeError("Save both Alpaca credential fields first.")
        start, end = self._research_window(selected.timeframe)
        bindings = tuple(
            AlpacaInstrumentBinding(instrument=item.to_instrument(), provider_symbol=item.symbol)
            for item in selected.watchlist
        )
        source = SourceIdentity(name=f"ALPACA_{selected.alpaca_feed.value}")
        provider: AlpacaMarketDataProvider | None = None
        rows: list[MarketResearchRow] = []
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
                for binding in bindings:
                    acquired = await coordinator.ingest_bars(
                        BarRequest(
                            instrument=binding.instrument,
                            start=start,
                            end=end,
                            timeframe=selected.timeframe,
                        )
                    )
                    rows.append(
                        self._research_row(
                            binding.instrument,
                            selected.timeframe,
                            source,
                            acquired.observed_at,
                            start,
                            end,
                            acquired.observations,
                        )
                    )
            return MarketResearchReport(
                status="COMPLETE_READ_ONLY",
                source=source.name,
                feed=selected.alpaca_feed,
                requested_start=start,
                requested_end=end,
                completed_at=self._receipt_time(),
                rows=tuple(rows),
            )
        except ProviderRateLimitError as exc:
            raise LiveProbeError("Alpaca rate limit reached; wait before retrying.") from exc
        except InstrumentNotFoundError as exc:
            raise LiveProbeError("A watchlist symbol was not available from Alpaca.") from exc
        except InvalidMarketDataRequestError as exc:
            raise LiveProbeError("Alpaca rejected the bounded historical-bar request.") from exc
        except (MarketDataUnavailableError, ObservationStoreError) as exc:
            raise LiveProbeError("Historical Alpaca data was unavailable or unauthorized.") from exc
        except (ValidationError, ValueError, TypeError, OSError) as exc:
            raise LiveProbeError("The historical research result failed local validation.") from exc
        finally:
            if provider is not None:
                await provider.aclose()

    def _research_window(self, timeframe: Timeframe) -> tuple[datetime, datetime]:
        now = self._receipt_time()
        seconds = {
            Timeframe.ONE_MINUTE: 60,
            Timeframe.FIVE_MINUTES: 300,
            Timeframe.FIFTEEN_MINUTES: 900,
            Timeframe.ONE_HOUR: 3_600,
            Timeframe.ONE_DAY: 86_400,
        }[timeframe]
        delayed = now - timedelta(minutes=20)
        boundary = datetime.fromtimestamp(int(delayed.timestamp()) // seconds * seconds, UTC)
        end = boundary - timedelta(microseconds=1)
        days = {
            Timeframe.ONE_MINUTE: 7,
            Timeframe.FIVE_MINUTES: 14,
            Timeframe.FIFTEEN_MINUTES: 30,
            Timeframe.ONE_HOUR: 90,
            Timeframe.ONE_DAY: 365,
        }[timeframe]
        return boundary - timedelta(days=days), end

    def _receipt_time(self) -> datetime:
        value = self._clock.now()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise LiveProbeError("The local receipt clock is invalid.")
        return value.astimezone(UTC)

    @staticmethod
    def _research_row(
        instrument: Instrument,
        timeframe: Timeframe,
        source: SourceIdentity,
        observed_at: datetime,
        start: datetime,
        end: datetime,
        observations: tuple[ObservedMarketData, ...],
    ) -> MarketResearchRow:
        if not isinstance(instrument, Instrument) or not all(
            isinstance(item, ObservedMarketData) for item in observations
        ):
            raise ValueError("research inputs must be canonical")
        ordered = tuple(
            sorted(observations, key=lambda item: (item.observed_at, item.observation_id))
        )
        history = DeterministicBarMaterializationEngine().materialize(
            ordered,
            BarSeriesRequest(
                instrument=instrument,
                timeframe=timeframe,
                source=source,
                as_of=observed_at,
                start=start,
                end=end,
            ),
        )
        research = DeterministicSingleSeriesResearchEngine().analyze(
            SingleSeriesResearchRequest(
                history=history,
                technical_config=TechnicalAnalysisConfig(),
                evidence_config=MarketEvidenceConfig(),
            )
        )
        trend = DeterministicTrendRegimeEngine().analyze(
            TrendRegimeRequest(
                history=history,
                config=TrendRegimeConfig(sma_period=20, return_period=1),
                evaluation_at=observed_at,
            )
        )
        latest_bar = history.bars[-1].bar if history.bars else None
        latest_trend = trend.snapshots[-1] if trend.snapshots else None
        latest_setup = research.setup_snapshots[-1] if research.setup_snapshots else None
        active = (
            tuple(
                item.key.value
                for item in latest_setup.setups
                if item.status is SetupStatus.ACTIVE
            )
            if latest_setup is not None
            else ()
        )
        action: Literal["REVIEW", "WAIT", "NO_DATA"] = (
            "NO_DATA" if latest_bar is None else "REVIEW" if active else "WAIT"
        )
        explanation = (
            "No completed bars were returned for this bounded window."
            if latest_bar is None
            else "An active descriptive setup needs your review and separate risk checks."
            if active
            else "No active frozen setup is present on the latest completed bar."
        )
        return MarketResearchRow(
            symbol=instrument.symbol,
            exchange=instrument.exchange or "UNKNOWN",
            timeframe=timeframe,
            observed_at=observed_at,
            bar_count=len(history.bars),
            latest_bar_at=latest_bar.timestamp if latest_bar else None,
            latest_close=str(latest_bar.close) if latest_bar else None,
            trend_status=latest_trend.status.value if latest_trend else "NO_DATA",
            trend=latest_trend.regime.value if latest_trend and latest_trend.regime else None,
            active_setups=active,
            action=action,
            explanation=explanation,
            chart=tuple(
                ResearchPoint(event_at=item.bar.timestamp, close=str(item.bar.close))
                for item in history.bars[-120:]
            ),
        )

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
