"""Bounded read-only Myfxbook account adapter; no execution capability."""

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from pydantic import SecretStr

from app.accounts.models import (
    AccountFactBatch,
    ExternalOpenOrderFact,
    ExternalOpenPositionFact,
    TradingAccountSnapshot,
)

_ORIGIN = "https://www.myfxbook.com"
_MAX_RESPONSE_BYTES = 1_000_000


class AccountClock(Protocol):
    def now(self) -> datetime: ...


class MyfxbookError(Exception):
    """Redacted provider failure safe for application boundaries."""


class MyfxbookAuthenticationError(MyfxbookError):
    """Credentials or session were rejected."""


class MyfxbookAdapter:
    """Read selected account state from Myfxbook's fixed official API origin."""

    def __init__(
        self,
        email: SecretStr,
        password: SecretStr,
        clock: AccountClock,
        *,
        broker_timezone: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not email.get_secret_value() or not password.get_secret_value():
            raise ValueError("Myfxbook credentials must be non-empty secret values")
        self._email = email
        self._password = password
        self._clock = clock
        try:
            self._broker_timezone = ZoneInfo(broker_timezone)
        except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
            raise ValueError("invalid IANA broker timezone") from exc
        self._session: SecretStr | None = None
        self._closed = False
        self._owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(
                base_url=_ORIGIN,
                follow_redirects=False,
                timeout=httpx.Timeout(connect=5, read=15, write=5, pool=5),
                headers={"Accept-Encoding": "identity"},
            )
        elif (
            str(client.base_url).rstrip("/") != _ORIGIN
            or client.follow_redirects
            or client.is_closed
        ):
            raise ValueError("injected Myfxbook client is not security-safe")
        self._client = client

    def _parse_local_time(self, value: str) -> datetime:
        """Convert an unambiguous broker-local wall time without machine-timezone inference."""
        try:
            naive = datetime.strptime(value, "%m/%d/%Y %H:%M")
        except ValueError as exc:
            raise MyfxbookError("Myfxbook returned invalid local time") from exc
        candidates: list[datetime] = []
        for fold in (0, 1):
            aware = naive.replace(tzinfo=self._broker_timezone, fold=fold)
            roundtrip = aware.astimezone(UTC).astimezone(self._broker_timezone)
            if roundtrip.replace(tzinfo=None) == naive:
                candidates.append(aware)
        offsets = {candidate.utcoffset() for candidate in candidates}
        if not candidates or len(offsets) != 1:
            raise MyfxbookError("Myfxbook local time is ambiguous or nonexistent")
        return candidates[0].astimezone(UTC)

    async def sync_account(self, provider_account_id: str) -> AccountFactBatch:
        """Return one atomic account snapshot; trading endpoints do not exist."""
        if not provider_account_id.strip():
            raise ValueError("provider account id must be non-empty")
        session = await self._ensure_session()
        payload = await self._get("/api/get-my-accounts.json", {"session": session})
        trade_payload = await self._get(
            "/api/get-open-trades.json", {"session": session, "id": provider_account_id}
        )
        order_payload = await self._get(
            "/api/get-open-orders.json", {"session": session, "id": provider_account_id}
        )
        accounts = payload.get("accounts")
        if not isinstance(accounts, list):
            raise MyfxbookError("Myfxbook returned malformed account data")
        matches = [
            item
            for item in accounts
            if isinstance(item, Mapping) and str(item.get("id")) == provider_account_id
        ]
        if len(matches) != 1:
            raise MyfxbookError("selected Myfxbook account is unavailable")
        item = matches[0]
        observed_at = self._utc_now()
        try:
            account = TradingAccountSnapshot(
                provider="myfxbook",
                provider_account_id=provider_account_id,
                account_name=str(item["name"]) if item.get("name") else None,
                currency=str(item["currency"]),
                balance=Decimal(str(item["balance"])),
                equity=Decimal(str(item["equity"])),
                margin=Decimal(str(item["margin"])) if item.get("margin") is not None else None,
                free_margin=None,
                observed_at=observed_at,
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise MyfxbookError("Myfxbook returned invalid account data") from exc
        raw_trades = trade_payload.get("openTrades")
        if not isinstance(raw_trades, list):
            raise MyfxbookError("Myfxbook returned malformed open-trade data")
        positions: list[ExternalOpenPositionFact] = []
        seen: set[str] = set()
        try:
            for raw in raw_trades:
                if not isinstance(raw, Mapping):
                    raise ValueError
                record_id = str(raw["id"]).strip()
                if not record_id or record_id in seen:
                    raise ValueError
                seen.add(record_id)
                action = str(raw["action"]).lower()
                if action not in {"buy", "sell"}:
                    raise ValueError
                sizing = raw["sizing"]
                if not isinstance(sizing, Mapping):
                    raise ValueError
                positions.append(
                    ExternalOpenPositionFact(
                        provider="myfxbook",
                        provider_account_id=provider_account_id,
                        provider_record_id=record_id,
                        provider_symbol=str(raw["symbol"]),
                        instrument=None,
                        side="LONG" if action == "buy" else "SHORT",
                        quantity=Decimal(str(sizing["value"])),
                        open_price=Decimal(str(raw["openPrice"])),
                        opened_at=self._parse_local_time(str(raw["openTime"])),
                        observed_at=observed_at,
                    )
                )
        except (KeyError, ValueError, TypeError) as exc:
            raise MyfxbookError("Myfxbook returned invalid open-trade data") from exc
        positions.sort(key=lambda item: (item.opened_at, item.provider_record_id))
        raw_orders = order_payload.get("openOrders")
        if not isinstance(raw_orders, list):
            raise MyfxbookError("Myfxbook returned malformed open-order data")
        orders: list[ExternalOpenOrderFact] = []
        seen_orders: set[str] = set()
        try:
            for raw in raw_orders:
                if not isinstance(raw, Mapping):
                    raise ValueError
                record_id = str(raw["id"]).strip()
                if not record_id or record_id in seen_orders:
                    raise ValueError
                seen_orders.add(record_id)
                action = str(raw["action"]).strip()
                side: Literal["BUY", "SELL"] | None = (
                    "BUY"
                    if action.lower().startswith("buy")
                    else "SELL"
                    if action.lower().startswith("sell")
                    else None
                )
                sizing = raw["sizing"]
                if side is None or not isinstance(sizing, Mapping):
                    raise ValueError
                orders.append(
                    ExternalOpenOrderFact(
                        provider="myfxbook",
                        provider_account_id=provider_account_id,
                        provider_record_id=record_id,
                        provider_symbol=str(raw["symbol"]),
                        instrument=None,
                        side=side,
                        order_type=action,
                        quantity=Decimal(str(sizing["value"])),
                        declared_price=Decimal(str(raw["openPrice"])),
                        created_at=self._parse_local_time(str(raw["openTime"])),
                        observed_at=observed_at,
                    )
                )
        except (KeyError, ValueError, TypeError) as exc:
            raise MyfxbookError("Myfxbook returned invalid open-order data") from exc
        orders.sort(key=lambda item: (item.created_at, item.provider_record_id))
        return AccountFactBatch(account=account, positions=tuple(positions), orders=tuple(orders))

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._session = None
        if self._owns_client:
            await self._client.aclose()

    async def _ensure_session(self) -> str:
        self._require_open()
        if self._session is None:
            payload = await self._get(
                "/api/login.json",
                {
                    "email": self._email.get_secret_value(),
                    "password": self._password.get_secret_value(),
                },
            )
            session = payload.get("session")
            if not isinstance(session, str) or not session:
                raise MyfxbookAuthenticationError("Myfxbook authentication failed")
            self._session = SecretStr(session)
        return self._session.get_secret_value()

    async def _get(self, path: str, params: dict[str, str]) -> Mapping[str, object]:
        self._require_open()
        try:
            async with self._client.stream("GET", path, params=params) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    raise MyfxbookError("Myfxbook redirect rejected")
                if response.status_code in {401, 403}:
                    raise MyfxbookAuthenticationError("Myfxbook authentication failed")
                if response.status_code != 200:
                    raise MyfxbookError("Myfxbook is unavailable")
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > _MAX_RESPONSE_BYTES:
                        raise MyfxbookError("Myfxbook response exceeded size limit")
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise MyfxbookError("Myfxbook is unavailable") from exc
        try:
            value = json.loads(content, parse_float=Decimal)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise MyfxbookError("Myfxbook returned malformed data") from exc
        if not isinstance(value, Mapping):
            raise MyfxbookError("Myfxbook returned malformed data")
        if value.get("error") is True:
            raise MyfxbookAuthenticationError("Myfxbook rejected the request")
        return value

    def _utc_now(self) -> datetime:
        value = self._clock.now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise MyfxbookError("account clock must be timezone-aware")
        return value.astimezone(UTC)

    def _require_open(self) -> None:
        if self._closed or self._client.is_closed:
            raise MyfxbookError("Myfxbook adapter is closed")
