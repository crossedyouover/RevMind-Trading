"""Hosted RevMind OS integration boundaries."""

from app.integration.asgi import HostedHttpConfig, HostedHttpConfigurationError, ReadOnlyAsgiApp
from app.integration.composition import (
    HostedEnvironment,
    HostedEnvironmentError,
    build_hosted_application,
)
from app.integration.hosted_api import (
    HostedApi,
    HostedApiRequest,
    HostedApiResponse,
    HostedResearchRun,
    ResearchRunReader,
    build_hosted_api,
)
from app.integration.supabase_auth import (
    AuthConfigurationError,
    AuthTokenError,
    SigningKeyClient,
    SupabaseAuthConfig,
    SupabaseJwtVerifier,
    SupabasePrincipal,
    bearer_token,
)
from app.integration.supabase_research import (
    SupabaseResearchConfig,
    SupabaseResearchConfigurationError,
    SupabaseResearchRunReader,
)

__all__ = [
    "AuthConfigurationError",
    "AuthTokenError",
    "HostedApi",
    "HostedApiRequest",
    "HostedApiResponse",
    "HostedEnvironment",
    "HostedEnvironmentError",
    "HostedHttpConfig",
    "HostedHttpConfigurationError",
    "HostedResearchRun",
    "ResearchRunReader",
    "ReadOnlyAsgiApp",
    "SupabaseAuthConfig",
    "SupabaseJwtVerifier",
    "SupabasePrincipal",
    "SupabaseResearchConfig",
    "SupabaseResearchConfigurationError",
    "SupabaseResearchRunReader",
    "SigningKeyClient",
    "bearer_token",
    "build_hosted_api",
    "build_hosted_application",
]
