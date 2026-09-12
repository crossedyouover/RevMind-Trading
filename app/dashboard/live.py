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
from app.catalysts.models import ObservedCatalystFact
from app.catalysts.provider import CatalystProvider, CatalystProviderError
from app.core.schemas import CanonicalModel, Instrument, MarketSnapshot, Timeframe, UtcDatetime
from app.dashboard.public_news import PublicNewsError, PublicRssNewsProvider
from app.dashboard.settings import AlpacaFeed, DataMode, SessionRule, SettingsStore, ValidationDepth
from app.data.ingestion import Clock, MarketDataIngestionCoordinator, SystemUtcClock
from app.data.market import (
    BarRequest,
    InstrumentNotFoundError,
    InvalidMarketDataRequestError,
    MarketDataUnavailableError,
    ProviderAuthenticationError,
    ProviderEntitlementError,
    ProviderRateLimitError,
)
from app.data.observation_store import ObservationStoreError, SQLiteObservationStore
from app.data.observations import ObservedMarketData, SourceIdentity
from app.data.providers.alpaca import (
    AlpacaInstrumentBinding,
    AlpacaMarketDataProvider,
    AlpacaNewsProvider,
)
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


class ResearchHeadline(CanonicalModel):
    headline: str
    source: str
    published_at: UtcDatetime
    url: str


class NewsDeskRow(CanonicalModel):
    symbol: str
    recent_news: tuple[ResearchHeadline, ...]


class NewsDeskReport(CanonicalModel):
    schema_version: Literal[1] = 1
    observed_at: UtcDatetime
    rows: tuple[NewsDeskRow, ...]


class DecisionCheck(CanonicalModel):
    label: str
    status: Literal["PASS", "CAUTION", "BLOCK", "INFO"]
    detail: str


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
    suggested_stop: str | None
    stop_comment: str
    next_step: str
    chart: tuple[ResearchPoint, ...]
    backtest: BacktestSummary
    evidence_grade: Literal["INSUFFICIENT", "WEAK", "PROMISING", "NOT_APPLICABLE"]
    evidence_score: str | None
    evidence_comment: str
    market_alignment: Literal["SUPPORTS", "CONTRADICTS", "NEUTRAL", "UNAVAILABLE"]
    market_comment: str
    relative_strength_percent: str | None
    relative_alignment: Literal["SUPPORTS", "CONTRADICTS", "NEUTRAL", "UNAVAILABLE"]
    relative_strength_comment: str
    news_status: Literal["AVAILABLE", "NONE", "UNAVAILABLE"] = "UNAVAILABLE"
    catalyst_comment: str = "Recent catalyst context has not been loaded."
    recent_news: tuple[ResearchHeadline, ...] = ()
    readiness: Literal["READY_FOR_RISK_CHECK", "CAUTION", "WAIT"] = "WAIT"
    readiness_comment: str = "Trade readiness has not been composed yet."
    decision_checks: tuple[DecisionCheck, ...] = ()
    opportunity_rank: int | None = None


class ScanOutcome(CanonicalModel):
    symbol: str
    timeframe: Timeframe
    session_rule: SessionRule
    assessed_at: UtcDatetime
    readiness: Literal["READY_FOR_RISK_CHECK", "CAUTION", "WAIT"]
    direction: Literal["LONG", "SHORT", "NONE"]
    reference_price: str
    measured_at: UtcDatetime
    measured_price: str
    market_return_percent: str
    direction_result: Literal["FAVORABLE", "ADVERSE", "FLAT", "NOT_APPLICABLE"]


class ScanCalibration(CanonicalModel):
    readiness: Literal["READY_FOR_RISK_CHECK", "CAUTION"]
    measured: int
    favorable: int
    adverse: int
    flat: int
    favorable_rate_percent: str | None
    evidence_status: Literal["INSUFFICIENT", "OBSERVED"]
    explanation: str


class DeskSummary(CanonicalModel):
    stance: Literal["REVIEW_READY", "CAUTION_ONLY", "WAIT"]
    headline: str
    instruction: str
    lead_symbol: str | None
    ready_count: int
    caution_count: int
    wait_count: int


