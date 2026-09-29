from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import jwt
import pytest

from app.integration.composition import (
    HostedEnvironment,
    HostedEnvironmentError,
    build_hosted_application,
)
from tests.test_hosted_asgi import invoke
from tests.test_supabase_auth import StaticKeyClient, rsa_keys

ISSUER = "https://identity-ref.supabase.co/auth/v1"
DATA_URL = "https://trading-ref.supabase.co"
ORIGIN = "https://trading.revmind.example"
SECRET = "trading-backend-secret-never-for-the-browser"
NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def values() -> dict[str, str]:
    return {
        "REVMIND_OS_SUPABASE_ISSUER": ISSUER,
        "REVMIND_OS_JWT_AUDIENCE": "authenticated",
        "REVMIND_TRADING_SUPABASE_URL": DATA_URL,
        "REVMIND_TRADING_SUPABASE_SERVICE_KEY": SECRET,
        "REVMIND_TRADING_FRONTEND_ORIGIN": ORIGIN,
    }


@pytest.mark.parametrize("missing", list(values()))
def test_every_hosted_value_is_required(missing: str) -> None:
    environment = values()
    del environment[missing]
    with pytest.raises(HostedEnvironmentError, match="missing required"):
        HostedEnvironment.from_mapping(environment)


def test_backend_secret_is_redacted_and_unrelated_environment_is_ignored() -> None:
    environment_values = values()
    environment_values["PATH"] = "unrelated"
    environment = HostedEnvironment.from_mapping(environment_values)
    assert SECRET not in repr(environment)
    assert environment.frontend_origin == ORIGIN


@pytest.mark.asyncio
async def test_composed_application_serves_only_verified_tenant_rows() -> None:
    private, jwk = rsa_keys()
    user_id, organization_id, run_id = uuid4(), uuid4(), uuid4()
    claims: dict[str, Any] = {
        "sub": str(user_id),
        "organization_id": str(organization_id),
        "aud": "authenticated",
        "role": "authenticated",
        "email": "trader@example.com",
        "iat": int((NOW - timedelta(minutes=1)).timestamp()),
        "exp": int((NOW + timedelta(minutes=15)).timestamp()),
        "iss": ISSUER,
    }
    access_token = jwt.encode(
        claims, private, algorithm="RS256", headers={"kid": "test-rsa"}
    )

    def data(request: httpx.Request) -> httpx.Response:
        assert request.url.params["user_id"] == f"eq.{user_id}"
        assert request.url.params["organization_id"] == f"eq.{organization_id}"
        return httpx.Response(
            200,
            json=[
                {
                    "id": str(run_id),
                    "user_id": str(user_id),
                    "organization_id": str(organization_id),
                    "source": "paper-research",
                    "timeframe": "H1",
                    "status": "complete",
                    "observed_at": NOW.isoformat(),
                    "created_at": NOW.isoformat(),
                }
            ],
        )

    application = build_hosted_application(
        HostedEnvironment.from_mapping(values()),
        key_client=StaticKeyClient(jwk),
        research_client=httpx.Client(transport=httpx.MockTransport(data)),
        clock=lambda: NOW,
    )
    status, headers, body = await invoke(
        application,
        path="/v1/research-runs",
        headers=[
            (b"origin", ORIGIN.encode()),
            (b"authorization", f"Bearer {access_token}".encode()),
        ],
    )
    assert status == 200
    assert headers["access-control-allow-origin"] == ORIGIN
    assert str(run_id).encode() in body
    assert str(user_id).encode() not in body
    assert SECRET.encode() not in body


def test_deployment_guide_records_authoritative_project_map() -> None:
    guide = Path("HOSTED_DEPLOYMENT_GUIDE.md").read_text(encoding="utf-8")
    assert "RevMind OS Website" in guide and "built in Lovable" in guide
    assert "PeptMind App" in guide and "existing Lovable project" in guide
    assert "RevMind Prospecting" in guide
    assert "RevMind Trading" in guide and "independent app" in guide
    assert "RevMind CRM" in guide and "Emergent project" in guide
    assert "Do not recreate, combine, rename, or overwrite" in guide
