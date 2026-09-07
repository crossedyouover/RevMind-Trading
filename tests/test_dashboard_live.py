"""On-demand dashboard market data stays read-only, bounded, and receipt-aware."""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.dashboard.live import DashboardLiveData, LiveProbeError
from app.dashboard.settings import DEFAULT_SETTINGS, DataMode, SettingsStore
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
