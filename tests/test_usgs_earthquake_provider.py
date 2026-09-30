"""Bounded official USGS earthquake adapter tests."""

from datetime import UTC, datetime

import httpx
import pytest

from app.intelligence.events import (
    GlobalEventCategory,
    GlobalEventProviderError,
    UsgsEarthquakeProvider,
)

OBSERVED = datetime(2026, 9, 30, 10, 0, tzinfo=UTC)
OCCURRED_MS = 1790758800000
UPDATED_MS = 1790759700000


def payload(**property_changes: object) -> dict[str, object]:
    properties: dict[str, object] = {
        "mag": 4.7,
        "place": "10 km NW of Example",
        "time": OCCURRED_MS,
        "updated": UPDATED_MS,
        "url": "https://earthquake.usgs.gov/earthquakes/eventpage/example1",
        "status": "reviewed",
    }
    properties.update(property_changes)
    return {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "id": "example1", "properties": properties}],
    }


def client_for(response: httpx.Response) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="https://earthquake.usgs.gov",
        follow_redirects=False,
        transport=httpx.MockTransport(lambda _request: response),
    )


@pytest.mark.asyncio
async def test_adapter_maps_official_feature_to_canonical_event() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=payload())

    client = httpx.AsyncClient(
        base_url="https://earthquake.usgs.gov",
        follow_redirects=False,
        transport=httpx.MockTransport(respond),
    )
    provider = UsgsEarthquakeProvider(client=client)

    events = await provider.get_events(observed_at=OBSERVED)

    assert len(events) == 1
    event = events[0]
    assert event.category is GlobalEventCategory.NATURAL_DISASTER
    assert event.source.name == "USGS_EARTHQUAKE_GEOJSON"
    assert event.source_event_id == "example1"
    assert event.source_revision_id == str(UPDATED_MS)
    assert event.headline == "Magnitude 4.7 earthquake — 10 km NW of Example"
    assert event.countries == event.regions == event.instruments == ()
    assert event.observed_at == OBSERVED
    assert requests[0].url.path == "/earthquakes/feed/v1.0/summary/all_hour.geojson"
    assert requests[0].headers["Accept-Encoding"] == "identity"
    await client.aclose()


@pytest.mark.asyncio
async def test_same_revision_has_stable_identity_and_correction_is_distinct() -> None:
    first_client = client_for(httpx.Response(200, json=payload()))
    corrected_client = client_for(
        httpx.Response(200, json=payload(updated=UPDATED_MS + 1, status="automatic"))
    )
    first = await UsgsEarthquakeProvider(client=first_client).get_events(observed_at=OBSERVED)
    repeated = await UsgsEarthquakeProvider(client=first_client).get_events(observed_at=OBSERVED)
    corrected = await UsgsEarthquakeProvider(client=corrected_client).get_events(
        observed_at=OBSERVED
    )

    assert first[0].observation_id == repeated[0].observation_id
    assert corrected[0].observation_id != first[0].observation_id
    await first_client.aclose()
    await corrected_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"time": True},
        {"updated": -1},
        {"mag": "4.7"},
        {"mag": 99},
        {"place": ""},
        {"url": "http://earthquake.usgs.gov/unsafe"},
        {"url": "https://example.test/spoof"},
    ],
)
async def test_malformed_or_unsafe_source_fields_fail_closed(changes: dict[str, object]) -> None:
    client = client_for(httpx.Response(200, json=payload(**changes)))
    provider = UsgsEarthquakeProvider(client=client)
    with pytest.raises(GlobalEventProviderError, match="invalid USGS"):
        await provider.get_events(observed_at=OBSERVED)
    await client.aclose()


@pytest.mark.asyncio
async def test_future_source_times_and_naive_knowledge_time_fail_closed() -> None:
    future = int(OBSERVED.timestamp() * 1000) + 1
    client = client_for(httpx.Response(200, json=payload(updated=future)))
    provider = UsgsEarthquakeProvider(client=client)
    with pytest.raises(GlobalEventProviderError, match="invalid USGS"):
        await provider.get_events(observed_at=OBSERVED)
    with pytest.raises(GlobalEventProviderError, match="timezone"):
        await provider.get_events(observed_at=datetime(2026, 9, 30, 10, 0))
    await client.aclose()


@pytest.mark.asyncio
async def test_duplicate_revisions_and_excess_features_fail_closed() -> None:
    duplicated = payload()
    duplicated["features"] = duplicated["features"] * 2  # type: ignore[operator]
    duplicate_client = client_for(httpx.Response(200, json=duplicated))
    with pytest.raises(GlobalEventProviderError, match="duplicate"):
        await UsgsEarthquakeProvider(client=duplicate_client).get_events(observed_at=OBSERVED)

    oversized = {"type": "FeatureCollection", "features": [payload()["features"][0]] * 501}
    count_client = client_for(httpx.Response(200, json=oversized))
    with pytest.raises(GlobalEventProviderError, match="malformed"):
        await UsgsEarthquakeProvider(client=count_client).get_events(observed_at=OBSERVED)
    await duplicate_client.aclose()
    await count_client.aclose()


@pytest.mark.asyncio
async def test_http_json_and_response_size_fail_closed() -> None:
    for response, message in (
        (httpx.Response(503), "unavailable"),
        (httpx.Response(200, content=b"not-json"), "request failed"),
        (httpx.Response(200, content=b"x" * 1_000_001), "size limit"),
    ):
        client = client_for(response)
        with pytest.raises(GlobalEventProviderError, match=message):
            await UsgsEarthquakeProvider(client=client).get_events(observed_at=OBSERVED)
        await client.aclose()


def test_injected_client_must_use_pinned_origin_without_redirects() -> None:
    wrong_origin = httpx.AsyncClient(base_url="https://example.test")
    redirects = httpx.AsyncClient(base_url="https://earthquake.usgs.gov", follow_redirects=True)
    with pytest.raises(GlobalEventProviderError, match="security-safe"):
        UsgsEarthquakeProvider(client=wrong_origin)
    with pytest.raises(GlobalEventProviderError, match="security-safe"):
        UsgsEarthquakeProvider(client=redirects)
