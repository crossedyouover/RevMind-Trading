from datetime import UTC, datetime

import httpx
import pytest

from app.dashboard.public_news import PublicRssNewsProvider


@pytest.mark.asyncio
async def test_public_rss_provider_is_bounded_timestamped_and_deduplicated() -> None:
    payload = b"""<?xml version="1.0"?><rss><channel><item>
    <title>Central bank publishes a policy update</title>
    <link>https://www.federalreserve.gov/example.htm</link>
    <pubDate>Fri, 11 Sep 2026 12:00:00 GMT</pubDate>
    </item></channel></rss>"""
    client = httpx.AsyncClient(
        follow_redirects=False,
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=payload)),
    )
    provider = PublicRssNewsProvider(client=client)
    items = await provider.get_news(datetime(2026, 9, 12, tzinfo=UTC))
    assert len(items) == 2
    assert {item.source for item in items} == {"FEDERAL_RESERVE", "ECB"}
    assert all(item.published_at.tzinfo is UTC for item in items)
    await client.aclose()


@pytest.mark.asyncio
async def test_public_rss_provider_rejects_entity_payloads() -> None:
    payload = b'<!DOCTYPE rss [<!ENTITY x "unsafe">]><rss><channel/></rss>'
    client = httpx.AsyncClient(
        follow_redirects=False,
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=payload)),
    )
    provider = PublicRssNewsProvider(client=client)
    with pytest.raises(RuntimeError, match="unavailable"):
        await provider.get_news(datetime(2026, 9, 12, tzinfo=UTC))
    await client.aclose()
