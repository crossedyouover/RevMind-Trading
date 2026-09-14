"""Bounded allowlisted ingestion for official public RSS feeds."""

import asyncio
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Final
from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx

from app.core.schemas import CanonicalModel, UtcDatetime

_MAX_BYTES: Final = 300_000
_MAX_PER_SOURCE: Final = 10
_MAX_HEADLINES: Final = 30
_FEEDS: Final = (
    ("FEDERAL_RESERVE", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("FED_MONETARY_POLICY", "https://www.federalreserve.gov/feeds/press_monetary.xml"),
    ("FED_SPEECHES", "https://www.federalreserve.gov/feeds/speeches.xml"),
    ("FED_TESTIMONY", "https://www.federalreserve.gov/feeds/testimony.xml"),
    ("ECB_PRESS", "https://www.ecb.europa.eu/rss/press.html"),
    ("ECB_STATISTICS", "https://www.ecb.europa.eu/rss/statpress.html"),
    ("ECB_MARKET_INFORMATION", "https://mid.ecb.europa.eu/rss/mid.xml"),
)


class PublicHeadline(CanonicalModel):
    headline: str
    source: str
    published_at: UtcDatetime
    url: str


class PublicNewsBatch(CanonicalModel):
    headlines: tuple[PublicHeadline, ...]
    available_sources: tuple[str, ...]
    unavailable_sources: tuple[str, ...]


class PublicNewsError(RuntimeError):
    pass


class PublicRssNewsProvider:
    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5, read=10, write=5, pool=5),
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get_news(self, observed_at: datetime) -> PublicNewsBatch:
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise PublicNewsError("receipt time must include timezone information")
        results: list[PublicHeadline] = []
        available: list[str] = []
        unavailable: list[str] = []
        fetched = await asyncio.gather(
            *(self._fetch(source, url, observed_at) for source, url in _FEEDS)
        )
        for source, parsed in fetched:
            if parsed is None:
                unavailable.append(source)
                continue
            available.append(source)
            results.extend(parsed)
        if not results:
            raise PublicNewsError("official public news feeds are unavailable")
        unique: dict[tuple[str, datetime], PublicHeadline] = {}
        for item in results:
            unique.setdefault((item.url, item.published_at), item)
        by_source: dict[str, list[PublicHeadline]] = {source: [] for source, _ in _FEEDS}
        for item in unique.values():
            by_source[item.source].append(item)
        for items in by_source.values():
            items.sort(key=lambda item: (item.published_at, item.url), reverse=True)
        selected: list[PublicHeadline] = []
        for index in range(_MAX_PER_SOURCE):
            for source, _ in _FEEDS:
                items = by_source[source]
                if index < len(items):
                    selected.append(items[index])
                    if len(selected) == _MAX_HEADLINES:
                        break
            if len(selected) == _MAX_HEADLINES:
                break
        return PublicNewsBatch(
            headlines=tuple(selected),
            available_sources=tuple(available),
            unavailable_sources=tuple(unavailable),
        )

    async def _fetch(
        self, source: str, url: str, observed_at: datetime
    ) -> tuple[str, list[PublicHeadline] | None]:
        try:
            response = await self._client.get(
                url, headers={"Accept": "application/rss+xml"}
            )
        except httpx.HTTPError:
            return source, None
        if response.status_code != 200 or len(response.content) > _MAX_BYTES:
            return source, None
        return source, self._parse(source, response.content, observed_at)

    @staticmethod
    def _parse(
        source: str, payload: bytes, observed_at: datetime
    ) -> list[PublicHeadline] | None:
        upper = payload[:4096].upper()
        if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
            return None
        try:
            root = ElementTree.fromstring(payload)
        except ElementTree.ParseError:
            return None
        cutoff = observed_at.astimezone(UTC) - timedelta(days=14)
        rows: list[PublicHeadline] = []
        for item in root.findall(".//item")[:50]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            raw_date = (item.findtext("pubDate") or "").strip()
            try:
                published = parsedate_to_datetime(raw_date).astimezone(UTC)
                parsed = urlsplit(link)
                if (
                    not title
                    or len(title) > 500
                    or parsed.scheme != "https"
                    or not parsed.hostname
                    or published < cutoff
                    or published > observed_at
                ):
                    continue
                rows.append(
                    PublicHeadline(
                        headline=title,
                        source=source,
                        published_at=published,
                        url=link,
                    )
                )
            except (TypeError, ValueError):
                continue
        return rows
