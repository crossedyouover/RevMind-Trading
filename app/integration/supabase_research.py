"""Read-only Supabase/PostgREST adapter for hosted research-run summaries."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from app.integration.hosted_api import HostedResearchRun


class SupabaseResearchConfigurationError(ValueError):
    """Trusted Supabase data-plane configuration is unsafe or incomplete."""


@dataclass(frozen=True, slots=True)
class SupabaseResearchConfig:
    """Pinned backend-only PostgREST configuration."""

    base_url: str
    service_key: str = field(repr=False)
    timeout_seconds: float = 5.0
    max_response_bytes: int = 512_000

    def __post_init__(self) -> None:
        parsed = urlsplit(self.base_url)
        hostname = (parsed.hostname or "").lower()
        if (
            parsed.scheme != "https"
            or not hostname.endswith(".supabase.co")
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
            or parsed.port is not None
        ):
            raise SupabaseResearchConfigurationError(
                "base URL must be an HTTPS Supabase project origin"
            )
        if not 20 <= len(self.service_key) <= 16_384 or any(
            character.isspace() for character in self.service_key
        ):
            raise SupabaseResearchConfigurationError("service key is malformed")
        if not 0.1 <= self.timeout_seconds <= 30:
            raise SupabaseResearchConfigurationError("timeout must be between 0.1 and 30 seconds")
        if not 1_024 <= self.max_response_bytes <= 2_000_000:
            raise SupabaseResearchConfigurationError("response bound is unsafe")
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))


class SupabaseResearchRunReader:
    """Fetch summaries through a backend credential and re-check every tenant row."""

    _SELECT = "id,user_id,organization_id,source,timeframe,status,observed_at,created_at"

    def __init__(
        self,
        config: SupabaseResearchConfig,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self._config = config
        self._client = client or httpx.Client(
            timeout=config.timeout_seconds,
            follow_redirects=False,
        )

    def list_owned(
        self,
        *,
        user_id: UUID,
        organization_id: UUID | None,
        limit: int,
    ) -> Sequence[HostedResearchRun]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        params = {
            "select": self._SELECT,
            "user_id": f"eq.{user_id}",
            "organization_id": (
                f"eq.{organization_id}" if organization_id is not None else "is.null"
            ),
            "order": "created_at.desc,id.asc",
            "limit": str(limit),
        }
        try:
            response = self._client.get(
                f"{self._config.base_url}/rest/v1/research_runs",
                params=params,
                headers={
                    "apikey": self._config.service_key,
                    "Authorization": f"Bearer {self._config.service_key}",
                    "Accept": "application/json",
                },
            )
        except httpx.HTTPError as error:
            raise OSError("research store is unavailable") from error
        if response.status_code != 200:
            raise OSError("research store refused the read")
        if len(response.content) > self._config.max_response_bytes:
            raise OSError("research store response exceeds the safety bound")
        try:
            payload = response.json()
            if not isinstance(payload, list) or len(payload) > limit:
                raise ValueError("research store returned an invalid collection")
            rows = tuple(_row(item) for item in payload)
        except (TypeError, ValueError, KeyError) as error:
            raise OSError("research store returned malformed data") from error
        if any(
            row.user_id != user_id or row.organization_id != organization_id for row in rows
        ):
            raise OSError("research store returned cross-tenant data")
        return rows


def _row(value: Any) -> HostedResearchRun:
    if not isinstance(value, Mapping) or set(value) != {
        "id",
        "user_id",
        "organization_id",
        "source",
        "timeframe",
        "status",
        "observed_at",
        "created_at",
    }:
        raise ValueError("unexpected research-run shape")
    organization = value["organization_id"]
    if organization is not None and not isinstance(organization, str):
        raise ValueError("invalid organization_id")
    return HostedResearchRun(
        id=UUID(_text(value, "id", 36)),
        user_id=UUID(_text(value, "user_id", 36)),
        organization_id=UUID(organization) if isinstance(organization, str) else None,
        source=_text(value, "source", 200),
        timeframe=_text(value, "timeframe", 32),
        status=_text(value, "status", 64),
        observed_at=_timestamp(value, "observed_at"),
        created_at=_timestamp(value, "created_at"),
    )


def _text(value: Mapping[str, Any], key: str, maximum: int) -> str:
    item = value[key]
    if not isinstance(item, str) or not item or len(item) > maximum:
        raise ValueError(f"invalid {key}")
    return item


def _timestamp(value: Mapping[str, Any], key: str) -> datetime:
    item = _text(value, key, 64)
    parsed = datetime.fromisoformat(item.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{key} must be timezone-aware")
    return parsed
