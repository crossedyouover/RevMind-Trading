"""Hosted RevMind OS integration boundaries."""

from app.integration.supabase_auth import (
    AuthConfigurationError,
    AuthTokenError,
    SupabaseAuthConfig,
    SupabaseJwtVerifier,
    SupabasePrincipal,
    bearer_token,
)

__all__ = [
    "AuthConfigurationError",
    "AuthTokenError",
    "SupabaseAuthConfig",
    "SupabaseJwtVerifier",
    "SupabasePrincipal",
    "bearer_token",
]
