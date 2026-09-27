"""Minimal read-only ASGI transport for the hosted RevMind API contract."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

from app.integration.hosted_api import HostedApi, HostedApiRequest, HostedApiResponse

AsgiReceive = Callable[[], Awaitable[Mapping[str, Any]]]
AsgiSend = Callable[[Mapping[str, Any]], Awaitable[None]]


class HostedHttpConfigurationError(ValueError):
    """The trusted frontend transport policy is unsafe."""


@dataclass(frozen=True, slots=True)
class HostedHttpConfig:
    allowed_origin: str
    max_query_bytes: int = 2_048

    def __post_init__(self) -> None:
        parsed = urlsplit(self.allowed_origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise HostedHttpConfigurationError("allowed origin must be one HTTPS origin")
        if not 256 <= self.max_query_bytes <= 8_192:
            raise HostedHttpConfigurationError("query bound is unsafe")
        object.__setattr__(self, "allowed_origin", self.allowed_origin.rstrip("/"))


class ReadOnlyAsgiApp:
    """Translate bounded ASGI HTTP requests into the pure Phase 98 contract."""

    def __init__(self, api: HostedApi, config: HostedHttpConfig) -> None:
        self._api = api
        self._config = config

    async def __call__(
        self,
        scope: Mapping[str, Any],
        receive: AsgiReceive,
        send: AsgiSend,
    ) -> None:
        if scope.get("type") != "http":
            await _send(send, 404, {"error": "unsupported_scope"})
            return
        raw_headers = scope.get("headers")
        if not isinstance(raw_headers, Sequence):
            await _send(send, 400, {"error": "invalid_request"})
            return
        try:
            headers = _headers(raw_headers)
            origin = headers.get("origin")
            if origin is not None and origin != self._config.allowed_origin:
                await _send(send, 403, {"error": "origin_forbidden"})
                return
            method = str(scope.get("method", ""))
            if method.upper() == "OPTIONS":
                if origin is None:
                    await _send(send, 403, {"error": "origin_required"})
                    return
                await _preflight(send, self._config.allowed_origin)
                return
            query_bytes = scope.get("query_string", b"")
            if (
                not isinstance(query_bytes, bytes)
                or len(query_bytes) > self._config.max_query_bytes
            ):
                raise ValueError("invalid query")
            path = scope.get("path")
            if not isinstance(path, str) or not path.startswith("/") or len(path) > 2_048:
                raise ValueError("invalid path")
            query = parse_qs(
                query_bytes.decode("ascii"),
                keep_blank_values=True,
                strict_parsing=True,
                max_num_fields=20,
            )
            response = self._api.handle(
                HostedApiRequest(method=method, path=path, headers=headers, query=query)
            )
        except (UnicodeDecodeError, ValueError):
            await _send(send, 400, {"error": "invalid_request"})
            return
        await _send_hosted(send, response, origin=origin)


def _headers(raw_headers: Sequence[Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in raw_headers:
        if not isinstance(raw, Sequence) or len(raw) != 2:
            raise ValueError("invalid header")
        name_raw, value_raw = raw
        if not isinstance(name_raw, bytes) or not isinstance(value_raw, bytes):
            raise ValueError("invalid header")
        name = name_raw.decode("ascii").lower()
        value = value_raw.decode("latin-1")
        if name in result:
            raise ValueError("duplicate header")
        result[name] = value
    return result


async def _send_hosted(
    send: AsgiSend, response: HostedApiResponse, *, origin: str | None
) -> None:
    headers = dict(response.headers)
    if origin is not None:
        headers.update(_cors(origin))
    await _send(send, response.status, response.body, headers=headers)


async def _preflight(send: AsgiSend, origin: str) -> None:
    headers = _cors(origin)
    headers.update(
        {
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "Authorization, Content-Type",
            "Access-Control-Max-Age": "600",
        }
    )
    await _send(send, 204, {}, headers=headers)


def _cors(origin: str) -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": origin,
        "Vary": "Origin",
    }


async def _send(
    send: AsgiSend,
    status: int,
    body: Mapping[str, Any],
    *,
    headers: Mapping[str, str] | None = None,
) -> None:
    encoded = b"" if status == 204 else json.dumps(body, separators=(",", ":")).encode("utf-8")
    response_headers = dict(headers or {})
    response_headers.setdefault("Content-Type", "application/json; charset=utf-8")
    response_headers.setdefault("Cache-Control", "no-store")
    response_headers.setdefault("X-Content-Type-Options", "nosniff")
    response_headers["Content-Length"] = str(len(encoded))
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (name.lower().encode("ascii"), value.encode("latin-1"))
                for name, value in response_headers.items()
            ],
        }
    )
    await send({"type": "http.response.body", "body": encoded})
