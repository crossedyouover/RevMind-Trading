from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest

from app.integration.asgi import (
    HostedHttpConfig,
    HostedHttpConfigurationError,
    ReadOnlyAsgiApp,
)
from app.integration.hosted_api import HostedApi
from tests.test_hosted_api import RecordingReader, StaticVerifier

ORIGIN = "https://trade.revmind.example"


async def invoke(
    app: ReadOnlyAsgiApp,
    *,
    method: str = "GET",
    path: str = "/v1/health",
    query: bytes = b"",
    headers: list[tuple[bytes, bytes]] | None = None,
    scope_type: str = "http",
) -> tuple[int, dict[str, str], bytes]:
    messages: list[Mapping[str, Any]] = []

    async def receive() -> Mapping[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Mapping[str, Any]) -> None:
        messages.append(message)

    await app(
        {
            "type": scope_type,
            "method": method,
            "path": path,
            "query_string": query,
            "headers": headers or [],
        },
        receive,
        send,
    )
    start, body = messages
    response_headers = {
        key.decode("ascii"): value.decode("latin-1") for key, value in start["headers"]
    }
    return int(start["status"]), response_headers, body["body"]


def application() -> ReadOnlyAsgiApp:
    return ReadOnlyAsgiApp(
        HostedApi(StaticVerifier(None), RecordingReader()), HostedHttpConfig(ORIGIN)
    )


@pytest.mark.asyncio
async def test_public_health_is_translated_with_security_headers() -> None:
    status, headers, body = await invoke(application())
    assert status == 200
    assert json.loads(body)["authority"] == "read_only_research"
    assert headers["cache-control"] == "no-store"
    assert headers["x-content-type-options"] == "nosniff"
    assert "access-control-allow-origin" not in headers


@pytest.mark.asyncio
async def test_exact_allowed_origin_receives_narrow_cors() -> None:
    status, headers, _ = await invoke(application(), headers=[(b"origin", ORIGIN.encode())])
    assert status == 200
    assert headers["access-control-allow-origin"] == ORIGIN
    assert headers["vary"] == "Origin"

    status, headers, body = await invoke(
        application(), method="OPTIONS", headers=[(b"origin", ORIGIN.encode())]
    )
    assert status == 204 and body == b""
    assert headers["access-control-allow-methods"] == "GET, OPTIONS"
    assert "POST" not in headers["access-control-allow-methods"]


@pytest.mark.asyncio
async def test_foreign_and_missing_preflight_origins_fail_closed() -> None:
    assert (
        await invoke(application(), headers=[(b"origin", b"https://attacker.example")])
    )[0] == 403
    assert (await invoke(application(), method="OPTIONS"))[0] == 403


@pytest.mark.asyncio
async def test_duplicate_authorization_and_malformed_requests_are_rejected() -> None:
    duplicate = [(b"authorization", b"Bearer one"), (b"authorization", b"Bearer two")]
    assert (await invoke(application(), headers=duplicate))[0] == 400
    assert (await invoke(application(), query=b"limit"))[0] == 400
    assert (await invoke(application(), query=b"x=" + b"1" * 3000))[0] == 400
    assert (await invoke(application(), path="not-absolute"))[0] == 400


@pytest.mark.asyncio
async def test_non_http_scope_and_mutations_have_no_capability() -> None:
    assert (await invoke(application(), scope_type="websocket"))[0] == 404
    assert (await invoke(application(), method="POST"))[0] == 405


@pytest.mark.parametrize(
    "origin",
    [
        "http://trade.revmind.example",
        "https://trade.revmind.example/path",
        "https://user:password@trade.revmind.example",
        "javascript:alert(1)",
    ],
)
def test_unsafe_allowed_origins_are_rejected(origin: str) -> None:
    with pytest.raises(HostedHttpConfigurationError):
        HostedHttpConfig(origin)
