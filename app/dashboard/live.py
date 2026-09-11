"""Bounded on-demand read-only market-data activation for the local dashboard."""

import os
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal
from typing import Literal
from uuid import uuid4

from pydantic import SecretStr, ValidationError

from app.broker.alpaca import AlpacaPaperBroker, PaperBrokerError
from app.broker.models import PaperAccount, PaperOrderReceipt, PaperOrderRequest, PaperOrderStatus
from app.broker.protocol import PaperBroker
from app.core.schemas import CanonicalModel, Instrument, MarketSnapshot, Timeframe, UtcDatetime
from app.dashboard.settings import AlpacaFeed, DataMode, SettingsStore, ValidationDepth
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
from app.desks.engine import DeterministicAdvisoryDeskEngine
from app.desks.models import SetupDeskRequest, TrendDeskRequest
from app.evaluation.backtest import BacktestSummary, evaluate_frozen_setups, grade_setup
from app.evidence.models import MarketEvidenceConfig
from app.materialization.engine import DeterministicBarMaterializationEngine
from app.materialization.models import BarSeriesRequest
from app.orchestration.desk import DeterministicHeadOfDeskEngine
from app.orchestration.models import HeadOfDeskPolicy, HeadOfDeskRequest
from app.portfolio.engine import DeterministicPortfolioContextEngine
from app.portfolio.models import (
    ObservedPaperAccountState,
    ObservedPositionMark,
    PortfolioContextRequest,
)
from app.regime.engine import DeterministicTrendRegimeEngine
from app.regime.models import TrendRegimeConfig, TrendRegimeRequest, TrendRegimeResult
from app.research.engine import DeterministicSingleSeriesResearchEngine
from app.research.models import SingleSeriesResearchRequest, SingleSeriesResearchResult
from app.risk.engine import DeterministicPaperRiskEngine
from app.risk.models import PaperRiskPolicy, PaperRiskProposal, PaperRiskRequest
from app.setups.models import SetupKey, SetupStatus
from app.technical.models import TechnicalAnalysisConfig, TechnicalFeatureKey


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
    assessment_id: str | None
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
    trend_comment: str
    opportunity_comment: str
    price_location: str
    next_step: str
    chart: tuple[ResearchPoint, ...]
    backtest: BacktestSummary
    evidence_grade: Literal["INSUFFICIENT", "WEAK", "PROMISING", "NOT_APPLICABLE"]
    evidence_score: str | None
    evidence_comment: str
    market_alignment: Literal["SUPPORTS", "CONTRADICTS", "NEUTRAL", "UNAVAILABLE"]
    market_comment: str
    opportunity_rank: int | None = None


class MarketResearchReport(CanonicalModel):
    schema_version: Literal[1] = 1
    status: Literal["COMPLETE_READ_ONLY"]
    source: str
    feed: AlpacaFeed
    requested_start: UtcDatetime
    requested_end: UtcDatetime
    completed_at: UtcDatetime
    validation_depth: ValidationDepth
    rows: tuple[MarketResearchRow, ...]


class PaperPlanInput(CanonicalModel):
    assessment_id: str
    side: Literal["BUY", "SELL"]
    quantity: Decimal
    cash_balance: Decimal
    max_trade_notional: Decimal
    max_gross_exposure: Decimal
    max_instrument_exposure: Decimal
    max_concentration_share: Decimal
    min_cash_balance: Decimal
    stop_price: Decimal
    max_loss_budget: Decimal
    auto_size: bool = False


class PaperPlanResult(CanonicalModel):
    schema_version: Literal[1] = 1
    status: Literal["ELIGIBLE_FOR_PAPER_REVIEW", "VETOED", "NOT_ACTIONABLE"]
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: str
    reference_price: str
    stop_price: str
    target_price: str
    estimated_loss_at_stop: str
    risk_status: str
    risk_reasons: tuple[str, ...]
    desk_disposition: str
    desk_reasons: tuple[str, ...]
    projected_cash: str | None
    projected_gross_exposure: str | None
    approval_id: str | None
    explanation: str
    quantity_source: Literal["USER_ENTERED", "AUTOMATIC_SAFE_SIZE"] = "USER_ENTERED"
    sizing_basis: tuple[str, ...] = ()


