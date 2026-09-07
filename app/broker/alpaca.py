"""Strict Alpaca Paper Trading adapter; live-trading hosts are not configurable."""

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import httpx
from pydantic import SecretStr, ValidationError

from app.broker.models import (
    PaperAccount,
    PaperOrderReceipt,
    PaperOrderRequest,
    PaperPositionSummary,
)

_BASE_URL = "https://paper-api.alpaca.markets"
_MAX_RESPONSE_BYTES = 1_000_000


class PaperBrokerError(Exception):
    """Redacted paper-broker failure."""


class AlpacaPaperBroker:
    def __init__(
        self,
        key_id: SecretStr,
        secret_key: SecretStr,
        *,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(key_id, SecretStr) or not isinstance(secret_key, SecretStr):
            raise ValueError("paper credentials must remain secret values")
        self._owns_client = client is None
        headers = {
            "APCA-API-KEY-ID": key_id.get_secret_value(),
            "APCA-API-SECRET-KEY": secret_key.get_secret_value(),
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        }
        if client is None:
            client = httpx.AsyncClient(
                base_url=_BASE_URL,
                headers=headers,
                timeout=httpx.Timeout(connect=5, read=15, write=5, pool=5),
                follow_redirects=False,
            )
        elif str(client.base_url).rstrip("/") != _BASE_URL or client.follow_redirects:
            raise ValueError("injected paper client is not security-safe")
        self._client = client
        self._headers = headers
        self._clock = clock or (lambda: datetime.now(UTC))

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def account(self) -> PaperAccount:
        account = await self._request("GET", "/v2/account")
        positions_value = await self._request("GET", "/v2/positions")
        if not isinstance(account, Mapping) or not isinstance(positions_value, list):
            raise PaperBrokerError("Alpaca returned malformed paper-account data")
        if not all(isinstance(item, Mapping) for item in positions_value):
            raise PaperBrokerError("Alpaca returned malformed paper-position data")
        try:
            currency = self._text(account, "currency")
            if currency != "USD":
                raise ValueError("unsupported paper-account currency")
            positions = tuple(
                sorted(
                    (
                        PaperPositionSummary(
                            symbol=self._text(item, "symbol"),
                            quantity=self._decimal(item, "qty"),
                            market_value=self._decimal(item, "market_value"),
                            current_price=self._decimal(item, "current_price"),
                        )
                        for item in positions_value
                    ),
                    key=lambda item: item.symbol,
                )
            )
            return PaperAccount(
                account_id=self._text(account, "id"),
                status=self._text(account, "status"),
                currency="USD",
                cash=self._decimal(account, "cash"),
                equity=self._decimal(account, "equity"),
                buying_power=self._decimal(account, "buying_power"),
                trading_blocked=self._boolean(account, "trading_blocked"),
                observed_at=self._now(),
                positions=positions,
            )
        except (ValidationError, ValueError, TypeError, InvalidOperation) as exc:
            raise PaperBrokerError("Alpaca returned invalid paper-account data") from exc

    async def place_bracket_order(self, request: PaperOrderRequest) -> PaperOrderReceipt:
        order = PaperOrderRequest.model_validate(request)
        body = {
            "client_order_id": order.client_order_id,
            "symbol": order.symbol,
            "side": order.side,
            "qty": str(order.quantity),
            "type": "limit",
            "limit_price": str(order.entry_limit),
            "time_in_force": "day",
            "order_class": "bracket",
            "take_profit": {"limit_price": str(order.target_price)},
            "stop_loss": {"stop_price": str(order.stop_price)},
            "extended_hours": False,
        }
        value = await self._request("POST", "/v2/orders", json_body=body)
        if not isinstance(value, Mapping):
            raise PaperBrokerError("Alpaca returned malformed paper-order data")
        try:
            side = self._text(value, "side")
            if side not in {"buy", "sell"}:
                raise ValueError("invalid order side")
            return PaperOrderReceipt(
                provider_order_id=self._text(value, "id"),
                client_order_id=self._text(value, "client_order_id"),
                symbol=self._text(value, "symbol"),
                side="buy" if side == "buy" else "sell",
                quantity=self._decimal(value, "qty"),
                status=self._text(value, "status"),
                submitted_at=datetime.fromisoformat(
                    self._text(value, "submitted_at").replace("Z", "+00:00")
                ),
            )
        except (ValidationError, ValueError, TypeError, InvalidOperation) as exc:
            raise PaperBrokerError("Alpaca returned invalid paper-order data") from exc

    async def _request(
        self, method: str, path: str, *, json_body: dict[str, object] | None = None
    ) -> object:
        try:
            async with self._client.stream(
                method, path, headers=self._headers, json=json_body
            ) as response:
                content = await response.aread()
                if len(content) > _MAX_RESPONSE_BYTES:
                    raise PaperBrokerError("Alpaca paper response exceeded the size limit")
                if not 200 <= response.status_code < 300:
                    raise PaperBrokerError(
                        f"Alpaca Paper Trading rejected the request (HTTP {response.status_code})."
                    )
                return json.loads(content)
        except PaperBrokerError:
            raise
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise PaperBrokerError("Alpaca Paper Trading is unavailable") from exc

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("paper receipt clock must be timezone-aware")
        return value.astimezone(UTC)

    @staticmethod
    def _text(value: Mapping[object, object], key: str) -> str:
        item = value.get(key)
        if not isinstance(item, str) or not item:
            raise ValueError(f"missing {key}")
        return item

    @classmethod
    def _decimal(cls, value: Mapping[object, object], key: str) -> Decimal:
        return Decimal(cls._text(value, key))

    @staticmethod
    def _boolean(value: Mapping[object, object], key: str) -> bool:
        item = value.get(key)
        if type(item) is not bool:
            raise ValueError(f"missing {key}")
        return item
