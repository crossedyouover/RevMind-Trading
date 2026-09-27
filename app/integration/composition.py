"""Fail-closed composition of the hosted read-only RevMind Trading application."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime

import httpx

from app.integration.asgi import HostedHttpConfig, ReadOnlyAsgiApp
from app.integration.hosted_api import HostedApi
from app.integration.supabase_auth import (
    SigningKeyClient,
    SupabaseAuthConfig,
    SupabaseJwtVerifier,
)
from app.integration.supabase_research import (
    SupabaseResearchConfig,
    SupabaseResearchRunReader,
)


class HostedEnvironmentError(ValueError):
    """Required hosted configuration is absent, ambiguous, or unsafe."""


_REQUIRED = frozenset(
    {
        "REVMIND_OS_SUPABASE_ISSUER",
        "REVMIND_OS_JWT_AUDIENCE",
        "REVMIND_TRADING_SUPABASE_URL",
        "REVMIND_TRADING_SUPABASE_SERVICE_KEY",
        "REVMIND_TRADING_FRONTEND_ORIGIN",
    }
)


@dataclass(frozen=True, slots=True)
class HostedEnvironment:
    os_issuer: str
    jwt_audience: str
    trading_supabase_url: str
    trading_service_key: str = field(repr=False)
    frontend_origin: str = ""

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> HostedEnvironment:
        missing = sorted(key for key in _REQUIRED if not values.get(key))
        if missing:
            raise HostedEnvironmentError("missing required hosted configuration")
        return cls(
            os_issuer=values["REVMIND_OS_SUPABASE_ISSUER"],
            jwt_audience=values["REVMIND_OS_JWT_AUDIENCE"],
            trading_supabase_url=values["REVMIND_TRADING_SUPABASE_URL"],
            trading_service_key=values["REVMIND_TRADING_SUPABASE_SERVICE_KEY"],
            frontend_origin=values["REVMIND_TRADING_FRONTEND_ORIGIN"],
        )


def build_hosted_application(
    environment: HostedEnvironment,
    *,
    key_client: SigningKeyClient | None = None,
    research_client: httpx.Client | None = None,
    clock: Callable[[], datetime] | None = None,
) -> ReadOnlyAsgiApp:
    """Compose only authentication, read-only research storage, and HTTP transport."""

    verifier = SupabaseJwtVerifier(
        SupabaseAuthConfig(
            issuer=environment.os_issuer,
            audience=environment.jwt_audience,
        ),
        key_client=key_client,
        clock=clock,
    )
    reader = SupabaseResearchRunReader(
        SupabaseResearchConfig(
            base_url=environment.trading_supabase_url,
            service_key=environment.trading_service_key,
        ),
        client=research_client,
    )
    return ReadOnlyAsgiApp(
        HostedApi(verifier, reader),
        HostedHttpConfig(allowed_origin=environment.frontend_origin),
    )
