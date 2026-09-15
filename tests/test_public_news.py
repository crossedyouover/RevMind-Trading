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
    batch = await provider.get_news(datetime(2026, 9, 12, tzinfo=UTC))
    assert len(batch.headlines) == 1
    assert batch.headlines[0].source == "FEDERAL_RESERVE"
    assert len(batch.available_sources) == 10
    assert batch.unavailable_sources == ()
    assert all(item.published_at.tzinfo is UTC for item in batch.headlines)
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


@pytest.mark.asyncio
async def test_public_rss_provider_reports_partial_source_availability() -> None:
    payload = b"""<?xml version="1.0"?><rss><channel><item>
    <title>Central bank publishes a policy update</title>
    <link>https://www.federalreserve.gov/example.htm</link>
    <pubDate>Fri, 11 Sep 2026 12:00:00 GMT</pubDate>
    </item></channel></rss>"""

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("press_monetary.xml"):
            return httpx.Response(503)
        return httpx.Response(200, content=payload)

    client = httpx.AsyncClient(
        follow_redirects=False,
        transport=httpx.MockTransport(respond),
    )
    provider = PublicRssNewsProvider(client=client)
    batch = await provider.get_news(datetime(2026, 9, 12, tzinfo=UTC))
    assert len(batch.headlines) == 1
    assert len(batch.available_sources) == 9
    assert batch.unavailable_sources == ("FED_MONETARY_POLICY",)
    await client.aclose()


@pytest.mark.asyncio
async def test_public_rss_provider_balances_sources_deterministically() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        feed = request.url.path.rsplit("/", 1)[-1].replace(".", "-")
        items = "".join(
            "<item>"
            f"<title>{feed} item {index}</title>"
            f"<link>https://example.test/{feed}/{index}</link>"
            f"<pubDate>Fri, 11 Sep 2026 12:{index:02d}:00 GMT</pubDate>"
            "</item>"
            for index in range(12)
        )
        return httpx.Response(200, content=f"<rss><channel>{items}</channel></rss>")

    client = httpx.AsyncClient(
        follow_redirects=False,
        transport=httpx.MockTransport(respond),
    )
    provider = PublicRssNewsProvider(client=client)
    batch = await provider.get_news(datetime(2026, 9, 12, tzinfo=UTC))
    counts = {source: 0 for source in batch.available_sources}
    for item in batch.headlines:
        counts[item.source] += 1
    assert len(batch.headlines) == 30
    assert tuple(counts.values()) == (3, 3, 3, 3, 3, 3, 3, 3, 3, 3)
    await client.aclose()
