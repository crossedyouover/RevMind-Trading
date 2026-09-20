from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr

from app.accounts.myfxbook import MyfxbookAdapter, MyfxbookAuthenticationError, MyfxbookError

NOW = datetime(2026, 9, 20, 21, tzinfo=UTC)


class Clock:
    calls = 0

    def now(self) -> datetime:
        self.calls += 1
        return NOW


def client(handler: httpx.AsyncBaseTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url="https://www.myfxbook.com", transport=handler)


@pytest.mark.asyncio
async def test_sync_maps_account_and_observes_only_after_validation() -> None:
    seen: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/api/login.json":
            return httpx.Response(200, json={"error": False, "session": "secret-session"})
        return httpx.Response(
            200,
            json={
                "error": False,
                "accounts": [
                    {
                        "id": 7,
                        "name": "Paper FX",
                        "currency": "USD",
                        "balance": 1000.25,
                        "equity": 990.5,
                        "margin": 10,
                    }
                ],
            },
        )

    clock = Clock()
    adapter = MyfxbookAdapter(
        SecretStr("mail@example.test"),
        SecretStr("password"),
        clock,
        client=client(httpx.MockTransport(respond)),
    )
    result = await adapter.sync_account("7")
    assert seen == ["/api/login.json", "/api/get-my-accounts.json"]
    assert result.account.balance.as_tuple().exponent == -2
    assert result.account.observed_at == NOW
    assert result.history_scope == "NOT_REQUESTED"
    assert result.positions == result.orders == result.transactions == result.performance == ()
    assert clock.calls == 1
    assert "secret-session" not in result.model_dump_json()

    again = await adapter.sync_account("7")
    assert again.account.provider_account_id == "7"
    assert seen.count("/api/login.json") == 1


def test_rejects_unsafe_client_and_redacts_secret_repr() -> None:
    unsafe = httpx.AsyncClient(base_url="https://example.com")
    with pytest.raises(ValueError, match="security-safe"):
        MyfxbookAdapter(SecretStr("email"), SecretStr("password"), Clock(), client=unsafe)
    assert "password" not in repr(SecretStr("password"))


@pytest.mark.asyncio
async def test_provider_error_is_redacted_and_clock_not_called() -> None:
    clock = Clock()
    adapter = MyfxbookAdapter(
        SecretStr("email-secret"),
        SecretStr("password-secret"),
        clock,
        client=client(
            httpx.MockTransport(
                lambda _request: httpx.Response(
                    200, json={"error": True, "message": "email-secret password-secret"}
                )
            )
        ),
    )
    with pytest.raises(MyfxbookAuthenticationError) as caught:
        await adapter.sync_account("7")
    assert "email-secret" not in str(caught.value)
    assert "password-secret" not in str(caught.value)
    assert clock.calls == 0


@pytest.mark.asyncio
async def test_malformed_and_oversized_responses_fail_closed() -> None:
    for response in (
        httpx.Response(200, content=b"not-json"),
        httpx.Response(200, content=b"{" + b"x" * 1_000_001),
    ):
        adapter = MyfxbookAdapter(
            SecretStr("e"),
            SecretStr("p"),
            Clock(),
            client=client(httpx.MockTransport(lambda _request, value=response: value)),
        )
        with pytest.raises(MyfxbookError):
            await adapter.sync_account("7")


@pytest.mark.asyncio
@pytest.mark.parametrize("accounts", [[], [{"id": 7}, {"id": "7"}]])
async def test_selected_account_must_exist_exactly_once(accounts: list[dict[str, object]]) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/login.json":
            return httpx.Response(200, json={"error": False, "session": "session"})
        return httpx.Response(200, json={"error": False, "accounts": accounts})

    adapter = MyfxbookAdapter(
        SecretStr("e"),
        SecretStr("p"),
        Clock(),
        client=client(httpx.MockTransport(respond)),
    )
    with pytest.raises(MyfxbookError, match="unavailable"):
        await adapter.sync_account("7")


def test_adapter_has_no_execution_methods_or_broker_import() -> None:
    names = set(dir(MyfxbookAdapter))
    assert not names & {"place_order", "cancel_order", "modify_order", "execute"}
    import inspect

    import app.accounts.myfxbook as module

    assert "app.broker" not in inspect.getsource(module)
