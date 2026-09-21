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
        if request.url.path == "/api/get-open-trades.json":
            return httpx.Response(200, json={"error": False, "openTrades": []})
        if request.url.path == "/api/get-open-orders.json":
            return httpx.Response(
                200,
                json={
                    "error": False,
                    "openOrders": [
                        {
                            "id": "o2",
                            "symbol": "EURUSD",
                            "action": "Sell Limit",
                            "sizing": {"value": "0.20"},
                            "openPrice": "1.200000001",
                            "openTime": "09/20/2026 14:00",
                        },
                        {
                            "id": "o1",
                            "symbol": "XAUUSD",
                            "action": "Buy Stop",
                            "sizing": {"value": "0.10"},
                            "openPrice": "2700.01",
                            "openTime": "09/20/2026 13:00",
                        },
                    ],
                },
            )
        if request.url.path == "/api/get-history.json":
            return httpx.Response(
                200,
                json={
                    "error": False,
                    "history": [
                        {
                            "id": "h1",
                            "action": "Deposit",
                            "closeTime": "09/20/2026 15:00",
                            "profit": "250.123456789",
                        }
                    ],
                },
            )
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
        broker_timezone="UTC",
        client=client(httpx.MockTransport(respond)),
    )
    result = await adapter.sync_account("7")
    assert seen == [
        "/api/login.json",
        "/api/get-my-accounts.json",
        "/api/get-open-trades.json",
        "/api/get-open-orders.json",
        "/api/get-history.json",
    ]
    assert result.account.balance.as_tuple().exponent == -2
    assert result.account.observed_at == NOW
    assert result.history_scope == "RECENT_INCOMPLETE"
    assert result.positions == result.performance == ()
    assert [item.provider_record_id for item in result.orders] == ["o1", "o2"]
    assert result.orders[0].side == "BUY" and result.orders[0].instrument is None
    assert result.orders[1].declared_price.as_tuple().exponent == -9
    assert result.transactions[0].provider_symbol is None
    assert result.transactions[0].profit_loss.as_tuple().exponent == -9
    assert clock.calls == 1
    assert "secret-session" not in result.model_dump_json()

    again = await adapter.sync_account("7")
    assert again.account.provider_account_id == "7"
    assert seen.count("/api/login.json") == 1


def test_rejects_unsafe_client_and_redacts_secret_repr() -> None:
    unsafe = httpx.AsyncClient(base_url="https://example.com")
    with pytest.raises(ValueError, match="security-safe"):
        MyfxbookAdapter(
            SecretStr("email"), SecretStr("password"), Clock(), broker_timezone="UTC", client=unsafe
        )
    assert "password" not in repr(SecretStr("password"))


@pytest.mark.asyncio
async def test_provider_error_is_redacted_and_clock_not_called() -> None:
    clock = Clock()
    adapter = MyfxbookAdapter(
        SecretStr("email-secret"),
        SecretStr("password-secret"),
        clock,
        broker_timezone="UTC",
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
            broker_timezone="UTC",
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
        if request.url.path == "/api/get-open-trades.json":
            return httpx.Response(200, json={"error": False, "openTrades": []})
        if request.url.path == "/api/get-open-orders.json":
            return httpx.Response(200, json={"error": False, "openOrders": []})
        if request.url.path == "/api/get-history.json":
            return httpx.Response(200, json={"error": False, "history": []})
        return httpx.Response(200, json={"error": False, "accounts": accounts})

    adapter = MyfxbookAdapter(
        SecretStr("e"),
        SecretStr("p"),
        Clock(),
        broker_timezone="UTC",
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


def test_broker_timezone_is_explicit_and_dst_ambiguity_fails_closed() -> None:
    transport = client(httpx.MockTransport(lambda _request: httpx.Response(200, json={})))
    with pytest.raises(ValueError, match="IANA"):
        MyfxbookAdapter(
            SecretStr("e"), SecretStr("p"), Clock(), broker_timezone="Not/AZone", client=transport
        )
    adapter = MyfxbookAdapter(
        SecretStr("e"), SecretStr("p"), Clock(), broker_timezone="Europe/Madrid", client=transport
    )
    assert adapter._parse_local_time("09/20/2026 12:00") == datetime(2026, 9, 20, 10, tzinfo=UTC)
    with pytest.raises(MyfxbookError, match="ambiguous"):
        adapter._parse_local_time("10/25/2026 02:30")
    with pytest.raises(MyfxbookError, match="nonexistent"):
        adapter._parse_local_time("03/29/2026 02:30")


@pytest.mark.asyncio
async def test_open_positions_are_unmapped_exact_and_deterministically_ordered() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/login.json":
            return httpx.Response(200, json={"error": False, "session": "session"})
        if request.url.path == "/api/get-my-accounts.json":
            return httpx.Response(
                200,
                json={
                    "error": False,
                    "accounts": [{"id": 7, "currency": "USD", "balance": "1000", "equity": "990"}],
                },
            )
        if request.url.path == "/api/get-open-orders.json":
            return httpx.Response(200, json={"error": False, "openOrders": []})
        if request.url.path == "/api/get-history.json":
            return httpx.Response(200, json={"error": False, "history": []})
        return httpx.Response(
            200,
            json={
                "error": False,
                "openTrades": [
                    {
                        "id": "b",
                        "symbol": "EURUSD",
                        "action": "sell",
                        "sizing": {"value": "0.20"},
                        "openPrice": "1.123456789",
                        "openTime": "09/20/2026 13:00",
                    },
                    {
                        "id": "a",
                        "symbol": "XAUUSD",
                        "action": "buy",
                        "sizing": {"value": "0.10"},
                        "openPrice": "2600.01",
                        "openTime": "09/20/2026 12:00",
                    },
                ],
            },
        )

    adapter = MyfxbookAdapter(
        SecretStr("e"),
        SecretStr("p"),
        Clock(),
        broker_timezone="Europe/Madrid",
        client=client(httpx.MockTransport(respond)),
    )
    result = await adapter.sync_account("7")
    assert [item.provider_record_id for item in result.positions] == ["a", "b"]
    assert result.positions[0].instrument is None
    assert result.positions[1].open_price.as_tuple().exponent == -9
    assert all(item.observed_at == result.account.observed_at for item in result.positions)
