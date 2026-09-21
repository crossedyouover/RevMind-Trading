from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr

from app.accounts.myfxbook import MyfxbookAdapter, MyfxbookAuthenticationError, MyfxbookError
from app.accounts.protocol import ReadOnlyTradingAccountProvider

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
async def test_daily_performance_is_explicit_exact_and_deterministic() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/login.json":
            return httpx.Response(200, json={"error": False, "session": "session"})
        return httpx.Response(
            200,
            json={
                "error": False,
                "dailyGain": [[
                    {"date": "09/20/2026", "value": "0.123456789", "profit": "999"},
                    {"date": "09/19/2026", "value": "-0.2", "profit": "-1"},
                ]],
            },
        )

    clock = Clock()
    adapter = MyfxbookAdapter(
        SecretStr("e"),
        SecretStr("p"),
        clock,
        broker_timezone="UTC",
        client=client(httpx.MockTransport(respond)),
    )
    result = await adapter.daily_performance("7", date(2026, 9, 19), date(2026, 9, 20))
    assert [item.effective_date for item in result] == [date(2026, 9, 19), date(2026, 9, 20)]
    assert result[1].gain_percent is not None
    assert result[1].gain_percent.as_tuple().exponent == -9
    assert result[0].balance is result[0].equity is None
    assert all(item.observed_at == NOW for item in result)
    query = requests[-1].url.params
    assert query["id"] == "7"
    assert query["start"] == "2026-09-19"
    assert query["end"] == "2026-09-20"
    assert clock.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "daily_gain",
    [
        None,
        [[[]]],
        [[], []],
        [[{"date": "09/19/2026"}]],
        [[{"date": "09/18/2026", "value": "1"}]],
        [[{"date": "09/19/2026", "value": "NaN"}]],
        [[
            {"date": "09/19/2026", "value": "1"},
            {"date": "09/19/2026", "value": "2"},
        ]],
    ],
)
async def test_daily_performance_rejects_malformed_data_without_observing(
    daily_gain: object,
) -> None:
    clock = Clock()

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/login.json":
            return httpx.Response(200, json={"error": False, "session": "session"})
        return httpx.Response(200, json={"error": False, "dailyGain": daily_gain})

    adapter = MyfxbookAdapter(
        SecretStr("e"),
        SecretStr("p"),
        clock,
        broker_timezone="UTC",
        client=client(httpx.MockTransport(respond)),
    )
    with pytest.raises(MyfxbookError, match="daily performance"):
        await adapter.daily_performance("7", date(2026, 9, 19), date(2026, 9, 20))
    assert clock.calls == 0


@pytest.mark.asyncio
async def test_daily_performance_empty_flat_response_and_future_date_rules() -> None:
    responses: list[object] = [[], [{"date": "09/21/2026", "value": "1"}]]

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/login.json":
            return httpx.Response(200, json={"error": False, "session": "session"})
        return httpx.Response(200, json={"error": False, "dailyGain": responses.pop(0)})

    adapter = MyfxbookAdapter(
        SecretStr("e"),
        SecretStr("p"),
        Clock(),
        broker_timezone="UTC",
        client=client(httpx.MockTransport(respond)),
    )
    assert await adapter.daily_performance("7", date(2026, 9, 20), date(2026, 9, 20)) == ()
    with pytest.raises(MyfxbookError, match="daily performance"):
        await adapter.daily_performance("7", date(2026, 9, 21), date(2026, 9, 21))


@pytest.mark.asyncio
async def test_daily_performance_range_and_response_bounds() -> None:
    adapter = MyfxbookAdapter(
        SecretStr("e"),
        SecretStr("p"),
        Clock(),
        broker_timezone="UTC",
        client=client(httpx.MockTransport(lambda _request: httpx.Response(200, json={}))),
    )
    with pytest.raises(ValueError, match="after"):
        await adapter.daily_performance("7", date(2026, 9, 2), date(2026, 9, 1))
    with pytest.raises(ValueError, match="366"):
        await adapter.daily_performance("7", date(2025, 9, 19), date(2026, 9, 20))
    with pytest.raises(ValueError, match="non-empty"):
        await adapter.daily_performance(" ", date(2026, 9, 20), date(2026, 9, 20))

    records = [
        {"date": (date(2025, 9, 20) + timedelta(days=index)).strftime("%m/%d/%Y"), "value": 0}
        for index in range(367)
    ]

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/login.json":
            return httpx.Response(200, json={"error": False, "session": "session"})
        return httpx.Response(200, json={"error": False, "dailyGain": records})

    bounded = MyfxbookAdapter(
        SecretStr("e"),
        SecretStr("p"),
        Clock(),
        broker_timezone="UTC",
        client=client(httpx.MockTransport(respond)),
    )
    with pytest.raises(MyfxbookError, match="too much"):
        await bounded.daily_performance("7", date(2025, 9, 21), date(2026, 9, 21))


def test_read_only_protocol_declares_explicit_daily_performance_range() -> None:
    import inspect

    parameters = inspect.signature(ReadOnlyTradingAccountProvider.daily_performance).parameters
    assert list(parameters) == ["self", "provider_account_id", "start", "end"]


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
