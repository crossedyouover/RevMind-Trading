"""Bounded read-only adapter for the official USGS earthquake summary feed."""

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
from pydantic import ValidationError

from app.data.observations import SourceIdentity
from app.intelligence.events.models import GlobalEventCategory, ObservedGlobalEvent
from app.intelligence.events.provider import GlobalEventProviderError

_BASE_URL = "https://earthquake.usgs.gov"
_FEED_PATH = "/earthquakes/feed/v1.0/summary/all_hour.geojson"
_MAX_RESPONSE_BYTES = 1_000_000
_MAX_FEATURES = 500
_SOURCE = SourceIdentity(name="USGS_EARTHQUAKE_GEOJSON")


class UsgsEarthquakeProvider:
    """Fetch one bounded official feed without exposing USGS wire data downstream."""

    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        self._owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(
                base_url=_BASE_URL,
                timeout=httpx.Timeout(connect=5, read=15, write=5, pool=5),
                follow_redirects=False,
            )
        elif str(client.base_url).rstrip("/") != _BASE_URL or client.follow_redirects:
            raise GlobalEventProviderError("injected USGS client is not security-safe")
        self._client = client

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get_events(self, *, observed_at: datetime) -> tuple[ObservedGlobalEvent, ...]:
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise GlobalEventProviderError("observed_at must include timezone information")
        cutoff = observed_at.astimezone(UTC)
        payload = await self._request()
        if not isinstance(payload, Mapping) or payload.get("type") != "FeatureCollection":
            raise GlobalEventProviderError("malformed USGS earthquake response")
        features = payload.get("features")
        if not isinstance(features, list) or len(features) > _MAX_FEATURES:
            raise GlobalEventProviderError("malformed USGS earthquake response")
        events = tuple(self._event(feature, cutoff) for feature in features)
        identities = {event.observation_id for event in events}
        if len(identities) != len(events):
            raise GlobalEventProviderError("duplicate USGS earthquake revision")
        return tuple(sorted(events, key=lambda event: (event.observed_at, event.observation_id)))

    @staticmethod
    def _event(raw: object, observed_at: datetime) -> ObservedGlobalEvent:
        if not isinstance(raw, Mapping) or raw.get("type") != "Feature":
            raise GlobalEventProviderError("invalid USGS earthquake feature")
        properties = raw.get("properties")
        required = {"place", "time", "updated", "url", "status", "mag"}
        if not isinstance(properties, Mapping) or not required <= set(properties):
            raise GlobalEventProviderError("invalid USGS earthquake feature")
        try:
            event_id = raw["id"]
            place = properties["place"]
            status = properties["status"]
            url = properties["url"]
            occurred_millis = _strict_millis(properties["time"])
            updated_millis = _strict_millis(properties["updated"])
            magnitude = _strict_magnitude(properties["mag"])
            if not all(
                isinstance(value, str) and value.strip()
                for value in (event_id, place, status)
            ):
                raise ValueError
            if not isinstance(url, str):
                raise ValueError
            parsed = urlsplit(url)
            if (
                parsed.scheme != "https"
                or parsed.hostname != "earthquake.usgs.gov"
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise ValueError
            occurred_at = datetime.fromtimestamp(occurred_millis / 1000, tz=UTC)
            updated_at = datetime.fromtimestamp(updated_millis / 1000, tz=UTC)
            if occurred_at > observed_at or updated_at > observed_at:
                raise ValueError
            revision_identity = f"{event_id}|{updated_millis}"
            observation_id = UUID(
                bytes=uuid5(NAMESPACE_URL, "usgs-earthquake:" + revision_identity).bytes,
                version=4,
            )
            return ObservedGlobalEvent(
                observation_id=observation_id,
                observed_at=observed_at,
                source=_SOURCE,
                category=GlobalEventCategory.NATURAL_DISASTER,
                headline=f"Magnitude {magnitude} earthquake — {place.strip()}",
                occurred_at=occurred_at,
                published_at=updated_at,
                source_event_id=event_id.strip(),
                source_revision_id=str(updated_millis),
                source_description=f"USGS status: {status.strip()}",
                source_url=url,
            )
        except (
            InvalidOperation,
            KeyError,
            OSError,
            OverflowError,
            TypeError,
            ValidationError,
            ValueError,
        ) as exc:
            raise GlobalEventProviderError("invalid USGS earthquake feature") from exc

    async def _request(self) -> object:
        try:
            async with self._client.stream(
                "GET", _FEED_PATH, headers={"Accept-Encoding": "identity"}
            ) as response:
                if response.status_code != 200:
                    raise GlobalEventProviderError("USGS earthquake feed is unavailable")
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > _MAX_RESPONSE_BYTES:
                        raise GlobalEventProviderError(
                            "USGS earthquake response exceeds size limit"
                        )
            return json.loads(bytes(content).decode("utf-8"))
        except GlobalEventProviderError:
            raise
        except (httpx.HTTPError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise GlobalEventProviderError("USGS earthquake request failed") from exc


def _strict_millis(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("timestamp must be nonnegative integer milliseconds")
    return value


def _strict_magnitude(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("magnitude must be numeric")
    magnitude = Decimal(str(value))
    if not magnitude.is_finite() or magnitude < Decimal("-2") or magnitude > Decimal("12"):
        raise ValueError("magnitude is outside the bounded domain")
    return magnitude
