"""Bounded read-only Myfxbook account adapter; no execution capability."""

import json
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from pydantic import SecretStr

from app.accounts.models import (
    AccountFactBatch,
    ExternalOpenOrderFact,
    ExternalOpenPositionFact,
    ExternalPerformanceObservation,
    ExternalTransactionFact,
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

    async def list_accounts(self) -> tuple[TradingAccountSnapshot, ...]:
        """Return the bounded account choices visible to the authenticated user."""
        session = await self._ensure_session()
        payload = await self._get("/api/get-my-accounts.json", {"session": session})
        raw_accounts = payload.get("accounts")
        if not isinstance(raw_accounts, list):
            raise MyfxbookError("Myfxbook returned malformed account data")
        if len(raw_accounts) > 100:
            raise MyfxbookError("Myfxbook returned too many accounts")
        parsed: list[tuple[str, str | None, str, Decimal, Decimal, Decimal | None]] = []
        seen_ids: set[str] = set()
        try:
            for raw in raw_accounts:
                if not isinstance(raw, Mapping) or raw.get("id") is None:
                    raise ValueError
                account_id = str(raw["id"]).strip()
                currency = str(raw["currency"]).strip()
                if not account_id or account_id in seen_ids or not currency:
                    raise ValueError
                seen_ids.add(account_id)
                name_value = raw.get("name")
                account_name = str(name_value).strip() if name_value is not None else None
                account_name = account_name or None
                balance = Decimal(str(raw["balance"]))
                equity = Decimal(str(raw["equity"]))
                margin = (
                    Decimal(str(raw["margin"])) if raw.get("margin") is not None else None
                )
                if (
                    not balance.is_finite()
                    or not equity.is_finite()
                    or (margin is not None and (not margin.is_finite() or margin < 0))
                ):
                    raise ValueError
                parsed.append((account_id, account_name, currency, balance, equity, margin))
        except (KeyError, ValueError, TypeError) as exc:
            raise MyfxbookError("Myfxbook returned invalid account data") from exc
        observed_at = self._utc_now()
        try:
            accounts = [
                TradingAccountSnapshot(
                    provider="myfxbook",
                    provider_account_id=account_id,
                    account_name=account_name,
                    currency=currency,
                    balance=balance,
                    equity=equity,
                    margin=margin,
                    free_margin=None,
                    observed_at=observed_at,
                )
                for account_id, account_name, currency, balance, equity, margin in parsed
            ]
        except ValueError as exc:
            raise MyfxbookError("Myfxbook returned invalid account data") from exc
        accounts.sort(key=lambda item: (item.provider_account_id, item.account_name or ""))
        return tuple(accounts)

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
        history_payload = await self._get(
            "/api/get-history.json", {"session": session, "id": provider_account_id}
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
        raw_history = history_payload.get("history")
        if not isinstance(raw_history, list) or len(raw_history) > 50:
            raise MyfxbookError("Myfxbook returned invalid recent history")
        transactions: list[ExternalTransactionFact] = []
        seen_history: set[str] = set()
        try:
            for raw in raw_history:
                if not isinstance(raw, Mapping):
                    raise ValueError
                record_id = str(raw["id"]).strip()
                transaction_type = str(raw["action"]).strip()
                if not record_id or record_id in seen_history or not transaction_type:
                    raise ValueError
                seen_history.add(record_id)
                sizing = raw.get("sizing")
                quantity = None
                if sizing is not None:
                    if not isinstance(sizing, Mapping):
                        raise ValueError
                    quantity = Decimal(str(sizing["value"]))
                symbol_value = raw.get("symbol")
                symbol = str(symbol_value).strip() if symbol_value is not None else None
                transactions.append(
                    ExternalTransactionFact(
                        provider="myfxbook",
                        provider_account_id=provider_account_id,
                        provider_record_id=record_id,
                        provider_symbol=symbol or None,
                        instrument=None,
                        transaction_type=transaction_type,
                        quantity=quantity,
                        price=Decimal(str(raw["closePrice"]))
                        if raw.get("closePrice") is not None
                        else None,
                        profit_loss=Decimal(str(raw["profit"]))
                        if raw.get("profit") is not None
                        else None,
                        event_at=self._parse_local_time(str(raw["closeTime"])),
                        observed_at=observed_at,
                    )
                )
        except (KeyError, ValueError, TypeError) as exc:
            raise MyfxbookError("Myfxbook returned invalid recent history") from exc
        transactions.sort(key=lambda item: (item.event_at, item.provider_record_id))
        return AccountFactBatch(
            account=account,
            positions=tuple(positions),
            orders=tuple(orders),
            transactions=tuple(transactions),
            history_scope="RECENT_INCOMPLETE",
        )

    async def daily_performance(
        self, provider_account_id: str, start: date, end: date
    ) -> tuple[ExternalPerformanceObservation, ...]:
        """Return provider-reported daily gain for one explicit inclusive date range."""
        if not provider_account_id.strip():
            raise ValueError("provider account id must be non-empty")
        if start > end:
            raise ValueError("performance start date cannot be after end date")
        if (end - start).days >= 366:
            raise ValueError("performance range cannot exceed 366 inclusive dates")
        session = await self._ensure_session()
        payload = await self._get(
            "/api/get-daily-gain.json",
            {
                "session": session,
                "id": provider_account_id,
                "start": start.isoformat(),
                "end": end.isoformat(),
            },
        )
        raw_collection = payload.get("dailyGain")
        if not isinstance(raw_collection, list):
            raise MyfxbookError("Myfxbook returned malformed daily performance data")
        raw_records: list[object]
        if len(raw_collection) == 1 and isinstance(raw_collection[0], list):
            raw_records = raw_collection[0]
        elif any(isinstance(item, list) for item in raw_collection):
            raise MyfxbookError("Myfxbook returned malformed daily performance data")
        else:
            raw_records = raw_collection
        if len(raw_records) > 366:
            raise MyfxbookError("Myfxbook returned too much daily performance data")
        parsed: list[tuple[date, Decimal]] = []
        seen_dates: set[date] = set()
        try:
            for raw in raw_records:
                if not isinstance(raw, Mapping):
                    raise ValueError
                effective_date = datetime.strptime(str(raw["date"]), "%m/%d/%Y").date()
                if effective_date in seen_dates or not start <= effective_date <= end:
                    raise ValueError
                seen_dates.add(effective_date)
                gain_percent = Decimal(str(raw["value"]))
                if not gain_percent.is_finite():
                    raise ValueError
                parsed.append((effective_date, gain_percent))
        except (KeyError, ValueError, TypeError) as exc:
            raise MyfxbookError("Myfxbook returned invalid daily performance data") from exc
        observed_at = self._utc_now()
        try:
            observations = [
                ExternalPerformanceObservation(
                    provider="myfxbook",
                    provider_account_id=provider_account_id,
                    effective_date=effective_date,
                    gain_percent=gain_percent,
                    observed_at=observed_at,
                )
                for effective_date, gain_percent in parsed
            ]
        except ValueError as exc:
            raise MyfxbookError("Myfxbook returned invalid daily performance data") from exc
        observations.sort(key=lambda item: item.effective_date)
        return tuple(observations)

    async def aclose(self) -> None:
        await self.disconnect()

    async def disconnect(self) -> None:
        """Invalidate a created provider session and make this adapter terminally closed."""
        if self._closed:
            return
        session = self._session.get_secret_value() if self._session is not None else None
        self._session = None
        failure: MyfxbookError | None = None
        try:
            if session is not None:
                await self._get("/api/logout.json", {"session": session})
        except MyfxbookError as exc:
            failure = exc
        finally:
            self._closed = True
            if self._owns_client:
                await self._client.aclose()
        if failure is not None:
            raise MyfxbookError("Myfxbook disconnect failed") from failure

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