class PaperApprovalInput(CanonicalModel):
    approval_id: str
    confirmation: Literal["PLACE PAPER ORDER"]


class PaperCancelInput(CanonicalModel):
    client_order_id: str
    confirmation: Literal["CANCEL PAPER ORDER"]


class _ResearchArtifact:
    def __init__(
        self,
        instrument: Instrument,
        observed_at: datetime,
        research: SingleSeriesResearchResult,
        trend: TrendRegimeResult,
    ) -> None:
        self.instrument = instrument
        self.observed_at = observed_at
        self.research = research
        self.trend = trend


ProviderFactory = Callable[
    [AlpacaMarketDataSettings, tuple[AlpacaInstrumentBinding, ...]],
    AlpacaMarketDataProvider,
]
PaperBrokerFactory = Callable[[SecretStr, SecretStr], PaperBroker]


class DashboardLiveData:
    """Use frozen provider and ingestion boundaries for one explicit snapshot batch."""

    def __init__(
        self,
        settings: SettingsStore,
        *,
        clock: Clock | None = None,
        provider_factory: ProviderFactory = AlpacaMarketDataProvider,
        paper_broker_factory: PaperBrokerFactory = AlpacaPaperBroker,
    ) -> None:
        self._settings = settings
        self._clock = clock or SystemUtcClock()
        self._provider_factory = provider_factory
        self._paper_broker_factory = paper_broker_factory
        self._status_path = settings.directory / "alpaca-status.json"
        self._observations_path = settings.directory / "market-observations.db"
        self._paper_orders_path = settings.directory / "paper-orders.db"
        self._research_artifacts: dict[str, _ResearchArtifact] = {}
        self._approved_plans: dict[str, PaperOrderRequest] = {}
        self._paper_account: PaperAccount | None = None

    async def paper_account(self) -> PaperAccount:
        selected = self._settings.load()
        if selected.data_mode is not DataMode.ALPACA:
            raise LiveProbeError("Select Alpaca and save settings first.")
        try:
            key, secret = self._settings.alpaca_credentials()
            broker = self._paper_broker_factory(key, secret)
            try:
                account = await broker.account()
                self._paper_account = account
                return account
            finally:
                await broker.aclose()
        except (PaperBrokerError, ValidationError, ValueError, TypeError) as exc:
            raise LiveProbeError(
                "The Alpaca paper account could not be read. Confirm these are Paper Trading keys."
            ) from exc

    async def place_paper_order(self, value: PaperApprovalInput) -> PaperOrderReceipt:
        approval = PaperApprovalInput.model_validate(value)
        order = self._approved_plans.pop(approval.approval_id, None)
        if order is None:
            raise LiveProbeError("That eligible paper plan expired. Analyze and check it again.")
        self._record_order(order, None, "PENDING")
        try:
            key, secret = self._settings.alpaca_credentials()
            broker = self._paper_broker_factory(key, secret)
            try:
                receipt = await broker.place_bracket_order(order)
                self._record_order(order, receipt, "ACCEPTED")
                return receipt
            finally:
                await broker.aclose()
        except (PaperBrokerError, ValidationError, ValueError, TypeError) as exc:
            self._record_order(order, None, "REJECTED_OR_UNKNOWN")
            raise LiveProbeError(
                "Alpaca rejected the paper order. No live order was attempted."
            ) from exc

    def paper_order_history(self) -> tuple[dict[str, str | None], ...]:
        if not self._paper_orders_path.exists():
            return ()
        with sqlite3.connect(self._paper_orders_path) as db:
            rows = db.execute(
                "SELECT client_order_id,symbol,side,quantity,status,provider_order_id,"
                "submitted_at,request FROM paper_orders ORDER BY created_at DESC LIMIT 50"
            ).fetchall()
        history: list[dict[str, str | None]] = []
        for row in rows:
            request = PaperOrderRequest.model_validate_json(row[7])
            planned_loss = request.quantity * abs(request.entry_limit - request.stop_price)
            history.append(
                {
                    "client_order_id": row[0],
                    "symbol": row[1],
                    "side": row[2],
                    "quantity": row[3],
                    "status": row[4],
                    "provider_order_id": row[5],
                    "submitted_at": row[6],
                    "next_action": self._paper_order_next_action(row[4]),
                    "entry_limit": str(request.entry_limit),
                    "stop_price": str(request.stop_price),
                    "target_price": str(request.target_price),
                    "planned_loss": str(planned_loss),
                    "protection_note": (
                        "Bracket requested with Alpaca; verify active child orders "
                        "after the entry fills."
                    ),
                }
            )
        return tuple(history)

    async def sync_paper_orders(self) -> tuple[dict[str, str | None], ...]:
        """Explicitly refresh recent known orders; never mutate or cancel provider orders."""
        known = tuple(
            row for row in self.paper_order_history() if row["provider_order_id"] is not None
        )[:10]
        if not known:
            return ()
        try:
            key, secret = self._settings.alpaca_credentials()
            broker = self._paper_broker_factory(key, secret)
            try:
                for row in known:
                    provider_id = row["provider_order_id"]
                    if provider_id is None:
                        continue
                    status = await broker.order_status(provider_id)
                    self._update_order_status(status)
            finally:
                await broker.aclose()
        except (PaperBrokerError, ValidationError, ValueError, TypeError) as exc:
            raise LiveProbeError(
                "Recent paper-order status could not be refreshed. No order was changed."
            ) from exc
        return self.paper_order_history()

    async def cancel_paper_order(
        self, value: PaperCancelInput
    ) -> tuple[dict[str, str | None], ...]:
        """Cancel one journaled active paper order after exact explicit confirmation."""
        cancellation = PaperCancelInput.model_validate(value)
        if not self._paper_orders_path.exists():
            raise LiveProbeError("That RevMind paper order does not exist.")
        with sqlite3.connect(self._paper_orders_path) as db:
            row = db.execute(
                "SELECT provider_order_id,status FROM paper_orders WHERE client_order_id=?",
                (cancellation.client_order_id,),
            ).fetchone()
        if row is None or row[0] is None:
            raise LiveProbeError("That RevMind paper order cannot be cancelled.")
        active = {"ACCEPTED", "NEW", "PENDING_NEW", "PARTIALLY_FILLED", "HELD", "CALCULATED"}
        if str(row[1]).upper() not in active:
            raise LiveProbeError("That paper order is not in a cancellable state.")
        try:
            key, secret = self._settings.alpaca_credentials()
            broker = self._paper_broker_factory(key, secret)
            try:
                await broker.cancel_order(str(row[0]))
            finally:
                await broker.aclose()
        except (PaperBrokerError, ValidationError, ValueError, TypeError) as exc:
            raise LiveProbeError(
                "Alpaca did not confirm the paper cancellation. Refresh its status before retrying."
            ) from exc
        with sqlite3.connect(self._paper_orders_path) as db:
            db.execute(
                "UPDATE paper_orders SET status=? WHERE client_order_id=?",
                ("CANCEL_REQUESTED", cancellation.client_order_id),
            )
        return self.paper_order_history()

    def _update_order_status(self, status: PaperOrderStatus) -> None:
        with sqlite3.connect(self._paper_orders_path) as db:
            existing = db.execute(
                "SELECT provider_order_id FROM paper_orders WHERE client_order_id=?",
                (status.client_order_id,),
            ).fetchone()
            if existing is None or existing[0] != status.provider_order_id:
                raise LiveProbeError("Paper-order status identity conflict.")
            db.execute(
                "UPDATE paper_orders SET status=?,receipt=? WHERE client_order_id=?",
                (status.status.upper(), status.model_dump_json(), status.client_order_id),
            )

    @staticmethod
    def _paper_order_next_action(status: str) -> str:
        normalized = status.upper()
        if normalized == "FILLED":
            return "Filled: verify the position and protective bracket in Alpaca Paper Trading."
        if normalized in {"CANCELED", "EXPIRED", "REJECTED", "REJECTED_OR_UNKNOWN"}:
            return "Not active: review the reason in Alpaca before creating another plan."
        if normalized in {"PARTIALLY_FILLED"}:
            return "Partially filled: inspect the remaining quantity and bracket in Alpaca."
        if normalized == "CANCEL_REQUESTED":
            return "Cancellation requested: refresh status for Alpaca confirmation."
        return "Working: wait or inspect the order in Alpaca Paper Trading."

    def _record_order(
        self, order: PaperOrderRequest, receipt: PaperOrderReceipt | None, status: str
    ) -> None:
        self._settings.directory.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._paper_orders_path) as db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute(
                """CREATE TABLE IF NOT EXISTS paper_orders (
                client_order_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, side TEXT NOT NULL,
                quantity TEXT NOT NULL, request TEXT NOT NULL, status TEXT NOT NULL,
                provider_order_id TEXT, submitted_at TEXT, receipt TEXT, created_at TEXT NOT NULL
                )"""
            )
            existing = db.execute(
                "SELECT request FROM paper_orders WHERE client_order_id=?",
                (order.client_order_id,),
            ).fetchone()
            encoded = order.model_dump_json()
            if existing is not None and existing[0] != encoded:
                raise LiveProbeError("Paper-order journal identity conflict.")
            if existing is None:
                db.execute(
                    "INSERT INTO paper_orders VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (order.client_order_id, order.symbol, order.side, str(order.quantity),
                     encoded, status, None, None, None, self._receipt_time().isoformat()),
                )
            else:
                db.execute(
                    "UPDATE paper_orders SET status=?,provider_order_id=?,submitted_at=?,receipt=? "
                    "WHERE client_order_id=?",
                    (status, receipt.provider_order_id if receipt else None,
                     receipt.submitted_at.isoformat() if receipt else None,
                     receipt.model_dump_json() if receipt else None, order.client_order_id),
                )

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
        start, end = self._research_window(selected.timeframe, selected.validation_depth)
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
                    row, artifact = self._research_row(
                        binding.instrument,
                        selected.timeframe,
                        source,
                        acquired.observed_at,
                        start,
                        end,
                        acquired.observations,
                    )
                    if row.assessment_id is not None:
                        self._research_artifacts[row.assessment_id] = artifact
                    rows.append(row)
            benchmark = next((item for item in rows if item.symbol == "SPY"), None)
            rows = [self._with_market_alignment(item, benchmark) for item in rows]
            grade_order = {"PROMISING": 0, "WEAK": 1, "INSUFFICIENT": 2, "NOT_APPLICABLE": 3}
            alignment_order = {"SUPPORTS": 0, "NEUTRAL": 1, "UNAVAILABLE": 2, "CONTRADICTS": 3}
            rows.sort(
                key=lambda item: (
                    item.action != "REVIEW",
                    alignment_order[item.market_alignment],
                    grade_order[item.evidence_grade],
                    -(
                        Decimal(item.evidence_score)
                        if item.evidence_score is not None
                        else Decimal("-999999")
                    ),
                    item.symbol,
                )
            )
            ranked: list[MarketResearchRow] = []
            rank = 0
            for item in rows:
                item_rank = None
                if (
                    item.action == "REVIEW"
                    and item.market_alignment != "CONTRADICTS"
                    and item.evidence_score is not None
                ):
                    rank += 1
                    item_rank = rank
                ranked.append(item.model_copy(update={"opportunity_rank": item_rank}))
            while len(self._research_artifacts) > 100:
                self._research_artifacts.pop(next(iter(self._research_artifacts)))
            return MarketResearchReport(
                status="COMPLETE_READ_ONLY",
                source=source.name,
                feed=selected.alpaca_feed,
                requested_start=start,
                requested_end=end,
                completed_at=self._receipt_time(),
                validation_depth=selected.validation_depth,
                rows=tuple(ranked),
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

    def _research_window(
        self, timeframe: Timeframe, depth: ValidationDepth
    ) -> tuple[datetime, datetime]:
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
        standard_days = {
            Timeframe.ONE_MINUTE: 7,
            Timeframe.FIVE_MINUTES: 14,
            Timeframe.FIFTEEN_MINUTES: 30,
            Timeframe.ONE_HOUR: 90,
            Timeframe.ONE_DAY: 365,
        }[timeframe]
        extended_days = {
            Timeframe.ONE_MINUTE: 14,
            Timeframe.FIVE_MINUTES: 45,
            Timeframe.FIFTEEN_MINUTES: 120,
            Timeframe.ONE_HOUR: 365,
            Timeframe.ONE_DAY: 1_825,
        }[timeframe]
        days = extended_days if depth is ValidationDepth.EXTENDED else standard_days
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
    ) -> tuple[MarketResearchRow, _ResearchArtifact]:
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
        latest_technical = (
            research.technical_snapshots[-1] if research.technical_snapshots else None
        )
        active = (
            tuple(
                item.key.value for item in latest_setup.setups if item.status is SetupStatus.ACTIVE
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
        values = {
            item.key: item.value
            for item in latest_technical.features
            if item.value is not None and item.period == 20
        } if latest_technical is not None else {}
        sma = values.get(TechnicalFeatureKey.SMA_CLOSE)
        prior_high = values.get(TechnicalFeatureKey.ROLLING_HIGHEST_HIGH)
        prior_low = values.get(TechnicalFeatureKey.ROLLING_LOWEST_LOW)
        trend_name = (
            latest_trend.regime.value.lower()
            if latest_trend and latest_trend.regime
            else None
        )
        trend_comment = (
            f"The 20-bar trend model is {trend_name}."
            if trend_name is not None
            else "There is not enough usable trend evidence yet."
        )
        opportunity_comment = (
            "The close is above its 20-bar average and broke above the prior 20-bar high."
            if SetupKey.UPSIDE_BREAKOUT_ABOVE_SMA.value in active
            else "The close is below its 20-bar average and broke below the prior 20-bar low."
            if SetupKey.DOWNSIDE_BREAKDOWN_BELOW_SMA.value in active
            else "No entry yet: neither the upside breakout pair nor the downside breakdown pair "
            "is simultaneously active."
        )
        prior_range = (
            f"${prior_low}–${prior_high}"
            if prior_low is not None and prior_high is not None
            else "unavailable"
        )
        price_location = (
            f"Latest close ${latest_bar.close}; 20-bar average "
            f"{f'${sma}' if sma is not None else 'unavailable'}; prior range "
            f"{prior_range}."
            if latest_bar is not None
            else "No completed price location is available."
        )
        next_step = (
            "Open the trade planner, choose a stop and loss budget, then let risk decide."
            if active
            else "Wait. Check again after another completed bar; do not force a trade."
        )
        backtest = evaluate_frozen_setups(research)
        evidence_grade: Literal["INSUFFICIENT", "WEAK", "PROMISING", "NOT_APPLICABLE"]
        if active:
            active_key = SetupKey(active[0])
            held_out = next(
                item for item in backtest.out_of_sample_results if item.setup is active_key
            )
            evidence_grade, evidence_score, evidence_comment = grade_setup(held_out)
        else:
            evidence_grade = "NOT_APPLICABLE"
            evidence_score = None
            evidence_comment = "No active setup exists to rank."
        assessment_id = str(uuid4()) if latest_bar is not None else None
        row = MarketResearchRow(
            assessment_id=assessment_id,
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
            trend_comment=trend_comment,
            opportunity_comment=opportunity_comment,
            price_location=price_location,
            next_step=next_step,
            chart=tuple(
                ResearchPoint(event_at=item.bar.timestamp, close=str(item.bar.close))
                for item in history.bars[-120:]
            ),
            backtest=backtest,
            evidence_grade=evidence_grade,
            evidence_score=str(evidence_score) if evidence_score is not None else None,
            evidence_comment=evidence_comment,
            market_alignment="UNAVAILABLE",
            market_comment="Benchmark context has not been composed yet.",
        )
        return row, _ResearchArtifact(instrument, observed_at, research, trend)

    @staticmethod
    def _with_market_alignment(
        row: MarketResearchRow, benchmark: MarketResearchRow | None
    ) -> MarketResearchRow:
        if not row.active_setups:
            alignment: Literal["SUPPORTS", "CONTRADICTS", "NEUTRAL", "UNAVAILABLE"] = (
                "NEUTRAL"
            )
            comment = "No active trade direction exists to confirm against the broad market."
        elif benchmark is None or benchmark.trend is None:
            alignment = "UNAVAILABLE"
            comment = "SPY benchmark trend is unavailable; no market confirmation is claimed."
        else:
            is_long = row.active_setups[0] == SetupKey.UPSIDE_BREAKOUT_ABOVE_SMA.value
            supports = (is_long and benchmark.trend == "UPWARD") or (
                not is_long and benchmark.trend == "DOWNWARD"
            )
            contradicts = (is_long and benchmark.trend == "DOWNWARD") or (
                not is_long and benchmark.trend == "UPWARD"
            )
            alignment = "SUPPORTS" if supports else "CONTRADICTS" if contradicts else "NEUTRAL"
            direction = "long" if is_long else "short"
            if supports:
                relationship = "supports"
            elif contradicts:
                relationship = "contradicts"
            else:
                relationship = "is neutral for"
            comment = (
                f"SPY trend is {benchmark.trend.lower()} and {relationship} "
                f"this {direction} setup."
            )
        return row.model_copy(
            update={"market_alignment": alignment, "market_comment": comment}
        )

    def paper_plan(self, value: PaperPlanInput) -> PaperPlanResult:
        """Evaluate a user-entered hypothetical plan; never submit or persist an order."""
        request = PaperPlanInput.model_validate(value)
        artifact = self._research_artifacts.get(request.assessment_id)
        if artifact is None:
            raise LiveProbeError("That research result expired. Analyze the watchlist again.")
        positive = (
            request.quantity > 0
            and request.cash_balance >= 0
            and request.max_trade_notional > 0
            and request.max_gross_exposure > 0
            and request.max_instrument_exposure > 0
            and Decimal("0") < request.max_concentration_share <= Decimal("1")
            and request.min_cash_balance >= 0
            and request.stop_price > 0
            and request.max_loss_budget > 0
        )
        if not positive:
            raise LiveProbeError(
                "Paper-plan amounts must be positive and concentration must be 0–1."
            )
        research = artifact.research
        trend = artifact.trend
        latest = research.request.history.bars[-1].bar if research.request.history.bars else None
        if latest is None:
            raise LiveProbeError("No completed reference bar is available for this plan.")
        quantity = request.quantity
        cash_balance = request.cash_balance
        quantity_source: Literal["USER_ENTERED", "AUTOMATIC_SAFE_SIZE"] = "USER_ENTERED"
        sizing_basis: tuple[str, ...] = ()
        if request.auto_size:
            synced = self._paper_account
            if synced is None:
                raise LiveProbeError("Sync the Alpaca paper account before using automatic sizing.")
            if synced.trading_blocked:
                raise LiveProbeError(
                    "Alpaca reports that this paper account is blocked from trading."
                )
            if request.side != "BUY":
                raise LiveProbeError(
                    "Automatic sizing currently supports long paper plans only; "
                    "shorting remains blocked."
                )
            per_share_risk = latest.close - request.stop_price
            if per_share_risk <= 0:
                raise LiveProbeError("For a long plan, the stop must be below the entry price.")
            cash_balance = synced.cash
            spendable_cash = max(Decimal("0"), cash_balance - request.min_cash_balance)
            existing_gross = sum(abs(position.market_value) for position in synced.positions)
            existing_instrument = sum(
                abs(position.market_value)
                for position in synced.positions
                if position.symbol == artifact.instrument.symbol
            )
            gross_headroom = max(Decimal("0"), request.max_gross_exposure - existing_gross)
            instrument_headroom = max(
                Decimal("0"), request.max_instrument_exposure - existing_instrument
            )
            concentration_headroom = max(
                Decimal("0"),
                synced.equity * request.max_concentration_share - existing_instrument,
            )
            caps = (
                ("loss budget", request.max_loss_budget / per_share_risk),
                ("maximum trade value", request.max_trade_notional / latest.close),
                ("maximum total exposure", gross_headroom / latest.close),
                ("maximum one-symbol exposure", instrument_headroom / latest.close),
                ("maximum account concentration", concentration_headroom / latest.close),
                ("paper cash kept available", spendable_cash / latest.close),
            )
            limiting_name, limiting_quantity = min(caps, key=lambda item: (item[1], item[0]))
            quantity = limiting_quantity.to_integral_value(rounding=ROUND_FLOOR)
            if quantity < 1:
                raise LiveProbeError(
                    "No whole share fits the selected limits; "
                    f"the binding limit is {limiting_name}."
                )
            quantity_source = "AUTOMATIC_SAFE_SIZE"
            sizing_basis = (
                f"Sized to {quantity} whole shares from the synchronized account and positions.",
                f"Binding limit: {limiting_name}.",
                "Buying power and margin were ignored; sizing uses cash-only limits.",
            )
        as_of = artifact.observed_at
        mark = ObservedPositionMark(
            observation_id=uuid4(),
            source=SourceIdentity(name="DASHBOARD_PAPER_MARK"),
            instrument=artifact.instrument,
            price=latest.close,
            valued_at=latest.timestamp,
            observed_at=as_of,
        )
        account_id = "LOCAL_PAPER_ACCOUNT"
        account = ObservedPaperAccountState(
            observation_id=uuid4(),
            account_id=account_id,
            source=SourceIdentity(name="USER_ENTERED_PAPER_ACCOUNT"),
            currency="USD",
            effective_at=as_of,
            observed_at=as_of,
            cash_balance=cash_balance,
            positions=(),
            pending_actions=(),
        )
        context = DeterministicPortfolioContextEngine().evaluate(
            PortfolioContextRequest(account=account, as_of=as_of, evaluation_at=as_of)
        )
        signed_quantity = quantity if request.side == "BUY" else -quantity
        proposal = PaperRiskProposal(
            proposal_id=uuid4(),
            account_id=account_id,
            instrument=artifact.instrument,
            quantity_change=signed_quantity,
            effective_at=as_of,
            observed_at=as_of,
            reference_mark=mark,
        )
        policy = PaperRiskPolicy(
            policy_id="DASHBOARD_PAPER_RISK",
            policy_version="1",
            account_id=account_id,
            currency="USD",
            max_abs_quantity_change=quantity,
            max_proposal_notional=request.max_trade_notional,
            max_gross_exposure=request.max_gross_exposure,
            max_instrument_exposure=request.max_instrument_exposure,
            max_gross_exposure_share=request.max_concentration_share,
            allow_short_positions=False,
            min_equity_value=Decimal("0.01"),
            min_cash_balance=request.min_cash_balance,
            max_account_age_us=0,
            max_mark_age_us=40_000_000_000_000,
            max_proposal_age_us=0,
        )
        risk = DeterministicPaperRiskEngine().evaluate(
            PaperRiskRequest(
                context=context, proposal=proposal, policy=policy, as_of=as_of, evaluation_at=as_of
            )
        )
        setup_key = (
            SetupKey.UPSIDE_BREAKOUT_ABOVE_SMA
            if request.side == "BUY"
            else SetupKey.DOWNSIDE_BREAKDOWN_BELOW_SMA
        )
        desks = DeterministicAdvisoryDeskEngine()
        decision = DeterministicHeadOfDeskEngine().compose(
            HeadOfDeskRequest(
                proposal=proposal,
                risk=risk,
                setup=desks.setup(SetupDeskRequest(payload=research, evaluation_at=as_of)),
                trend=desks.trend(TrendDeskRequest(payload=trend, evaluation_at=as_of)),
                catalyst=None,
                insider=None,
                policy=HeadOfDeskPolicy(
                    policy_id="DASHBOARD_DESK",
                    policy_version="1",
                    account_id=account_id,
                    expected_risk_policy_id=policy.policy_id,
                    expected_risk_policy_version=policy.policy_version,
                    setup_key=setup_key,
                    enable_watchlist=True,
                    enable_alert=True,
                    max_bar_age_us=40_000_000_000_000,
                ),
                as_of=as_of,
                evaluation_at=as_of,
            )
        )
        estimated_loss = quantity * abs(latest.close - request.stop_price)
        stop_direction_invalid = (request.side == "BUY" and request.stop_price >= latest.close) or (
            request.side == "SELL" and request.stop_price <= latest.close
        )
        loss_veto = estimated_loss > request.max_loss_budget or stop_direction_invalid
        eligible = (
            risk.status.value == "PASS_CHECKS"
            and decision.disposition.value == "ALERT"
            and not loss_veto
        )
        status: Literal["ELIGIBLE_FOR_PAPER_REVIEW", "VETOED", "NOT_ACTIONABLE"] = (
            "ELIGIBLE_FOR_PAPER_REVIEW"
            if eligible
            else "VETOED"
            if loss_veto or risk.status.value == "VETO"
            else "NOT_ACTIONABLE"
        )
        projection = risk.projection
        risk_per_share = abs(latest.close - request.stop_price)
        target_price = (
            latest.close + risk_per_share * Decimal("2")
            if request.side == "BUY"
            else latest.close - risk_per_share * Decimal("2")
        )
        reasons = tuple(reason.value for reason in risk.reasons) + (
            ("STOP_DIRECTION_INVALID",)
            if stop_direction_invalid
            else ("LOSS_BUDGET_EXCEEDED",)
            if loss_veto
            else ()
        )
        explanation = (
            "All selected setup, trend, frozen risk, and loss-budget checks passed. "
            "Review this hypothetical plan in Alpaca Paper Trading; RevMind did not submit it."
            if eligible
            else "The plan is blocked by one or more deterministic risk checks. Do not place it."
            if status == "VETOED"
            else "Risk checks passed, but the selected setup and trend do not currently "
            "support this direction. Wait and re-analyze later."
        )
        approval_id = None
        if eligible:
            approval_id = str(uuid4())
            self._approved_plans[approval_id] = PaperOrderRequest(
                client_order_id=f"revmind-{uuid4()}",
                symbol=artifact.instrument.symbol,
                side="buy" if request.side == "BUY" else "sell",
                quantity=quantity,
                entry_limit=latest.close,
                stop_price=request.stop_price,
                target_price=target_price,
            )
            while len(self._approved_plans) > 50:
                self._approved_plans.pop(next(iter(self._approved_plans)))
        return PaperPlanResult(
            status=status,
            symbol=artifact.instrument.symbol,
            side=request.side,
            quantity=str(quantity),
            reference_price=str(latest.close),
            stop_price=str(request.stop_price),
            target_price=str(target_price),
            estimated_loss_at_stop=str(estimated_loss),
            risk_status=risk.status.value,
            risk_reasons=reasons,
            desk_disposition=decision.disposition.value,
            desk_reasons=tuple(reason.value for reason in decision.reasons),
            projected_cash=str(projection.projected_cash) if projection else None,
            projected_gross_exposure=str(projection.gross_exposure) if projection else None,
            approval_id=approval_id,
            explanation=explanation,
            quantity_source=quantity_source,
            sizing_basis=sizing_basis,
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
