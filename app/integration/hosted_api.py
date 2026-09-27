"""Small read-only API contract for a future Lovable RevMind Trading frontend."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from app.integration.supabase_auth import AuthTokenError, SupabaseJwtVerifier, SupabasePrincipal


@dataclass(frozen=True, slots=True)
class HostedApiRequest:
    method: str
    path: str
    headers: Mapping[str, str]
    query: Mapping[str, Sequence[str]]


@dataclass(frozen=True, slots=True)
class HostedApiResponse:
    status: int
    body: Mapping[str, Any]
    headers: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class HostedResearchRun:
    id: UUID
    user_id: UUID
    organization_id: UUID | None
    source: str
    timeframe: str
    status: str
    observed_at: datetime
    created_at: datetime

    def public(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "source": self.source,
            "timeframe": self.timeframe,
            "status": self.status,
            "observed_at": self.observed_at.isoformat(),
            "created_at": self.created_at.isoformat(),
        }


class ResearchRunReader(Protocol):
    """Storage boundary that must enforce both supplied ownership predicates."""

    def list_owned(
        self,
        *,
        user_id: UUID,
        organization_id: UUID | None,
        limit: int,
    ) -> Sequence[HostedResearchRun]: ...


class PrincipalVerifier(Protocol):
    def verify_authorization(self, authorization: str | None) -> SupabasePrincipal: ...


class HostedApi:
    """Pure read-only router; an HTTP host may translate to and from this contract."""

    _RESPONSE_HEADERS = {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }

    def __init__(self, verifier: PrincipalVerifier, research_runs: ResearchRunReader) -> None:
        self._verifier = verifier
        self._research_runs = research_runs

    def handle(self, request: HostedApiRequest) -> HostedApiResponse:
        method = request.method.upper()
        if method != "GET":
            return self._response(405, {"error": "method_not_allowed"}, allow="GET")
        if request.path == "/v1/health":
            if request.query:
                return self._response(400, {"error": "invalid_query"})
            return self._response(
                200,
                {
                    "service": "revmind-trading-api",
                    "status": "ready",
                    "authority": "read_only_research",
                    "execution": "unavailable",
                },
            )
        try:
            principal = self._verifier.verify_authorization(
                _case_insensitive_header(request.headers, "Authorization")
            )
        except AuthTokenError:
            return self._response(401, {"error": "unauthorized"})
        if request.path == "/v1/me":
            if request.query:
                return self._response(400, {"error": "invalid_query"})
            return self._response(200, _public_principal(principal))
        if request.path == "/v1/research-runs":
            try:
                limit = _limit(request.query)
                rows = self._research_runs.list_owned(
                    user_id=principal.user_id,
                    organization_id=principal.organization_id,
                    limit=limit,
                )
                _validate_owned(rows, principal)
            except ValueError:
                return self._response(400, {"error": "invalid_query"})
            except OSError:
                return self._response(503, {"error": "temporarily_unavailable"})
            return self._response(
                200,
                {"items": [row.public() for row in rows], "count": len(rows), "limit": limit},
            )
        return self._response(404, {"error": "not_found"})

    def _response(
        self, status: int, body: Mapping[str, Any], *, allow: str | None = None
    ) -> HostedApiResponse:
        headers = dict(self._RESPONSE_HEADERS)
        if allow is not None:
            headers["Allow"] = allow
        return HostedApiResponse(status=status, body=body, headers=headers)


def _case_insensitive_header(headers: Mapping[str, str], name: str) -> str | None:
    matches = [value for key, value in headers.items() if key.lower() == name.lower()]
    if len(matches) != 1:
        return None
    return matches[0]


def _limit(query: Mapping[str, Sequence[str]]) -> int:
    if set(query) - {"limit"}:
        raise ValueError("unsupported query parameter")
    values = query.get("limit", ("20",))
    if len(values) != 1 or not values[0].isdigit():
        raise ValueError("limit must be one integer")
    limit = int(values[0])
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    return limit


def _public_principal(principal: SupabasePrincipal) -> dict[str, object]:
    return {
        "user_id": str(principal.user_id),
        "organization_id": (
            str(principal.organization_id) if principal.organization_id is not None else None
        ),
        "email": principal.email,
        "role": principal.role,
    }


def _validate_owned(rows: Sequence[HostedResearchRun], principal: SupabasePrincipal) -> None:
    for row in rows:
        if row.user_id != principal.user_id:
            raise OSError("storage returned cross-user data")
        if row.organization_id != principal.organization_id:
            raise OSError("storage returned cross-organization data")


def build_hosted_api(
    verifier: SupabaseJwtVerifier, research_runs: ResearchRunReader
) -> HostedApi:
    """Explicit composition seam for a future ASGI/serverless adapter."""

    return HostedApi(verifier, research_runs)
