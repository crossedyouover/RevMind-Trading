"""Alpaca paper adapter is bounded to the paper host and redacts failures."""

import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from pydantic import SecretStr

from app.broker.alpaca import AlpacaPaperBroker, PaperBrokerError
from app.broker.models import PaperOrderRequest

NOW = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


def order() -> PaperOrderRequest:
    return PaperOrderRequest(
        client_order_id="revmind-00000000-0000-4000-8000-000000000001",
        symbol="AAPL",
        side="buy",
        quantity=Decimal("2"),
        entry_limit=Decimal("100"),
        stop_price=Decimal("98"),
        target_price=Decimal("104"),
    )


@pytest.mark.asyncio
async def test_account_and_bracket_order_use_only_paper_host() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v2/account":
            return httpx.Response(
                200,
                json={
                    "id": "paper-account",
                    "status": "ACTIVE",
                    "currency": "USD",
                    "cash": "10000.00",
                    "equity": "10250.00",
                    "buying_power": "20000.00",
                    "trading_blocked": False,
                },
            )
        if request.url.path == "/v2/positions":
            return httpx.Response(
                200,
                json=[
                    {
                        "symbol": "MSFT",
                        "qty": "1",
                        "market_value": "250",
                        "current_price": "250",
                    }
                ],
            )
        return httpx.Response(
            200,
            json={
                "id": "provider-order",
                "client_order_id": order().client_order_id,
                "symbol": "AAPL",
                "side": "buy",
                "qty": "2",
                "status": "accepted",
                "submitted_at": "2026-09-07T12:00:00Z",
            },
        )

    client = httpx.AsyncClient(
        base_url="https://paper-api.alpaca.markets",
        transport=httpx.MockTransport(respond),
        follow_redirects=False,
    )
    broker = AlpacaPaperBroker(
        SecretStr("paper-key"), SecretStr("paper-secret"), client=client, clock=lambda: NOW
    )
    account = await broker.account()
    receipt = await broker.place_bracket_order(order())
    assert account.cash == Decimal("10000.00")
    assert account.positions[0].symbol == "MSFT"
    assert receipt.status == "accepted"
    assert all(request.url.host == "paper-api.alpaca.markets" for request in requests)
    assert all(request.headers["APCA-API-KEY-ID"] == "paper-key" for request in requests)
    body = json.loads(requests[-1].content)
    assert body == {
        "client_order_id": order().client_order_id,
        "symbol": "AAPL",
        "side": "buy",
        "qty": "2",
        "type": "limit",
        "limit_price": "100",
        "time_in_force": "day",
        "order_class": "bracket",
        "take_profit": {"limit_price": "104"},
        "stop_loss": {"stop_price": "98"},
        "extended_hours": False,
    }
    await client.aclose()


def test_live_host_injection_is_rejected() -> None:
    client = httpx.AsyncClient(base_url="https://api.alpaca.markets")
    with pytest.raises(ValueError, match="security-safe"):
        AlpacaPaperBroker(SecretStr("key"), SecretStr("secret"), client=client)


@pytest.mark.asyncio
async def test_provider_failure_is_redacted() -> None:
    client = httpx.AsyncClient(
        base_url="https://paper-api.alpaca.markets",
        transport=httpx.MockTransport(lambda _request: httpx.Response(403, text="paper-secret")),
    )
    broker = AlpacaPaperBroker(SecretStr("paper-key"), SecretStr("paper-secret"), client=client)
    with pytest.raises(PaperBrokerError) as captured:
        await broker.account()
    assert "paper-secret" not in str(captured.value)
    await client.aclose()
