"""Strict read-only Alpaca adapter for provider-neutral catalyst facts."""

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
from pydantic import SecretStr, ValidationError

from app.catalysts.models import ObservedCatalystFact
from app.catalysts.provider import CatalystProviderError
from app.core.schemas import CatalystSourceType, Instrument
from app.data.observations import SourceIdentity

_BASE_URL = "https://data.alpaca.markets"
_MAX_PAGES = 5
_PAGE_SIZE = 50
_MAX_RESPONSE_BYTES = 1_000_000
_TOKEN = re.compile(r"^[A-Za-z0-9_+=/-]{1,2048}$")


class AlpacaNewsProvider:
    """Fetch bounded news without exposing Alpaca wire data downstream."""

    def __init__(
        self,
        api_key_id: SecretStr,
        api_secret_key: SecretStr,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not isinstance(api_key_id, SecretStr) or not isinstance(api_secret_key, SecretStr):
            raise CatalystProviderError("invalid Alpaca news credentials")
        self._key = api_key_id
        self._secret = api_secret_key
        self._owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(
                base_url=_BASE_URL,
                timeout=httpx.Timeout(connect=5, read=15, write=5, pool=5),
                follow_redirects=False,
            )
        elif str(client.base_url).rstrip("/") != _BASE_URL or client.follow_redirects:
            raise CatalystProviderError("injected Alpaca news client is not security-safe")
        self._client = client

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get_news(
        self,
        instruments: tuple[Instrument, ...],
        *,
        published_start: datetime,
        published_end: datetime,
        observed_at: datetime,
    ) -> tuple[ObservedCatalystFact, ...]:
        if not instruments or any(not isinstance(item, Instrument) for item in instruments):
            raise CatalystProviderError("news instruments must be canonical")
        if any(
            value.tzinfo is None or value.utcoffset() is None
            for value in (published_start, published_end, observed_at)
        ):
            raise CatalystProviderError("news boundaries must include timezone information")
        if published_start >= published_end or observed_at < published_end:
            raise CatalystProviderError("invalid point-in-time news boundary")
        by_symbol = {item.symbol: item for item in instruments}
        page_token: str | None = None
        seen: set[str] = set()
        facts: list[ObservedCatalystFact] = []
        for _ in range(_MAX_PAGES):
            params: dict[str, str | int | bool] = {
                "symbols": ",".join(by_symbol),
                "start": published_start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "end": published_end.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "sort": "asc",
                "limit": _PAGE_SIZE,
                "include_content": "false",
            }
            if page_token is not None:
                params["page_token"] = page_token
            payload = await self._request(params)
            if not isinstance(payload, Mapping) or set(payload) - {"news", "next_page_token"}:
                raise CatalystProviderError("malformed Alpaca news response")
            news = payload.get("news")
            if not isinstance(news, list):
                raise CatalystProviderError("malformed Alpaca news response")
            for raw in news:
                fact = self._fact(raw, by_symbol, observed_at, published_start, published_end)
                if any(item.observation_id == fact.observation_id for item in facts):
                    raise CatalystProviderError("duplicate Alpaca news record")
                facts.append(fact)
            raw_token = payload.get("next_page_token")
            if raw_token is None:
                return tuple(
                    sorted(facts, key=lambda item: (item.observed_at, item.observation_id))
                )
            if (
                not isinstance(raw_token, str)
                or _TOKEN.fullmatch(raw_token) is None
                or raw_token in seen
            ):
                raise CatalystProviderError("malformed Alpaca news pagination")
            seen.add(raw_token)
            page_token = raw_token
        raise CatalystProviderError("Alpaca news response exceeds page limit")

    @staticmethod
    def _fact(
        raw: object,
        by_symbol: dict[str, Instrument],
        observed_at: datetime,
        start: datetime,
        end: datetime,
    ) -> ObservedCatalystFact:
        if not isinstance(raw, Mapping):
            raise CatalystProviderError("invalid Alpaca news record")
        required = {
            "id",
            "headline",
            "created_at",
            "updated_at",
            "symbols",
            "source",
            "url",
        }
        if not required <= set(raw):
            raise CatalystProviderError("invalid Alpaca news record")
        try:
            if isinstance(raw["id"], bool) or not isinstance(raw["id"], (str, int)):
                raise ValueError
            record_id = str(raw["id"])
            headline = raw["headline"]
            published = datetime.fromisoformat(str(raw["created_at"]).replace("Z", "+00:00"))
            updated = datetime.fromisoformat(str(raw["updated_at"]).replace("Z", "+00:00"))
            article_url = raw["url"]
            if not isinstance(article_url, str):
                raise ValueError
            parsed_url = urlsplit(article_url)
            if parsed_url.scheme != "https" or not parsed_url.hostname:
                raise ValueError
            symbols = raw["symbols"]
            if not isinstance(symbols, list) or any(not isinstance(item, str) for item in symbols):
                raise ValueError
            instruments = tuple(
                sorted(
                    {by_symbol[item] for item in symbols if item in by_symbol},
                    key=lambda item: (
                        item.asset_class.value,
                        item.exchange or "",
                        item.symbol,
                        item.currency or "",
                    ),
                )
            )
            if not instruments or published < start or published >= end or updated >= end:
                raise ValueError
            identity = f"{record_id}|{published.astimezone(UTC).isoformat()}"
            return ObservedCatalystFact(
                observation_id=UUID(
                    bytes=uuid5(NAMESPACE_URL, "alpaca-news:" + identity).bytes,
                    version=4,
                ),
                headline=headline,
                source=SourceIdentity(name="ALPACA_NEWS"),
                source_type=CatalystSourceType.SECONDARY,
                observed_at=observed_at,
                published_at=published,
                source_record_id=record_id,
                url=article_url,
                source_summary=raw.get("summary") or None,
                instruments=instruments,
            )
        except (ValidationError, TypeError, ValueError, KeyError) as exc:
            raise CatalystProviderError("invalid Alpaca news record") from exc

    async def _request(self, params: dict[str, str | int | bool]) -> object:
        headers = {
            "APCA-API-KEY-ID": self._key.get_secret_value(),
            "APCA-API-SECRET-KEY": self._secret.get_secret_value(),
            "Accept-Encoding": "identity",
        }
        try:
            async with self._client.stream(
                "GET", "/v1beta1/news", params=params, headers=headers
            ) as response:
                if not 200 <= response.status_code < 300:
                    raise CatalystProviderError("Alpaca news is unavailable or unauthorized")
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > _MAX_RESPONSE_BYTES:
                        raise CatalystProviderError("Alpaca news response exceeds size limit")
            return json.loads(bytes(content).decode("utf-8"))
        except CatalystProviderError:
            raise
        except (httpx.HTTPError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise CatalystProviderError("Alpaca news request failed") from exc
