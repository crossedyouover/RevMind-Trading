from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest

from app.integration.supabase_research import (
    SupabaseResearchConfig,
    SupabaseResearchConfigurationError,
    SupabaseResearchRunReader,
)

BASE_URL = "https://trading-project.supabase.co"
KEY = "backend-secret-key-that-is-never-exposed"
NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def config(**changes: object) -> SupabaseResearchConfig:
    values: dict[str, object] = {"base_url": BASE_URL, "service_key": KEY}
    values.update(changes)
    return SupabaseResearchConfig(**values)  # type: ignore[arg-type]


def item(user_id: UUID, organization_id: UUID | None) -> dict[str, object]:
    return {
        "id": str(uuid4()),
        "user_id": str(user_id),
        "organization_id": str(organization_id) if organization_id else None,
        "source": "alpaca-paper-research",
        "timeframe": "H1",
        "status": "complete",
        "observed_at": NOW.isoformat(),
        "created_at": NOW.isoformat(),
    }


def reader(handler: httpx.MockTransport) -> SupabaseResearchRunReader:
    return SupabaseResearchRunReader(config(), client=httpx.Client(transport=handler))


def test_exact_tenant_filters_and_backend_headers_are_applied() -> None:
    user_id, organization_id = uuid4(), uuid4()

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/v1/research_runs"
        assert request.url.params["user_id"] == f"eq.{user_id}"
        assert request.url.params["organization_id"] == f"eq.{organization_id}"
        assert request.url.params["limit"] == "7"
        assert request.url.params["order"] == "created_at.desc,id.asc"
        assert request.headers["apikey"] == KEY
        assert request.headers["authorization"] == f"Bearer {KEY}"
        return httpx.Response(200, json=[item(user_id, organization_id)])

    rows = reader(httpx.MockTransport(handle)).list_owned(
        user_id=user_id, organization_id=organization_id, limit=7
    )
    assert rows[0].user_id == user_id
    assert rows[0].organization_id == organization_id


def test_personal_tenant_requires_null_organization() -> None:
    user_id = uuid4()

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.params["organization_id"] == "is.null"
        return httpx.Response(200, json=[item(user_id, None)])

    rows = reader(httpx.MockTransport(handle)).list_owned(
        user_id=user_id, organization_id=None, limit=1
    )
    assert rows[0].organization_id is None


@pytest.mark.parametrize(
    "base_url",
    [
        "http://project.supabase.co",
        "https://supabase.co.attacker.example",
        "https://project.supabase.co/rest/v1",
        "https://user:pass@project.supabase.co",
        "https://project.supabase.co?redirect=evil",
    ],
)
def test_unsafe_project_origins_are_rejected(base_url: str) -> None:
    with pytest.raises(SupabaseResearchConfigurationError):
        config(base_url=base_url)


def test_secret_is_redacted_from_configuration_repr() -> None:
    assert KEY not in repr(config())


@pytest.mark.parametrize("limit", [0, 101])
def test_unbounded_limits_are_rejected_without_a_request(limit: int) -> None:
    calls = 0

    def handle(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=[])

    with pytest.raises(ValueError):
        reader(httpx.MockTransport(handle)).list_owned(
            user_id=uuid4(), organization_id=None, limit=limit
        )
    assert calls == 0


@pytest.mark.parametrize("foreign_field", ["user_id", "organization_id"])
def test_cross_tenant_results_fail_closed(foreign_field: str) -> None:
    user_id, organization_id = uuid4(), uuid4()
    payload = item(user_id, organization_id)
    payload[foreign_field] = str(uuid4())
    adapter = reader(httpx.MockTransport(lambda _: httpx.Response(200, json=[payload])))
    with pytest.raises(OSError, match="cross-tenant"):
        adapter.list_owned(user_id=user_id, organization_id=organization_id, limit=1)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, json={"message": "details must not escape"}),
        httpx.Response(200, json={"not": "a list"}),
        httpx.Response(200, content=b"not-json"),
    ],
)
def test_remote_and_shape_failures_become_safe_storage_errors(response: httpx.Response) -> None:
    adapter = reader(httpx.MockTransport(lambda _: response))
    with pytest.raises(OSError, match="research store"):
        adapter.list_owned(user_id=uuid4(), organization_id=None, limit=1)


def test_naive_timestamps_and_extra_fields_are_rejected() -> None:
    user_id = uuid4()
    payload = item(user_id, None)
    payload["created_at"] = "2026-09-27T12:00:00"
    adapter = reader(httpx.MockTransport(lambda _: httpx.Response(200, json=[payload])))
    with pytest.raises(OSError, match="malformed"):
        adapter.list_owned(user_id=user_id, organization_id=None, limit=1)

    payload = item(user_id, None)
    payload["result"] = {"private": "must not be decoded"}
    adapter = reader(httpx.MockTransport(lambda _: httpx.Response(200, json=[payload])))
    with pytest.raises(OSError, match="malformed"):
        adapter.list_owned(user_id=user_id, organization_id=None, limit=1)


def test_oversized_response_is_rejected_before_decoding() -> None:
    adapter = SupabaseResearchRunReader(
        config(max_response_bytes=1024),
        client=httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"[" + b" " * 1024))
        ),
    )
    with pytest.raises(OSError, match="safety bound"):
        adapter.list_owned(user_id=uuid4(), organization_id=None, limit=1)


def test_transport_failure_is_normalized() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    with pytest.raises(OSError, match="unavailable"):
        reader(httpx.MockTransport(handle)).list_owned(
            user_id=uuid4(), organization_id=None, limit=1
        )
