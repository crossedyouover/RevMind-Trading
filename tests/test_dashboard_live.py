"""On-demand dashboard market data stays read-only, bounded, and receipt-aware."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from app.broker.models import (
    PaperAccount,
    PaperOrderReceipt,
    PaperOrderRequest,
    PaperOrderStatus,
)
from app.catalysts.models import ObservedCatalystFact
from app.core.schemas import CatalystSourceType
from app.dashboard.live import (
    DashboardLiveData,
    LiveProbeError,
    PaperApprovalInput,
    PaperCancelInput,
    PaperPlanInput,
)
from app.dashboard.settings import DEFAULT_SETTINGS, DataMode, SettingsStore, ValidationDepth
from app.data.observations import SourceIdentity
from app.data.providers.alpaca import (
    AlpacaInstrumentBinding,
    AlpacaMarketDataProvider,
    AlpacaMarketDataSettings,
)


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 9, 7, 10, 0, tzinfo=UTC)


def configured_store(tmp_path: Path) -> SettingsStore:
    store = SettingsStore(tmp_path / ".revmind")
    store.save(
        DEFAULT_SETTINGS.model_copy(update={"data_mode": DataMode.ALPACA}),
        "distinct-key",
        "distinct-secret",
        False,
    )
    return store


def snapshot(symbol: str) -> dict[str, object]:
    return {
        "latestTrade": {
            "t": "2026-09-07T09:59:59.123456789Z",
            "p": 101.25 if symbol == "AAPL" else 202.50,
        }
    }


def test_extended_validation_window_is_explicit_and_bounded(tmp_path: Path) -> None:
    service = DashboardLiveData(configured_store(tmp_path), clock=FixedClock())
    standard_start, standard_end = service._research_window(
        DEFAULT_SETTINGS.timeframe, ValidationDepth.STANDARD
    )
    extended_start, extended_end = service._research_window(
        DEFAULT_SETTINGS.timeframe, ValidationDepth.EXTENDED
    )
    assert standard_end == extended_end
    assert standard_end - standard_start == timedelta(days=7) - timedelta(microseconds=1)
    assert extended_end - extended_start == timedelta(days=14) - timedelta(microseconds=1)


def test_regular_session_filter_handles_dst_boundaries_and_weekends() -> None:
    regular = DashboardLiveData._is_regular_us_equity_time
    assert regular(datetime(2026, 1, 5, 14, 30, tzinfo=UTC))
    assert not regular(datetime(2026, 1, 5, 14, 29, tzinfo=UTC))
    assert not regular(datetime(2026, 1, 5, 21, 0, tzinfo=UTC))
    assert regular(datetime(2026, 7, 6, 13, 30, tzinfo=UTC))
    assert not regular(datetime(2026, 7, 6, 20, 0, tzinfo=UTC))
    assert not regular(datetime(2026, 7, 5, 15, 0, tzinfo=UTC))


def test_regular_session_filter_uses_explicit_us_eastern_dst_boundaries() -> None:
    check = DashboardLiveData._is_regular_us_equity_time
    assert not check(datetime(2026, 9, 8, 13, 29, tzinfo=UTC))
    assert check(datetime(2026, 9, 8, 13, 30, tzinfo=UTC))
    assert check(datetime(2026, 9, 8, 19, 59, tzinfo=UTC))
    assert not check(datetime(2026, 9, 8, 20, 0, tzinfo=UTC))
    assert check(datetime(2026, 1, 6, 14, 30, tzinfo=UTC))
    assert not check(datetime(2026, 1, 6, 21, 0, tzinfo=UTC))
    assert not check(datetime(2026, 9, 6, 14, 0, tzinfo=UTC))


@pytest.mark.asyncio
async def test_probe_uses_frozen_adapter_and_persists_receipt_aware_snapshots(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    clients: list[httpx.AsyncClient] = []

    def factory(
        settings: AlpacaMarketDataSettings,
        bindings: tuple[AlpacaInstrumentBinding, ...],
    ) -> AlpacaMarketDataProvider:
        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            symbols = request.url.params["symbols"].split(",")
            return httpx.Response(200, json={symbol: snapshot(symbol) for symbol in symbols})

        client = httpx.AsyncClient(
            base_url="https://data.alpaca.markets",
            follow_redirects=False,
            transport=httpx.MockTransport(respond),
        )
        clients.append(client)
        return AlpacaMarketDataProvider(settings, bindings, client=client)

    store = configured_store(tmp_path)
    service = DashboardLiveData(store, clock=FixedClock(), provider_factory=factory)
    report = await service.probe()
    assert report.status == "CONNECTED_READ_ONLY"
    assert report.feed == "IEX"
    assert report.source == "ALPACA_IEX"
    assert report.observed_at == FixedClock().now()
    assert tuple(item.symbol for item in report.quotes) == ("AAPL", "MSFT", "SPY")
    assert service.state().observation_count == 3
    assert (store.directory / "market-observations.db").is_file()
    assert requests[0].url.host == "data.alpaca.markets"
    assert requests[0].url.path == "/v2/stocks/snapshots"
    assert requests[0].url.params["feed"] == "iex"
    assert requests[0].headers["APCA-API-KEY-ID"] == "distinct-key"
    public = report.model_dump_json() + service.state().model_dump_json()
    assert "distinct-key" not in public and "distinct-secret" not in public
    service.invalidate()
    assert service.state().status == "NOT_TESTED"
    for client in clients:
        await client.aclose()


@pytest.mark.asyncio
async def test_market_research_uses_historical_bars_and_frozen_engines(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    clients: list[httpx.AsyncClient] = []

    def factory(
        settings: AlpacaMarketDataSettings,
        bindings: tuple[AlpacaInstrumentBinding, ...],
    ) -> AlpacaMarketDataProvider:
        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            symbol = request.url.path.split("/")[-2]
            first = datetime(2026, 9, 4, 13, 30, tzinfo=UTC)
            bars = [
                {
                    "t": (first + timedelta(minutes=index)).isoformat().replace("+00:00", "Z"),
                    "o": 100 + index,
                    "h": 101 + index,
                    "l": 99 + index,
                    "c": 101 + index,
                    "v": 1_000 + index,
                }
                for index in range(25)
            ]
            return httpx.Response(
                200, json={"bars": bars, "symbol": symbol, "next_page_token": None}
            )

        client = httpx.AsyncClient(
            base_url="https://data.alpaca.markets",
            follow_redirects=False,
            transport=httpx.MockTransport(respond),
        )
        clients.append(client)
        return AlpacaMarketDataProvider(settings, bindings, client=client)

    store = configured_store(tmp_path)
    service = DashboardLiveData(store, clock=FixedClock(), provider_factory=factory)
    report = await service.research()
    assert report.status == "COMPLETE_READ_ONLY"
    assert report.session_rule == "REGULAR"
    assert report.recent_outcomes == ()
    assert all(item.measured == 0 for item in report.calibration)
    assert all(item.evidence_status == "INSUFFICIENT" for item in report.calibration)
    assert report.desk_summary.stance == "CAUTION_ONLY"
    assert report.desk_summary.ready_count == 0
    assert report.desk_summary.caution_count == 3
    assert tuple(row.symbol for row in report.rows) == ("AAPL", "MSFT", "SPY")
    assert all(row.bar_count == 25 for row in report.rows)
    assert all(row.latest_close == "125" for row in report.rows)
    assert all(row.trend == "UPWARD" for row in report.rows)
    assert all(row.action == "REVIEW" for row in report.rows)
    assert all(row.active_setups == ("UPSIDE_BREAKOUT_ABOVE_SMA",) for row in report.rows)
    assert all("trend model is upward" in row.trend_comment for row in report.rows)
    assert all(
        "broke above the prior 20-bar high" in row.opportunity_comment for row in report.rows
    )
    assert all("Latest close $125" in row.price_location for row in report.rows)
    assert {row.suggested_stop for row in report.rows} == {"124"}
    assert all("prior 20-bar high" in row.stop_comment for row in report.rows)
    assert all("Open the trade planner" in row.next_step for row in report.rows)
    assert all(row.backtest.bars == 25 for row in report.rows)
    assert all(row.backtest.stop_percent == Decimal("2") for row in report.rows)
    assert all(row.backtest.target_percent == Decimal("4") for row in report.rows)
    assert all(len(row.backtest.results) == 2 for row in report.rows)
    assert all(len(row.backtest.out_of_sample_results) == 2 for row in report.rows)
    assert all(row.backtest.evidence_grade == "INSUFFICIENT" for row in report.rows)
    assert all(row.backtest.ranking_score is None for row in report.rows)
    assert all(row.evidence_grade == "INSUFFICIENT" for row in report.rows)
    assert all(row.evidence_score is None for row in report.rows)
    assert all(row.opportunity_rank is None for row in report.rows)
    assert all("Fewer than 20" in row.evidence_comment for row in report.rows)
    assert all(row.market_alignment == "SUPPORTS" for row in report.rows)
    assert all("SPY trend is upward" in row.market_comment for row in report.rows)
    assert all(row.relative_alignment == "NEUTRAL" for row in report.rows)
    assert all(Decimal(row.relative_strength_percent or "1") == 0 for row in report.rows)
    assert all(row.readiness == "CAUTION" for row in report.rows)
    assert all(len(row.decision_checks) == 5 for row in report.rows)
    assert all("held-out history" in row.readiness_comment for row in report.rows)
    short_row = report.rows[0].model_copy(
        update={"active_setups": ("DOWNSIDE_BREAKDOWN_BELOW_SMA",)}
    )
    contradicted = service._with_market_alignment(short_row, report.rows[-1])
    assert contradicted.market_alignment == "CONTRADICTS"
    assert "contradicts this short setup" in contradicted.market_comment
    stronger_chart = (
        *report.rows[0].chart[:-1],
        report.rows[0].chart[-1].model_copy(update={"close": "150"}),
    )
    stronger = report.rows[0].model_copy(update={"chart": stronger_chart})
    relative = service._with_relative_strength(stronger, report.rows[-1])
    assert relative.relative_alignment == "SUPPORTS"
    assert Decimal(relative.relative_strength_percent or "0") > 0
    with_news = service._attach_news(
        report.rows[0],
        (
            ObservedCatalystFact(
                observation_id=uuid4(),
                headline="Timestamped company update",
                source=SourceIdentity(name="ALPACA_NEWS"),
                source_type=CatalystSourceType.SECONDARY,
                observed_at=FixedClock().now(),
                published_at=FixedClock().now() - timedelta(days=1),
                source_record_id="story-1",
                url="https://example.test/story-1",
                instruments=(
                    next(
                        item.to_instrument()
                        for item in DEFAULT_SETTINGS.watchlist
                        if item.symbol == report.rows[0].symbol
                    ),
                ),
            ),
        ),
    )
    assert with_news.news_status == "AVAILABLE"
    assert with_news.recent_news[0].headline == "Timestamped company update"
    assert "context only" in with_news.catalyst_comment
    news_readiness = service._with_trade_readiness(with_news)
    assert news_readiness.readiness == report.rows[0].readiness
    ready = service._with_trade_readiness(
        report.rows[0].model_copy(
            update={
                "evidence_grade": "PROMISING",
                "market_alignment": "SUPPORTS",
                "relative_alignment": "SUPPORTS",
            }
        )
    )
    assert ready.readiness == "READY_FOR_RISK_CHECK"
    assert "Risk can still veto" in ready.readiness_comment
    ready_summary = service._desk_summary((ready,))
    assert ready_summary.stance == "REVIEW_READY"
    assert ready_summary.lead_symbol == ready.symbol
    waiting = service._with_trade_readiness(
        report.rows[0].model_copy(update={"active_setups": (), "action": "WAIT"})
    )
    assert waiting.readiness == "WAIT"
    assert service._desk_summary((waiting,)).stance == "WAIT"
    later_rows = tuple(
        row.model_copy(
            update={
                "assessment_id": str(uuid4()),
                "latest_bar_at": row.latest_bar_at + timedelta(minutes=1)
                if row.latest_bar_at
                else None,
                "latest_close": "126",
            }
        )
        for row in report.rows
    )
    outcomes = service._update_scan_history(
        later_rows, DEFAULT_SETTINGS.session_rule, FixedClock().now()
    )
    assert len(outcomes) == 3
    assert all(item.market_return_percent == "0.8000" for item in outcomes)
    assert all(item.direction_result == "FAVORABLE" for item in outcomes)
    calibration = service._scan_calibration(
        DEFAULT_SETTINGS.timeframe, DEFAULT_SETTINGS.session_rule
    )
    cautious = next(item for item in calibration if item.readiness == "CAUTION")
    assert cautious.measured == 3
    assert cautious.favorable_rate_percent == "100.0"
    assert cautious.evidence_status == "INSUFFICIENT"
    assert "3 of 20" in cautious.explanation
    assert (
        service._update_scan_history(later_rows, DEFAULT_SETTINGS.session_rule, FixedClock().now())
        == outcomes
    )
    assert (store.directory / "scan-history.db").is_file()
    assert all(row.backtest.results[0].trades >= 1 for row in report.rows)
    assert all("not a prediction" in row.backtest.warning for row in report.rows)
    assert all(request.url.params["feed"] == "iex" for request in requests)
    assert all(request.url.params["adjustment"] == "raw" for request in requests)
    assert "distinct" not in report.model_dump_json()
    assert (store.directory / "market-observations.db").is_file()
    plan = service.paper_plan(
        PaperPlanInput.model_validate(
            {
                "assessment_id": report.rows[0].assessment_id,
                "side": "BUY",
                "quantity": "1",
                "cash_balance": "10000",
                "max_trade_notional": "1000",
                "max_gross_exposure": "10000",
                "max_instrument_exposure": "2500",
                "max_concentration_share": "1",
                "min_cash_balance": "0",
                "stop_price": "120",
                "max_loss_budget": "10",
            }
        )
    )
    assert plan.status == "ELIGIBLE_FOR_PAPER_REVIEW"
    assert plan.risk_status == "PASS_CHECKS"
    assert plan.desk_disposition == "ALERT"
    assert plan.projected_cash == "9875"
    assert plan.estimated_loss_at_stop == "5"
    assert plan.target_price == "135"
    assert plan.approval_id is not None
    veto = service.paper_plan(
        PaperPlanInput.model_validate(
            {
                "assessment_id": report.rows[0].assessment_id,
                "side": "BUY",
                "quantity": "1",
                "cash_balance": "10000",
                "max_trade_notional": "1000",
                "max_gross_exposure": "10000",
                "max_instrument_exposure": "2500",
                "max_concentration_share": "1",
                "min_cash_balance": "0",
                "stop_price": "100",
                "max_loss_budget": "10",
            }
        )
    )
    assert veto.status == "VETOED"
    assert "LOSS_BUDGET_EXCEEDED" in veto.risk_reasons
    service._paper_account = PaperAccount(  # type: ignore[attr-defined]
        account_id="paper-account",
        status="ACTIVE",
        currency="USD",
        cash="1000",
        equity="20000",
        buying_power="40000",
        trading_blocked=False,
        observed_at=FixedClock().now(),
        positions=(),
    )
    automatic = service.paper_plan(
        PaperPlanInput.model_validate(
            {
                "assessment_id": report.rows[0].assessment_id,
                "side": "BUY",
                "quantity": "9999",
                "cash_balance": "999999",
                "auto_size": True,
                "max_trade_notional": "1000",
                "max_gross_exposure": "10000",
                "max_instrument_exposure": "2500",
                "max_concentration_share": "1",
                "min_cash_balance": "0",
                "stop_price": "120",
                "max_loss_budget": "40",
            }
        )
    )
    assert automatic.quantity == "8"
    assert automatic.quantity_source == "AUTOMATIC_SAFE_SIZE"
    assert automatic.estimated_loss_at_stop == "40"
    assert automatic.projected_cash == "0"
    assert "Binding limit: loss budget." in automatic.sizing_basis
    for client in clients:
        await client.aclose()


@pytest.mark.asyncio
async def test_eligible_plan_requires_one_time_exact_paper_approval(tmp_path: Path) -> None:
    placed: list[PaperOrderRequest] = []
    cancelled: list[str] = []

    class FakeBroker:
        async def account(self) -> PaperAccount:
            raise AssertionError("not used")

        async def place_bracket_order(self, request: PaperOrderRequest) -> PaperOrderReceipt:
            placed.append(request)
            return PaperOrderReceipt(
                provider_order_id="paper-order",
                client_order_id=request.client_order_id,
                symbol=request.symbol,
                side=request.side,
                quantity=request.quantity,
                status="accepted",
                submitted_at=FixedClock().now(),
            )

        async def order_status(self, provider_order_id: str) -> PaperOrderStatus:
            assert provider_order_id == "paper-order"
            return PaperOrderStatus(
                provider_order_id=provider_order_id,
                client_order_id=placed[0].client_order_id,
                symbol="AAPL",
                side="buy",
                quantity="1",
                filled_quantity="1",
                status="filled",
                filled_average_price="100",
                submitted_at=FixedClock().now(),
                updated_at=FixedClock().now(),
            )

        async def cancel_order(self, provider_order_id: str) -> None:
            cancelled.append(provider_order_id)

        async def aclose(self) -> None:
            pass

    service = DashboardLiveData(
        configured_store(tmp_path),
        clock=FixedClock(),
        paper_broker_factory=lambda _key, _secret: FakeBroker(),
    )
    service._approved_plans["eligible"] = PaperOrderRequest(  # type: ignore[attr-defined]
        client_order_id="revmind-00000000-0000-4000-8000-000000000001",
        symbol="AAPL",
        side="buy",
        quantity="1",
        entry_limit="100",
        stop_price="98",
        target_price="104",
    )
    receipt = await service.place_paper_order(
        PaperApprovalInput(approval_id="eligible", confirmation="PLACE PAPER ORDER")
    )
    assert receipt.provider_order_id == "paper-order"
    assert len(placed) == 1
    recorded = service.paper_order_history()[0]
    assert recorded["status"] == "ACCEPTED"
    assert recorded["entry_limit"] == "100"
    assert recorded["stop_price"] == "98"
    assert recorded["target_price"] == "104"
    assert recorded["planned_loss"] == "2"
    assert recorded["protection_note"].startswith("Bracket requested")
    cancelled_history = await service.cancel_paper_order(
        PaperCancelInput(
            client_order_id=placed[0].client_order_id,
            confirmation="CANCEL PAPER ORDER",
        )
    )
    assert cancelled == ["paper-order"]
    assert cancelled_history[0]["status"] == "CANCEL_REQUESTED"
    refreshed = await service.sync_paper_orders()
    assert refreshed[0]["status"] == "FILLED"
    assert refreshed[0]["next_action"].startswith("Filled:")
    assert (tmp_path / ".revmind" / "paper-orders.db").is_file()
    with pytest.raises(LiveProbeError, match="expired"):
        await service.place_paper_order(
            PaperApprovalInput(approval_id="eligible", confirmation="PLACE PAPER ORDER")
        )


@pytest.mark.asyncio
async def test_probe_failure_is_redacted_and_durable(tmp_path: Path) -> None:
    clients: list[httpx.AsyncClient] = []

    def factory(
        settings: AlpacaMarketDataSettings,
        bindings: tuple[AlpacaInstrumentBinding, ...],
    ) -> AlpacaMarketDataProvider:
        client = httpx.AsyncClient(
            base_url="https://data.alpaca.markets",
            follow_redirects=False,
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(401, text="distinct-secret")
            ),
        )
        clients.append(client)
        return AlpacaMarketDataProvider(settings, bindings, client=client)

    service = DashboardLiveData(
        configured_store(tmp_path), clock=FixedClock(), provider_factory=factory
    )
    with pytest.raises(LiveProbeError) as captured:
        await service.probe()
    assert "distinct" not in str(captured.value)
    state = service.state()
    assert state.status == "FAILED"
    assert state.reason == "UNAVAILABLE_OR_UNAUTHORIZED"
    assert "distinct" not in json.dumps(state.model_dump(mode="json"))
    for client in clients:
        await client.aclose()


@pytest.mark.asyncio
async def test_probe_requires_explicit_alpaca_selection(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / ".revmind")
    store.save(DEFAULT_SETTINGS, "key", "secret", False)
    service = DashboardLiveData(store, clock=FixedClock())
    with pytest.raises(LiveProbeError, match="Select Alpaca"):
        await service.probe()
