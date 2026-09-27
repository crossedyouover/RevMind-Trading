from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.integration.hosted_api import (
    HostedApi,
    HostedApiRequest,
    HostedResearchRun,
)
from app.integration.supabase_auth import AuthTokenError, SupabasePrincipal

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


class StaticVerifier:
    def __init__(self, principal: SupabasePrincipal | None) -> None:
        self.principal = principal

    def verify_authorization(self, authorization: str | None) -> SupabasePrincipal:
        if authorization != "Bearer valid" or self.principal is None:
            raise AuthTokenError("no")
        return self.principal


@dataclass
class RecordingReader:
    rows: list[HostedResearchRun] = field(default_factory=list)
    calls: list[tuple[UUID, UUID | None, int]] = field(default_factory=list)
    failure: bool = False

    def list_owned(
        self, *, user_id: UUID, organization_id: UUID | None, limit: int
    ) -> list[HostedResearchRun]:
        self.calls.append((user_id, organization_id, limit))
        if self.failure:
            raise OSError("database unavailable")
        return self.rows[:limit]


def principal() -> SupabasePrincipal:
    return SupabasePrincipal(
        user_id=uuid4(),
        audience="authenticated",
        role="authenticated",
        email="trader@example.com",
        organization_id=uuid4(),
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=10),
    )


def request(
    path: str,
    *,
    method: str = "GET",
    token: bool = True,
    query: dict[str, tuple[str, ...]] | None = None,
) -> HostedApiRequest:
    return HostedApiRequest(
        method=method,
        path=path,
        headers={"Authorization": "Bearer valid"} if token else {},
        query=query or {},
    )


def test_health_is_public_but_reveals_no_backend_details() -> None:
    response = HostedApi(StaticVerifier(None), RecordingReader()).handle(
        request("/v1/health", token=False)
    )
    assert response.status == 200
    assert response.body == {
        "service": "revmind-trading-api",
        "status": "ready",
        "authority": "read_only_research",
        "execution": "unavailable",
    }
    assert response.headers["Cache-Control"] == "no-store"
    assert "database" not in str(response.body).lower()


def test_identity_comes_only_from_verified_principal() -> None:
    person = principal()
    response = HostedApi(StaticVerifier(person), RecordingReader()).handle(request("/v1/me"))
    assert response.status == 200
    assert response.body["user_id"] == str(person.user_id)
    assert response.body["organization_id"] == str(person.organization_id)


def test_research_query_uses_verified_tenant_and_bounded_limit() -> None:
    person = principal()
    row = HostedResearchRun(
        id=uuid4(),
        user_id=person.user_id,
        organization_id=person.organization_id,
        source="alpaca-paper-research",
        timeframe="H1",
        status="complete",
        observed_at=NOW,
        created_at=NOW,
    )
    reader = RecordingReader([row])
    response = HostedApi(StaticVerifier(person), reader).handle(
        request("/v1/research-runs", query={"limit": ("7",)})
    )
    assert response.status == 200
    assert reader.calls == [(person.user_id, person.organization_id, 7)]
    assert response.body["items"] == [row.public()]
    assert "user_id" not in response.body["items"][0]  # type: ignore[index]


@pytest.mark.parametrize(
    "query",
    [
        {"user_id": (str(uuid4()),)},
        {"organization_id": (str(uuid4()),)},
        {"limit": ("0",)},
        {"limit": ("101",)},
        {"limit": ("1", "2")},
        {"limit": ("not-a-number",)},
    ],
)
def test_caller_cannot_choose_identity_or_unbounded_limit(
    query: dict[str, tuple[str, ...]],
) -> None:
    reader = RecordingReader()
    response = HostedApi(StaticVerifier(principal()), reader).handle(
        request("/v1/research-runs", query=query)
    )
    assert response.status == 400
    assert reader.calls == []


def test_cross_tenant_storage_result_fails_closed() -> None:
    person = principal()
    foreign = HostedResearchRun(
        id=uuid4(),
        user_id=uuid4(),
        organization_id=person.organization_id,
        source="foreign",
        timeframe="M5",
        status="complete",
        observed_at=NOW,
        created_at=NOW,
    )
    response = HostedApi(StaticVerifier(person), RecordingReader([foreign])).handle(
        request("/v1/research-runs")
    )
    assert response.status == 503
    assert response.body == {"error": "temporarily_unavailable"}


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("POST", "/v1/research-runs", 405),
        ("PUT", "/v1/me", 405),
        ("DELETE", "/v1/research-runs/anything", 405),
        ("GET", "/v1/paper-orders", 404),
        ("GET", "/v1/settings", 404),
    ],
)
def test_mutation_and_sensitive_surfaces_are_unavailable(
    method: str, path: str, expected: int
) -> None:
    response = HostedApi(StaticVerifier(principal()), RecordingReader()).handle(
        request(path, method=method)
    )
    assert response.status == expected


def test_authentication_and_storage_failures_are_safe() -> None:
    unauthenticated = HostedApi(StaticVerifier(None), RecordingReader()).handle(
        request("/v1/me", token=False)
    )
    assert unauthenticated.status == 401
    reader = RecordingReader(failure=True)
    unavailable = HostedApi(StaticVerifier(principal()), reader).handle(
        request("/v1/research-runs")
    )
    assert unavailable.status == 503
    assert "database" not in str(unavailable.body).lower()


def test_duplicate_authorization_headers_are_not_accepted() -> None:
    person = principal()
    api = HostedApi(StaticVerifier(person), RecordingReader())
    duplicate_case = HostedApiRequest(
        method="GET",
        path="/v1/me",
        headers={"Authorization": "Bearer valid", "authorization": "Bearer attacker"},
        query={},
    )
    assert api.handle(duplicate_case).status == 401
