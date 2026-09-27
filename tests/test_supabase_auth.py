from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt import PyJWK

from app.integration.supabase_auth import (
    AuthConfigurationError,
    AuthTokenError,
    SupabaseAuthConfig,
    SupabaseJwtVerifier,
    bearer_token,
)

ISSUER = "https://identity-ref.supabase.co/auth/v1"
NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


class StaticKeyClient:
    def __init__(self, key: Any) -> None:
        self.key = key

    def get_signing_key_from_jwt(self, token: str) -> PyJWK:
        del token
        return PyJWK.from_dict(self.key)


def rsa_keys() -> tuple[Any, dict[str, Any]]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private.public_key().public_numbers()
    return private, {
        "kty": "RSA",
        "kid": "test-rsa",
        "use": "sig",
        "alg": "RS256",
        "n": jwt.utils.to_base64url_uint(public.n).decode(),
        "e": jwt.utils.to_base64url_uint(public.e).decode(),
    }


def token(private: Any, **updates: Any) -> str:
    claims: dict[str, Any] = {
        "sub": str(uuid4()),
        "aud": "authenticated",
        "role": "authenticated",
        "email": "trader@example.com",
        "iat": int((NOW - timedelta(minutes=1)).timestamp()),
        "exp": int((NOW + timedelta(minutes=15)).timestamp()),
        "iss": ISSUER,
    }
    claims.update(updates)
    return jwt.encode(claims, private, algorithm="RS256", headers={"kid": "test-rsa"})


def verifier(private: Any, jwk: dict[str, Any]) -> SupabaseJwtVerifier:
    del private
    return SupabaseJwtVerifier(
        SupabaseAuthConfig(ISSUER),
        key_client=StaticKeyClient(jwk),
        clock=lambda: NOW,
    )


def test_valid_signed_token_materializes_bound_principal() -> None:
    private, jwk = rsa_keys()
    user_id = uuid4()
    organization_id = uuid4()
    principal = verifier(private, jwk).verify_authorization(
        f"Bearer {token(private, sub=str(user_id), organization_id=str(organization_id))}"
    )
    assert principal.user_id == user_id
    assert principal.organization_id == organization_id
    assert principal.audience == "authenticated"
    assert principal.role == "authenticated"
    assert principal.email == "trader@example.com"


@pytest.mark.parametrize(
    "authorization",
    [None, "", "Basic abc", "Bearer", "Bearer ", "Bearer  abc", "Bearer abc def"],
)
def test_bearer_header_fails_closed(authorization: str | None) -> None:
    with pytest.raises(AuthTokenError):
        bearer_token(authorization)


def test_forged_signature_is_rejected() -> None:
    trusted_private, jwk = rsa_keys()
    forged_private, _ = rsa_keys()
    with pytest.raises(AuthTokenError, match="verification failed"):
        verifier(trusted_private, jwk).verify_token(token(forged_private))


@pytest.mark.parametrize(
    "updates",
    [
        {"iss": "https://attacker.supabase.co/auth/v1"},
        {"aud": "service_role"},
        {"role": "service_role"},
        {"exp": int((NOW - timedelta(seconds=31)).timestamp())},
        {"iat": int((NOW + timedelta(seconds=31)).timestamp())},
        {"nbf": int((NOW + timedelta(seconds=31)).timestamp())},
        {"sub": "not-a-uuid"},
        {"organization_id": "not-a-uuid"},
    ],
)
def test_untrusted_or_malformed_claims_are_rejected(updates: dict[str, Any]) -> None:
    private, jwk = rsa_keys()
    with pytest.raises(AuthTokenError):
        verifier(private, jwk).verify_token(token(private, **updates))


def test_algorithm_substitution_is_rejected_before_key_lookup() -> None:
    unsigned = jwt.encode(
        {
            "sub": str(UUID(int=1)),
            "aud": "authenticated",
            "role": "authenticated",
            "iat": int(NOW.timestamp()),
            "exp": int((NOW + timedelta(minutes=1)).timestamp()),
            "iss": ISSUER,
        },
        key="shared-secret-that-is-at-least-32-bytes-long",
        algorithm="HS256",
    )
    private, jwk = rsa_keys()
    with pytest.raises(AuthTokenError, match="untrusted algorithm"):
        verifier(private, jwk).verify_token(unsigned)


@pytest.mark.parametrize(
    "config",
    [
        lambda: SupabaseAuthConfig("http://identity-ref.supabase.co/auth/v1"),
        lambda: SupabaseAuthConfig(ISSUER, jwks_url="https://attacker.example/jwks.json"),
        lambda: SupabaseAuthConfig(ISSUER, leeway_seconds=301),
    ],
)
def test_unsafe_auth_configuration_is_rejected(config: Any) -> None:
    with pytest.raises(AuthConfigurationError):
        config()
