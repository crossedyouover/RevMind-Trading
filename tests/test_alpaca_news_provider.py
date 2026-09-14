"""Alpaca news remains bounded, PIT-safe, and provider-neutral."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr

from app.catalysts.provider import CatalystProviderError
from app.core.schemas import AssetClass, Instrument
from app.data.providers.alpaca.news import AlpacaNewsProvider


def instrument(symbol: str) -> Instrument:
    return Instrument(
        symbol=symbol,
        exchange="XNAS",
        asset_class=AssetClass.EQUITY,
        currency="USD",
    )


@pytest.mark.asyncio
async def test_news_adapter_uses_bounded_endpoint_and_canonical_facts() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "news": [
                    {
                        "id": 42,
                        "headline": "A factual timestamped headline",
                        "summary": "Source supplied summary.",
                        "created_at": "2026-09-06T12:00:00Z",
                        "updated_at": "2026-09-06T12:01:00Z",
                        "symbols": ["MSFT", "AAPL"],
                        "source": "example",
                        "url": "https://example.test/story",
                    }
                ],
                "next_page_token": None,
            },
        )

    client = httpx.AsyncClient(
        base_url="https://data.alpaca.markets",
        follow_redirects=False,
        transport=httpx.MockTransport(respond),
    )
    provider = AlpacaNewsProvider(SecretStr("key"), SecretStr("secret"), client=client)
    end = datetime(2026, 9, 7, 9, 40, tzinfo=UTC)
    facts = await provider.get_news(
        (instrument("AAPL"), instrument("MSFT")),
        published_start=end - timedelta(days=7),
        published_end=end,
        observed_at=datetime(2026, 9, 7, 10, 0, tzinfo=UTC),
    )
    assert len(facts) == 1
    assert facts[0].headline == "A factual timestamped headline"
    assert tuple(item.symbol for item in facts[0].instruments) == ("AAPL", "MSFT")
    assert facts[0].source.name == "ALPACA_NEWS"
    request = requests[0]
    assert request.url.path == "/v1beta1/news"
    assert request.url.params["symbols"] == "AAPL,MSFT"
    assert request.url.params["sort"] == "asc"
    assert request.url.params["limit"] == "50"
    assert request.url.params["include_content"] == "false"
    assert request.headers["APCA-API-KEY-ID"] == "key"
    assert "secret" not in repr(facts)
    await client.aclose()


@pytest.mark.asyncio
async def test_news_adapter_rejects_records_after_pit_cutoff() -> None:
    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "news": [
                    {
                        "id": 43,
                        "headline": "Future headline",
                        "created_at": "2026-09-07T09:41:00Z",
                        "updated_at": "2026-09-07T09:41:00Z",
                        "symbols": ["AAPL"],
                        "source": "example",
                        "url": "https://example.test/future",
                    }
                ],
                "next_page_token": None,
            },
        )

    client = httpx.AsyncClient(
        base_url="https://data.alpaca.markets",
        follow_redirects=False,
        transport=httpx.MockTransport(respond),
    )
    provider = AlpacaNewsProvider(SecretStr("key"), SecretStr("secret"), client=client)
    end = datetime(2026, 9, 7, 9, 40, tzinfo=UTC)
    with pytest.raises(CatalystProviderError, match="invalid Alpaca news record"):
        await provider.get_news(
            (instrument("AAPL"),),
            published_start=end - timedelta(days=7),
            published_end=end,
            observed_at=datetime(2026, 9, 7, 10, 0, tzinfo=UTC),
        )
    await client.aclose()
