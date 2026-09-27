"""Fail-closed verification for RevMind OS-issued Supabase access tokens.

The browser presents a central RevMind OS access token. RevMind Trading verifies
the signature and immutable identity claims before constructing a tenant context.
No service-role key or broker credential crosses this boundary.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Protocol
from uuid import UUID

import jwt
from jwt import PyJWK, PyJWKClient
from jwt.exceptions import InvalidTokenError, PyJWKClientError


class AuthConfigurationError(ValueError):
    """The trusted identity-provider configuration is unsafe or incomplete."""


class AuthTokenError(ValueError):
    """A presented access token cannot establish an authenticated principal."""


_ALGORITHMS: Final[tuple[str, ...]] = ("RS256", "ES256")
_MAX_TOKEN_LENGTH: Final[int] = 16_384


@dataclass(frozen=True, slots=True)
class SupabaseAuthConfig:
    """Pinned public identity-provider details, never inferred from a token."""

    issuer: str
    audience: str = "authenticated"
    jwks_url: str | None = None
    leeway_seconds: int = 30

    def __post_init__(self) -> None:
        issuer = self.issuer.rstrip("/")
        if not issuer.startswith("https://") or ".supabase.co/auth/v1" not in issuer:
            raise AuthConfigurationError("issuer must be an HTTPS Supabase auth issuer")
        if not self.audience or len(self.audience) > 128:
            raise AuthConfigurationError("audience must be a bounded non-empty value")
        if not 0 <= self.leeway_seconds <= 300:
            raise AuthConfigurationError("leeway must be between 0 and 300 seconds")
        jwks_url = self.jwks_url or f"{issuer}/.well-known/jwks.json"
        if not jwks_url.startswith(f"{issuer}/"):
            raise AuthConfigurationError("JWKS URL must remain under the configured issuer")
        object.__setattr__(self, "issuer", issuer)
        object.__setattr__(self, "jwks_url", jwks_url)


@dataclass(frozen=True, slots=True)
class SupabasePrincipal:
    """Verified identity and tenant boundary for one request."""

    user_id: UUID
    audience: str
    role: str
    email: str | None
    organization_id: UUID | None
    issued_at: datetime
    expires_at: datetime


class SigningKeyClient(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> PyJWK: ...


def bearer_token(value: str | None) -> str:
    """Extract exactly one bounded Authorization bearer token."""

    if value is None:
        raise AuthTokenError("authorization bearer token is required")
    scheme, separator, token = value.partition(" ")
    if separator != " " or scheme.lower() != "bearer":
        raise AuthTokenError("authorization must use the Bearer scheme")
    if not token or token.strip() != token or any(character.isspace() for character in token):
        raise AuthTokenError("bearer token is malformed")
    if len(token) > _MAX_TOKEN_LENGTH:
        raise AuthTokenError("bearer token exceeds the accepted size")
    return token


class SupabaseJwtVerifier:
    """Verify a Supabase JWT and materialize a strict RevMind principal."""

    def __init__(
        self,
        config: SupabaseAuthConfig,
        *,
        key_client: SigningKeyClient | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        jwks_url = config.jwks_url
        if jwks_url is None:
            raise AuthConfigurationError("JWKS URL is required")
        self._keys = key_client or PyJWKClient(jwks_url, cache_keys=True, lifespan=300)
        self._clock = clock or (lambda: datetime.now(UTC))

    def verify_authorization(self, authorization: str | None) -> SupabasePrincipal:
        return self.verify_token(bearer_token(authorization))

    def verify_token(self, token: str) -> SupabasePrincipal:
        if not token or len(token) > _MAX_TOKEN_LENGTH:
            raise AuthTokenError("access token is malformed")
        try:
            header = jwt.get_unverified_header(token)
            algorithm = header.get("alg")
            if algorithm not in _ALGORITHMS:
                raise AuthTokenError("access token uses an untrusted algorithm")
            signing_key = self._keys.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=[algorithm],
                audience=self.config.audience,
                issuer=self.config.issuer,
                leeway=self.config.leeway_seconds,
                options={
                    "require": ["sub", "aud", "exp", "iat", "role"],
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                },
            )
        except AuthTokenError:
            raise
        except (InvalidTokenError, PyJWKClientError, TypeError, ValueError) as error:
            raise AuthTokenError("access token verification failed") from error
        return self._principal(claims)

    def _principal(self, claims: Mapping[str, Any]) -> SupabasePrincipal:
        try:
            user_id = UUID(_required_text(claims, "sub"))
            role = _required_text(claims, "role")
            if role != "authenticated":
                raise AuthTokenError("access token role is not authenticated")
            audience_claim = claims["aud"]
            audiences = (
                (audience_claim,)
                if isinstance(audience_claim, str)
                else tuple(audience_claim)
                if isinstance(audience_claim, list)
                else ()
            )
            if self.config.audience not in audiences:
                raise AuthTokenError("access token audience does not match")
            issued_at = _timestamp(claims, "iat")
            expires_at = _timestamp(claims, "exp")
            now = self._clock()
            if now.tzinfo is None or now.utcoffset() is None:
                raise AuthConfigurationError("authentication clock must be timezone-aware")
            leeway = timedelta(seconds=self.config.leeway_seconds)
            if issued_at > now + leeway:
                raise AuthTokenError("access token was issued in the future")
            if expires_at <= now - leeway:
                raise AuthTokenError("access token has expired")
            if "nbf" in claims and _timestamp(claims, "nbf") > now + leeway:
                raise AuthTokenError("access token is not active yet")
            email_claim = claims.get("email")
            if email_claim is not None and (
                not isinstance(email_claim, str) or not 1 <= len(email_claim) <= 254
            ):
                raise AuthTokenError("access token email claim is malformed")
            organization_claim = claims.get("organization_id")
            organization_id = (
                UUID(organization_claim) if isinstance(organization_claim, str) else None
            )
            if organization_claim is not None and organization_id is None:
                raise AuthTokenError("access token organization claim is malformed")
        except AuthConfigurationError:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise AuthTokenError("access token claims are malformed") from error
        return SupabasePrincipal(
            user_id=user_id,
            audience=self.config.audience,
            role=role,
            email=email_claim,
            organization_id=organization_id,
            issued_at=issued_at,
            expires_at=expires_at,
        )


def _required_text(claims: Mapping[str, Any], key: str) -> str:
    value = claims[key]
    if not isinstance(value, str) or not value or len(value) > 512:
        raise AuthTokenError(f"access token {key} claim is malformed")
    return value


def _timestamp(claims: Mapping[str, Any], key: str) -> datetime:
    value = claims[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AuthTokenError(f"access token {key} claim is malformed")
    return datetime.fromtimestamp(value, UTC)
