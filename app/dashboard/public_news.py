"""Bounded allowlisted ingestion for official public RSS feeds."""

from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Final
from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx

from app.core.schemas import CanonicalModel, UtcDatetime

_MAX_BYTES: Final = 300_000
_FEEDS: Final = (
    ("FEDERAL_RESERVE", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("ECB", "https://mid.ecb.europa.eu/rss/mid.xml"),
)


class PublicHeadline(CanonicalModel):
    headline: str
    source: str
    published_at: UtcDatetime
    url: str


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

    async def get_news(self, observed_at: datetime) -> tuple[PublicHeadline, ...]:
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise PublicNewsError("receipt time must include timezone information")
        results: list[PublicHeadline] = []
        for source, url in _FEEDS:
            try:
                response = await self._client.get(url, headers={"Accept": "application/rss+xml"})
                if response.status_code != 200 or len(response.content) > _MAX_BYTES:
                    continue
                results.extend(self._parse(source, response.content, observed_at))
            except httpx.HTTPError:
                continue
        if not results:
            raise PublicNewsError("official public news feeds are unavailable")
        unique = {(item.source, item.url, item.published_at): item for item in results}
        return tuple(
            sorted(unique.values(), key=lambda item: item.published_at, reverse=True)[:30]
        )

    @staticmethod
    def _parse(source: str, payload: bytes, observed_at: datetime) -> list[PublicHeadline]:
        upper = payload[:4096].upper()
        if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
            return []
        try:
            root = ElementTree.fromstring(payload)
        except ElementTree.ParseError:
            return []
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