class MarketResearchReport(CanonicalModel):
    schema_version: Literal[1] = 1
    status: Literal["COMPLETE_READ_ONLY"]
    source: str
    feed: AlpacaFeed
    requested_start: UtcDatetime
    requested_end: UtcDatetime
    completed_at: UtcDatetime
    validation_depth: ValidationDepth
    session_rule: SessionRule
    rows: tuple[MarketResearchRow, ...]
    recent_outcomes: tuple[ScanOutcome, ...] = ()
    calibration: tuple[ScanCalibration, ...] = ()
    desk_summary: DeskSummary


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
NewsProviderFactory = Callable[[SecretStr, SecretStr], CatalystProvider]


class DashboardLiveData:
    """Use frozen provider and ingestion boundaries for one explicit snapshot batch."""

    def __init__(
        self,
        settings: SettingsStore,
        *,
        clock: Clock | None = None,
        provider_factory: ProviderFactory = AlpacaMarketDataProvider,
        paper_broker_factory: PaperBrokerFactory = AlpacaPaperBroker,
        news_provider_factory: NewsProviderFactory | None = None,
    ) -> None:
        self._settings = settings
        self._clock = clock or SystemUtcClock()
        self._provider_factory = provider_factory
        self._paper_broker_factory = paper_broker_factory
        self._news_provider_factory = (
            AlpacaNewsProvider
            if news_provider_factory is None and provider_factory is AlpacaMarketDataProvider
            else news_provider_factory
        )
        self._status_path = settings.directory / "alpaca-status.json"
        self._observations_path = settings.directory / "market-observations.db"
        self._paper_orders_path = settings.directory / "paper-orders.db"
        self._scan_history_path = settings.directory / "scan-history.db"
        self._research_artifacts: dict[str, _ResearchArtifact] = {}
        self._approved_plans: dict[str, PaperOrderRequest] = {}
        self._paper_account: PaperAccount | None = None

    async def news(self) -> NewsDeskReport:
        """Fetch bounded factual news independently of historical price availability."""
        selected = self._settings.load()
        if selected.data_mode is not DataMode.ALPACA:
            raise LiveProbeError("Select Alpaca and save settings first.")
        if self._news_provider_factory is None:
            raise LiveProbeError("No news provider is configured.")
        observed_at = self._receipt_time()
        instruments = tuple(item.to_instrument() for item in selected.watchlist)
        provider: CatalystProvider | None = None
        try:
            key, secret = self._settings.alpaca_credentials()
            provider = self._news_provider_factory(key, secret)
            facts = await provider.get_news(
                instruments,
                published_start=observed_at - timedelta(days=7),
                published_end=observed_at,
                observed_at=observed_at,
            )
        except (CatalystProviderError, ValidationError, ValueError, TypeError, OSError):
            public_provider = PublicRssNewsProvider()
            try:
                public_headlines = await public_provider.get_news(observed_at)
                return NewsDeskReport(
                    observed_at=observed_at,
                    rows=(
                        NewsDeskRow(
                            symbol="MACRO",
                            recent_news=tuple(
                                ResearchHeadline(
                                    headline=item.headline,
                                    source=item.source,
                                    published_at=item.published_at,
                                    url=item.url,
                                )
                                for item in public_headlines
                            ),
                        ),
                    ),
                )
            except PublicNewsError as public_exc:
                raise LiveProbeError(
                    "Alpaca news and official public RSS feeds were unavailable."
                ) from public_exc
            finally:
                await public_provider.aclose()
        finally:
            if provider is not None:
                await provider.aclose()
        rows = []
        for instrument in instruments:
            matching = sorted(
                (
                    fact
                    for fact in facts
                    if any(item.symbol == instrument.symbol for item in fact.instruments)
                ),
                key=lambda fact: (
                    fact.published_at or fact.observed_at,
                    fact.source_record_id or "",
                ),
                reverse=True,
            )
            rows.append(
                NewsDeskRow(
                    symbol=instrument.symbol,
                    recent_news=tuple(
                        ResearchHeadline(
                            headline=fact.headline,
                            source=fact.source.name,
                            published_at=fact.published_at or fact.observed_at,
                            url=fact.url or "",
                        )
                        for fact in matching[:10]
                    ),
                )
            )
        return NewsDeskReport(observed_at=observed_at, rows=tuple(rows))

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
                    (
                        order.client_order_id,
                        order.symbol,
                        order.side,
                        str(order.quantity),
                        encoded,
                        status,
                        None,
                        None,
                        None,
                        self._receipt_time().isoformat(),
                    ),
                )
            else:
                db.execute(
                    "UPDATE paper_orders SET status=?,provider_order_id=?,submitted_at=?,receipt=? "
                    "WHERE client_order_id=?",
                    (
                        status,
                        receipt.provider_order_id if receipt else None,
                        receipt.submitted_at.isoformat() if receipt else None,
                        receipt.model_dump_json() if receipt else None,
                        order.client_order_id,
                    ),
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
        except ProviderAuthenticationError as exc:
            self._record_failure("CREDENTIALS_REJECTED", selected.alpaca_feed)
            raise LiveProbeError(
                "Alpaca rejected the stored API key or secret. Revoke the exposed key, generate "
                "a new Paper Trading key pair, and save both new values in Connections."
            ) from exc
        except ProviderEntitlementError as exc:
            self._record_failure("FEED_NOT_ENTITLED", selected.alpaca_feed)
            raise LiveProbeError(
                "The Alpaca credentials are valid but cannot access the selected feed. "
                "Select IEX unless this account has SIP entitlement."
            ) from exc
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
                        self._session_observations(
                            acquired.observations, selected.timeframe, selected.session_rule
                        ),
                    )
                    if row.assessment_id is not None:
                        self._research_artifacts[row.assessment_id] = artifact
                    rows.append(row)
            benchmark = next((item for item in rows if item.symbol == "SPY"), None)
            rows = [self._with_market_alignment(item, benchmark) for item in rows]
            rows = [self._with_relative_strength(item, benchmark) for item in rows]
            rows = await self._with_recent_news(
                rows, tuple(item.instrument for item in bindings), end
            )
            rows = [self._with_trade_readiness(item) for item in rows]
            grade_order = {"PROMISING": 0, "WEAK": 1, "INSUFFICIENT": 2, "NOT_APPLICABLE": 3}
            alignment_order = {"SUPPORTS": 0, "NEUTRAL": 1, "UNAVAILABLE": 2, "CONTRADICTS": 3}
            rows.sort(
                key=lambda item: (
                    item.action != "REVIEW",
                    alignment_order[item.market_alignment],
                    alignment_order[item.relative_alignment],
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
            report = MarketResearchReport(
                status="COMPLETE_READ_ONLY",
                source=source.name,
                feed=selected.alpaca_feed,
                requested_start=start,
                requested_end=end,
                completed_at=self._receipt_time(),
                validation_depth=selected.validation_depth,
                session_rule=selected.session_rule,
                rows=tuple(ranked),
                desk_summary=self._desk_summary(tuple(ranked)),
            )
            outcomes = self._update_scan_history(
                report.rows, report.session_rule, report.completed_at
            )
            return report.model_copy(
                update={
                    "recent_outcomes": outcomes,
                    "calibration": self._scan_calibration(
                        report.rows[0].timeframe, report.session_rule
                    ),
                }
            )
        except ProviderAuthenticationError as exc:
            raise LiveProbeError(
                "Alpaca rejected the stored API key or secret. Replace both credentials in "
                "Connections, then run Test read-only connection."
            ) from exc
        except ProviderEntitlementError as exc:
            raise LiveProbeError(
                "Alpaca denied historical access for the selected feed. Use IEX unless this "
                "account has SIP entitlement."
            ) from exc
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

    async def _with_recent_news(
        self,
        rows: list[MarketResearchRow],
        instruments: tuple[Instrument, ...],
        market_end: datetime,
    ) -> list[MarketResearchRow]:
        """Attach bounded factual headlines without changing signal or risk decisions."""
        if self._news_provider_factory is None:
            return rows
        provider: CatalystProvider | None = None
        try:
            key, secret = self._settings.alpaca_credentials()
            provider = self._news_provider_factory(key, secret)
            published_end = market_end + timedelta(microseconds=1)
            facts = await provider.get_news(
                instruments,
                published_start=published_end - timedelta(days=7),
                published_end=published_end,
                observed_at=self._receipt_time(),
            )
        except (CatalystProviderError, ValidationError, ValueError, TypeError, OSError):
            return [
                row.model_copy(
                    update={
                        "news_status": "UNAVAILABLE",
                        "catalyst_comment": (
                            "Recent headline context is unavailable; no catalyst claim is made."
                        ),
                    }
                )
                for row in rows
            ]
        finally:
            if provider is not None:
                await provider.aclose()
        return [self._attach_news(row, facts) for row in rows]

    @staticmethod
    def _attach_news(
        row: MarketResearchRow, facts: tuple[ObservedCatalystFact, ...]
    ) -> MarketResearchRow:
        matching = tuple(
            sorted(
                (
                    fact
                    for fact in facts
                    if any(item.symbol == row.symbol for item in fact.instruments)
                ),
                key=lambda fact: (
                    fact.published_at or fact.observed_at,
                    fact.source_record_id or "",
                ),
                reverse=True,
            )
        )
        if not matching:
            return row.model_copy(
                update={
                    "news_status": "NONE",
                    "catalyst_comment": (
                        "No timestamped Alpaca headlines were found in the last 7 days."
                    ),
                    "recent_news": (),
                }
            )
        headlines = tuple(
            ResearchHeadline(
                headline=fact.headline,
                source=fact.source.name,
                published_at=fact.published_at or fact.observed_at,
                url=fact.url or "",
            )
            for fact in matching[:3]
        )
        return row.model_copy(
            update={
                "news_status": "AVAILABLE",
                "catalyst_comment": (
                    f"{len(matching)} timestamped headline(s) found in the last 7 days. "
                    "They provide context only and are not classified as bullish or bearish."
                ),
                "recent_news": headlines,
            }
        )

    @staticmethod
    def _with_trade_readiness(row: MarketResearchRow) -> MarketResearchRow:
        """Explain whether an opportunity merits a separate risk check."""
        has_setup = bool(row.active_setups)
        market_block = row.market_alignment == "CONTRADICTS"
        relative_block = row.relative_alignment == "CONTRADICTS"
        evidence_pass = row.evidence_grade == "PROMISING"
        checks = (
            DecisionCheck(
                label="Active setup",
                status="PASS" if has_setup else "BLOCK",
                detail=(
                    row.active_setups[0].replace("_", " ")
                    if has_setup
                    else "No frozen entry setup is active."
                ),
            ),
            DecisionCheck(
                label="Broad market",
                status="BLOCK"
                if market_block
                else "PASS"
                if row.market_alignment == "SUPPORTS"
                else "CAUTION",
                detail=row.market_comment,
            ),
            DecisionCheck(
                label="Relative strength",
                status="BLOCK"
                if relative_block
                else "PASS"
                if row.relative_alignment == "SUPPORTS"
                else "CAUTION",
                detail=row.relative_strength_comment,
            ),
            DecisionCheck(
                label="Held-out evidence",
                status="PASS" if evidence_pass else "CAUTION" if has_setup else "INFO",
                detail=row.evidence_comment,
            ),
            DecisionCheck(
                label="Recent catalysts",
                status="INFO",
                detail=row.catalyst_comment,
            ),
        )
        if not has_setup:
            readiness: Literal["READY_FOR_RISK_CHECK", "CAUTION", "WAIT"] = "WAIT"
            comment = "WAIT: no entry setup exists. Check again after another completed bar."
        elif market_block or relative_block:
            readiness = "CAUTION"
            blockers = []
            if market_block:
                blockers.append("the SPY trend contradicts the setup")
            if relative_block:
                blockers.append("relative performance contradicts the setup")
            comment = "CAUTION: " + " and ".join(blockers) + ". Do not treat this as trade-ready."
        elif not evidence_pass:
            readiness = "CAUTION"
            comment = (
                "CAUTION: a setup exists, but held-out history is not yet PROMISING. "
                "You may inspect a paper plan, but the evidence does not justify confidence."
            )
        else:
            readiness = "READY_FOR_RISK_CHECK"
            comment = (
                "READY FOR RISK CHECK: price evidence is aligned and held-out validation is "
                "PROMISING. Risk can still veto the paper plan."
            )
        return row.model_copy(
            update={
                "readiness": readiness,
                "readiness_comment": comment,
                "decision_checks": checks,
                "next_step": (
                    "RevMind will automatically build the risk-bounded paper plan; review it "
                    "before any explicit paper-order approval."
                    if readiness == "READY_FOR_RISK_CHECK"
                    else "Wait. RevMind will not create an approvable plan until the setup, "
                    "market context, and held-out evidence pass the readiness gate."
                ),
            }
        )

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

    @classmethod
    def _session_observations(
        cls,
        observations: tuple[ObservedMarketData, ...],
        timeframe: Timeframe,
        rule: SessionRule,
    ) -> tuple[ObservedMarketData, ...]:
        """Apply the explicit US-equity session preference before research."""
        if rule is SessionRule.EXTENDED or timeframe is Timeframe.ONE_DAY:
            return observations
        return tuple(
            item for item in observations if cls._is_regular_us_equity_time(item.payload.timestamp)
        )

    @staticmethod
    def _is_regular_us_equity_time(event_at: datetime) -> bool:
        """Return whether UTC event time is in 09:30–16:00 US Eastern, weekdays only."""
        utc = event_at.astimezone(UTC)
        year = utc.year

        def sunday(year_value: int, month: int, occurrence: int) -> int:
            first_weekday = datetime(year_value, month, 1, tzinfo=UTC).weekday()
            return 1 + (6 - first_weekday) % 7 + 7 * (occurrence - 1)

        daylight_start = datetime(year, 3, sunday(year, 3, 2), 7, tzinfo=UTC)
        daylight_end = datetime(year, 11, sunday(year, 11, 1), 6, tzinfo=UTC)
        offset = -4 if daylight_start <= utc < daylight_end else -5
        eastern = utc + timedelta(hours=offset)
        minutes = eastern.hour * 60 + eastern.minute
        return eastern.weekday() < 5 and 9 * 60 + 30 <= minutes < 16 * 60

    def _update_scan_history(
        self,
        rows: tuple[MarketResearchRow, ...],
        session_rule: SessionRule,
        recorded_at: datetime,
    ) -> tuple[ScanOutcome, ...]:
        """Persist assessments and measure older ones at the next later completed price."""
        if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
            raise ValueError("scan-history receipt time must include timezone information")
        if any(row.latest_bar_at is not None and row.latest_bar_at > recorded_at for row in rows):
            raise ValueError("scan history cannot record a future completed bar")
        self._settings.directory.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._scan_history_path) as db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute(
                """CREATE TABLE IF NOT EXISTS scan_assessments (
                assessment_id TEXT PRIMARY KEY, symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL, session_rule TEXT NOT NULL,
                assessed_at TEXT NOT NULL, readiness TEXT NOT NULL,
                direction TEXT NOT NULL, reference_price TEXT NOT NULL,
                measured_at TEXT, measured_price TEXT, market_return_percent TEXT,
                direction_result TEXT)"""
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(scan_assessments)")}
            if "timeframe" not in columns:
                db.execute(
                    "ALTER TABLE scan_assessments ADD COLUMN timeframe TEXT NOT NULL "
                    "DEFAULT 'UNKNOWN'"
                )
            if "session_rule" not in columns:
                db.execute(
                    "ALTER TABLE scan_assessments ADD COLUMN session_rule TEXT NOT NULL "
                    "DEFAULT 'UNKNOWN'"
                )
            db.execute("DROP INDEX IF EXISTS one_equivalent_scan")
            db.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS one_equivalent_scan
                ON scan_assessments(
                symbol,timeframe,session_rule,assessed_at,readiness,direction,reference_price)"""
            )
            for row in rows:
                if row.latest_close is None or row.latest_bar_at is None:
                    continue
                prior = db.execute(
                    """SELECT assessment_id,assessed_at,direction,reference_price
                    FROM scan_assessments WHERE symbol=? AND timeframe=? AND session_rule=?
                    AND measured_at IS NULL
                    AND assessed_at<? ORDER BY assessed_at,assessment_id""",
                    (
                        row.symbol,
                        row.timeframe.value,
                        session_rule.value,
                        row.latest_bar_at.isoformat(),
                    ),
                ).fetchall()
                current = Decimal(row.latest_close)
                for assessment_id, assessed_at, direction, reference_text in prior:
                    reference = Decimal(reference_text)
                    if reference <= 0:
                        continue
                    change = ((current - reference) / reference * Decimal("100")).quantize(
                        Decimal("0.0001")
                    )
                    result = "NOT_APPLICABLE"
                    if direction == "LONG":
                        result = "FAVORABLE" if change > 0 else "ADVERSE" if change < 0 else "FLAT"
                    elif direction == "SHORT":
                        result = "FAVORABLE" if change < 0 else "ADVERSE" if change > 0 else "FLAT"
                    db.execute(
                        """UPDATE scan_assessments SET measured_at=?,measured_price=?,
                        market_return_percent=?,direction_result=? WHERE assessment_id=?""",
                        (
                            row.latest_bar_at.isoformat(),
                            str(current),
                            str(change),
                            result,
                            assessment_id,
                        ),
                    )
                direction = (
                    "LONG"
                    if row.active_setups
                    and row.active_setups[0] == SetupKey.UPSIDE_BREAKOUT_ABOVE_SMA.value
                    else "SHORT"
                    if row.active_setups
                    else "NONE"
                )
                if row.assessment_id is not None:
                    db.execute(
                        """INSERT OR IGNORE INTO scan_assessments (
                        assessment_id,symbol,timeframe,session_rule,assessed_at,readiness,
                        direction,reference_price,measured_at,measured_price,
                        market_return_percent,direction_result) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            row.assessment_id,
                            row.symbol,
                            row.timeframe.value,
                            session_rule.value,
                            row.latest_bar_at.isoformat(),
                            row.readiness,
                            direction,
                            row.latest_close,
                            None,
                            None,
                            None,
                            None,
                        ),
                    )
            selected = db.execute(
                """SELECT symbol,timeframe,session_rule,assessed_at,readiness,direction,
                reference_price,
                measured_at,measured_price,market_return_percent,direction_result
                FROM scan_assessments WHERE timeframe=? AND session_rule=?
                AND measured_at IS NOT NULL
                ORDER BY measured_at DESC,assessment_id DESC LIMIT 20""",
                (rows[0].timeframe.value, session_rule.value),
            ).fetchall()
        return tuple(
            ScanOutcome(
                symbol=row[0],
                timeframe=row[1],
                session_rule=row[2],
                assessed_at=datetime.fromisoformat(row[3]),
                readiness=row[4],
                direction=row[5],
                reference_price=row[6],
                measured_at=datetime.fromisoformat(row[7]),
                measured_price=row[8],
                market_return_percent=row[9],
                direction_result=row[10],
            )
            for row in selected
        )

    def _scan_calibration(
        self, timeframe: Timeframe, session_rule: SessionRule
    ) -> tuple[ScanCalibration, ...]:
        """Summarize measured directions without overstating small samples."""
        with sqlite3.connect(self._scan_history_path) as db:
            rows = db.execute(
                """SELECT readiness,direction_result,COUNT(*) FROM scan_assessments
                WHERE readiness IN ('READY_FOR_RISK_CHECK','CAUTION')
                AND direction_result IN ('FAVORABLE','ADVERSE','FLAT')
                AND timeframe=? AND session_rule=?
                GROUP BY readiness,direction_result""",
                (timeframe.value, session_rule.value),
            ).fetchall()
        readiness_values: tuple[Literal["READY_FOR_RISK_CHECK", "CAUTION"], ...] = (
            "READY_FOR_RISK_CHECK",
            "CAUTION",
        )
        counts = {
            readiness: {"FAVORABLE": 0, "ADVERSE": 0, "FLAT": 0} for readiness in readiness_values
        }
        for readiness, result, count in rows:
            counts[readiness][result] = count
        output = []
        for readiness in readiness_values:
            values = counts[readiness]
            measured = sum(values.values())
            rate = (
                (Decimal(values["FAVORABLE"]) / Decimal(measured) * Decimal("100")).quantize(
                    Decimal("0.1")
                )
                if measured
                else None
            )
            enough = measured >= 20
            output.append(
                ScanCalibration(
                    readiness=readiness,
                    measured=measured,
                    favorable=values["FAVORABLE"],
                    adverse=values["ADVERSE"],
                    flat=values["FLAT"],
                    favorable_rate_percent=str(rate) if rate is not None else None,
                    evidence_status="OBSERVED" if enough else "INSUFFICIENT",
                    explanation=(
                        "At least 20 live forward observations exist. This is observed direction "
                        "frequency, not a guarantee or realized P/L."
                        if enough
                        else f"Only {measured} of 20 required live forward observations exist; "
                        "do not infer reliability yet."
                    ),
                )
            )
        return tuple(output)

    @staticmethod
    def _desk_summary(rows: tuple[MarketResearchRow, ...]) -> DeskSummary:
        """Produce one conservative instruction from the fully composed rows."""
        ready = tuple(row for row in rows if row.readiness == "READY_FOR_RISK_CHECK")
        cautious = tuple(row for row in rows if row.readiness == "CAUTION")
        waiting = tuple(row for row in rows if row.readiness == "WAIT")
        if ready:
            lead = ready[0]
            return DeskSummary(
                stance="REVIEW_READY",
                headline=f"{lead.symbol} leads {len(ready)} setup(s) ready for a risk check.",
                instruction=(
                    "Inspect the lead card, then build a paper plan. Do not submit anything "
                    "unless the separate risk gate passes and you explicitly approve it."
                ),
                lead_symbol=lead.symbol,
                ready_count=len(ready),
                caution_count=len(cautious),
                wait_count=len(waiting),
            )
        if cautious:
            return DeskSummary(
                stance="CAUTION_ONLY",
                headline="No setup is trade-ready; only cautious candidates exist.",
                instruction=(
                    "Review the blockers for learning, but wait for stronger aligned and "
                    "validated evidence before treating a candidate as ready."
                ),
                lead_symbol=None,
                ready_count=0,
                caution_count=len(cautious),
                wait_count=len(waiting),
            )
        return DeskSummary(
            stance="WAIT",
            headline="No valid entry is present on the latest completed bars.",
            instruction="Do nothing. Scan again after the next completed bar.",
            lead_symbol=None,
            ready_count=0,
            caution_count=0,
            wait_count=len(waiting),
        )

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
        values = (
            {
                item.key: item.value
                for item in latest_technical.features
                if item.value is not None and item.period == 20
            }
            if latest_technical is not None
            else {}
        )
        sma = values.get(TechnicalFeatureKey.SMA_CLOSE)
        prior_high = values.get(TechnicalFeatureKey.ROLLING_HIGHEST_HIGH)
        prior_low = values.get(TechnicalFeatureKey.ROLLING_LOWEST_LOW)
        prior_structure = history.bars[-21:-1] if len(history.bars) >= 21 else ()
        structural_high = max((item.bar.high for item in prior_structure), default=None)
        structural_low = min((item.bar.low for item in prior_structure), default=None)
        trend_name = (
            latest_trend.regime.value.lower() if latest_trend and latest_trend.regime else None
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
        suggested_stop: Decimal | None = None
        stop_comment = "No active setup exists, so RevMind is not suggesting a stop."
        if latest_bar is not None and SetupKey.UPSIDE_BREAKOUT_ABOVE_SMA.value in active:
            if structural_high is not None and structural_high < latest_bar.close:
                suggested_stop = structural_high
                stop_comment = (
                    "Suggested invalidation reference: the prior 20-bar high that price broke "
                    "above. You must review and may change it before running risk checks."
                )
        elif latest_bar is not None and SetupKey.DOWNSIDE_BREAKDOWN_BELOW_SMA.value in active:
            if structural_low is not None and structural_low > latest_bar.close:
                suggested_stop = structural_low
                stop_comment = (
                    "Suggested invalidation reference: the prior 20-bar low that price broke "
                    "below. You must review and may change it before running risk checks."
                )
        if active and suggested_stop is None:
            stop_comment = (
                "The setup has no valid structural stop reference. Enter and review a stop "
                "manually before running risk checks."
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
            suggested_stop=str(suggested_stop) if suggested_stop is not None else None,
            stop_comment=stop_comment,
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
            relative_strength_percent=None,
            relative_alignment="UNAVAILABLE",
            relative_strength_comment="Relative-strength context has not been composed yet.",
        )
        return row, _ResearchArtifact(instrument, observed_at, research, trend)

    @staticmethod
    def _with_market_alignment(
        row: MarketResearchRow, benchmark: MarketResearchRow | None
    ) -> MarketResearchRow:
        if not row.active_setups:
            alignment: Literal["SUPPORTS", "CONTRADICTS", "NEUTRAL", "UNAVAILABLE"] = "NEUTRAL"
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
                f"SPY trend is {benchmark.trend.lower()} and {relationship} this {direction} setup."
            )
        return row.model_copy(update={"market_alignment": alignment, "market_comment": comment})

    @staticmethod
    def _with_relative_strength(
        row: MarketResearchRow, benchmark: MarketResearchRow | None
    ) -> MarketResearchRow:
        unavailable = {
            "relative_strength_percent": None,
            "relative_alignment": "UNAVAILABLE",
            "relative_strength_comment": (
                "At least two timestamp-aligned symbol and SPY closes are required."
            ),
        }
        if benchmark is None:
            return row.model_copy(update=unavailable)
        row_by_time = {point.event_at: Decimal(point.close) for point in row.chart}
        benchmark_by_time = {point.event_at: Decimal(point.close) for point in benchmark.chart}
        common = tuple(sorted(set(row_by_time) & set(benchmark_by_time)))[-21:]
        if len(common) < 2:
            return row.model_copy(update=unavailable)
        first, last = common[0], common[-1]
        if row_by_time[first] <= 0 or benchmark_by_time[first] <= 0:
            return row.model_copy(update=unavailable)
        row_return = row_by_time[last] / row_by_time[first] - Decimal("1")
        benchmark_return = benchmark_by_time[last] / benchmark_by_time[first] - Decimal("1")
        excess = (row_return - benchmark_return) * Decimal("100")
        if not row.active_setups or excess == 0:
            alignment: Literal["SUPPORTS", "CONTRADICTS", "NEUTRAL", "UNAVAILABLE"] = "NEUTRAL"
        else:
            is_long = row.active_setups[0] == SetupKey.UPSIDE_BREAKOUT_ABOVE_SMA.value
            alignment = (
                "SUPPORTS"
                if (is_long and excess > 0) or (not is_long and excess < 0)
                else "CONTRADICTS"
            )
        relationship = {
            "SUPPORTS": "supports the active direction",
            "CONTRADICTS": "contradicts the active direction",
            "NEUTRAL": "is neutral for the current decision",
            "UNAVAILABLE": "is unavailable",
        }[alignment]
        return row.model_copy(
            update={
                "relative_strength_percent": str(excess),
                "relative_alignment": alignment,
                "relative_strength_comment": (
                    f"20-bar performance versus SPY is {excess:+.2f} percentage points; "
                    f"this {relationship}."
                ),
            }
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
