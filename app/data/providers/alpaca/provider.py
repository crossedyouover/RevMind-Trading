"""Read-only Alpaca implementation of RevMind's frozen market-data boundary."""

import json
import re
from collections.abc import Mapping
from datetime import UTC
from decimal import Decimal
from urllib.parse import quote

import httpx
from pydantic import ValidationError

from app.core.schemas import Instrument, MarketBar, MarketSnapshot, Timeframe
from app.data.market import (
    BarRequest,
    InstrumentNotFoundError,
    InvalidMarketDataRequestError,
    MarketDataProvider,
    MarketDataUnavailableError,
    ProviderRateLimitError,
)
from app.data.providers.alpaca.config import AlpacaMarketDataSettings
from app.data.providers.alpaca.mapping import AlpacaInstrumentBinding, build_binding_index
from app.data.providers.alpaca.wire import (
    AlpacaBarsResponseWire,
    AlpacaSnapshotWire,
    parse_alpaca_timestamp,
)

_BASE_URL = "https://data.alpaca.markets"
_PAGE_SIZE = 1_000
_MAX_PAGES = 100
_MAX_RECORDS = 100_000
_MAX_RESPONSE_BYTES = 5_000_000
_MAX_ATTEMPTS = 3
_RETRY_STATUSES = frozenset({502, 503, 504})
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_+=/-]{1,2048}$")
_TIMEFRAMES = {
    Timeframe.ONE_MINUTE: "1Min",
    Timeframe.FIVE_MINUTES: "5Min",
    Timeframe.FIFTEEN_MINUTES: "15Min",
    Timeframe.ONE_HOUR: "1Hour",
    Timeframe.ONE_DAY: "1Day",
}


class AlpacaMarketDataProvider(MarketDataProvider):
    """Strict HTTP adapter returning only canonical frozen RevMind models."""

    def __init__(
        self,
        settings: AlpacaMarketDataSettings,
        bindings: tuple[AlpacaInstrumentBinding, ...],
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not isinstance(settings, AlpacaMarketDataSettings):
            raise InvalidMarketDataRequestError("invalid Alpaca settings")
        try:
            self._settings = AlpacaMarketDataSettings.model_validate(
                settings.model_dump(mode="python", round_trip=True, warnings="none")
            )
        except (ValidationError, ValueError, TypeError, AttributeError) as exc:
            raise InvalidMarketDataRequestError("invalid Alpaca settings") from exc
        self._bindings = build_binding_index(bindings)
        self._closed = False
        self._owns_client = client is None
        if client is None:
            headers: dict[str, str] = {}
            if self._settings.available:
                assert self._settings.api_key_id is not None
                assert self._settings.api_secret_key is not None
                headers = {
                    "APCA-API-KEY-ID": self._settings.api_key_id.get_secret_value(),
                    "APCA-API-SECRET-KEY": self._settings.api_secret_key.get_secret_value(),
                }
            client = httpx.AsyncClient(
                base_url=_BASE_URL,
                headers=headers,
                timeout=httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0),
                follow_redirects=False,
            )
        elif (
            str(client.base_url).rstrip("/") != _BASE_URL
            or client.follow_redirects
            or client.is_closed
        ):
            raise InvalidMarketDataRequestError("injected Alpaca client is not security-safe")
        self._client = client

    async def __aenter__(self) -> "AlpacaMarketDataProvider":
        self._require_open()
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close an owned client; injected clients remain caller-owned."""
        if self._closed:
            return
        self._closed = True
        if self._owns_client:
            await self._client.aclose()

    async def _get_bars(self, request: BarRequest) -> list[MarketBar]:
        symbol = self._provider_symbol(request.instrument)
        timeframe = self._map_timeframe(request.timeframe)
        bars: list[MarketBar] = []
        page_token: str | None = None
        seen_tokens: set[str] = set()
        for _page in range(_MAX_PAGES):
            parameters: dict[str, str | int] = {
                "start": request.start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "end": request.end.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "timeframe": timeframe,
                "feed": self._settings.data_feed.value,
                "adjustment": "raw",
                "sort": "asc",
                "limit": _PAGE_SIZE,
            }
            if page_token is not None:
                parameters["page_token"] = page_token
            response = await self._request(
                f"/v2/stocks/{quote(symbol, safe='')}/bars", parameters
            )
            payload = self._decode_json(response)
            try:
                wire = AlpacaBarsResponseWire.model_validate(payload)
            except (ValidationError, ValueError, TypeError) as exc:
                raise MarketDataUnavailableError("malformed Alpaca bars response") from exc
            if wire.symbol != symbol:
                raise MarketDataUnavailableError("Alpaca returned an unexpected symbol")
            try:
                page_bars = [
                    MarketBar(
                        instrument=request.instrument,
                        timeframe=request.timeframe,
                        timestamp=parse_alpaca_timestamp(item.timestamp),
                        open=item.open,
                        high=item.high,
                        low=item.low,
                        close=item.close,
                        volume=item.volume,
                    )
                    for item in wire.bars
                ]
            except (ValidationError, ValueError, TypeError) as exc:
                raise MarketDataUnavailableError("invalid Alpaca bar data") from exc
            bars.extend(page_bars)
            if len(bars) > _MAX_RECORDS:
                raise MarketDataUnavailableError("Alpaca bar response exceeds record limit")
            page_token = wire.next_page_token
            if page_token is None:
                return bars
            if _TOKEN_PATTERN.fullmatch(page_token) is None:
                raise MarketDataUnavailableError("Alpaca returned a malformed page token")
            if page_token in seen_tokens:
                raise MarketDataUnavailableError("Alpaca repeated a pagination token")
            seen_tokens.add(page_token)
        raise MarketDataUnavailableError("Alpaca bar response exceeds page limit")

    async def get_snapshot(self, instrument: Instrument) -> MarketSnapshot:
        return (await self.get_batch_snapshots([instrument]))[0]

    async def get_batch_snapshots(
        self, instruments: list[Instrument]
    ) -> list[MarketSnapshot]:
        if not isinstance(instruments, list):
            raise InvalidMarketDataRequestError("snapshot instruments must be a list")
        if not instruments:
            self._require_open()
            return []
        pairs = [(instrument, self._provider_symbol(instrument)) for instrument in instruments]
        unique_symbols = tuple(dict.fromkeys(symbol for _instrument, symbol in pairs))
        response = await self._request(
            "/v2/stocks/snapshots",
            {"symbols": ",".join(unique_symbols), "feed": self._settings.data_feed.value},
        )
        payload = self._decode_json(response)
        if not isinstance(payload, Mapping) or not all(isinstance(key, str) for key in payload):
            raise MarketDataUnavailableError("malformed Alpaca snapshots response")
        if set(payload) != set(unique_symbols):
            if set(unique_symbols) - set(payload):
                raise InstrumentNotFoundError("Alpaca snapshot instrument not found")
            raise MarketDataUnavailableError("Alpaca returned unexpected snapshot symbols")
        snapshots: dict[str, MarketSnapshot] = {}
        for instrument, symbol in pairs:
            if symbol in snapshots:
                continue
            try:
                wire = AlpacaSnapshotWire.model_validate(payload[symbol])
                snapshots[symbol] = MarketSnapshot(
                    instrument=instrument,
                    timestamp=parse_alpaca_timestamp(wire.latest_trade.timestamp),
                    last_price=wire.latest_trade.price,
                    day_volume=None,
                    percent_change=None,
                )
            except (ValidationError, ValueError, TypeError) as exc:
                raise MarketDataUnavailableError("invalid Alpaca snapshot data") from exc
        return [snapshots[symbol] for _instrument, symbol in pairs]

    def _provider_symbol(self, instrument: Instrument) -> str:
        if not isinstance(instrument, Instrument):
            raise InvalidMarketDataRequestError("instrument must be canonical")
        try:
            return self._bindings[instrument]
        except KeyError as exc:
            raise InstrumentNotFoundError("instrument has no Alpaca binding") from exc

    @staticmethod
    def _map_timeframe(timeframe: Timeframe) -> str:
        try:
            return _TIMEFRAMES[timeframe]
        except (KeyError, TypeError) as exc:
            raise InvalidMarketDataRequestError("unsupported Alpaca timeframe") from exc

    async def _request(
        self, path: str, parameters: dict[str, str | int]
    ) -> httpx.Response:
        self._require_available()
        assert self._settings.api_key_id is not None
        assert self._settings.api_secret_key is not None
        headers = {
            "APCA-API-KEY-ID": self._settings.api_key_id.get_secret_value(),
            "APCA-API-SECRET-KEY": self._settings.api_secret_key.get_secret_value(),
        }
        for attempt in range(_MAX_ATTEMPTS):
            try:
                async with self._client.stream(
                    "GET", path, params=parameters, headers=headers
                ) as response:
                    if (
                        response.status_code in _RETRY_STATUSES
                        and attempt + 1 < _MAX_ATTEMPTS
                    ):
                        continue
                    self._raise_for_status(response.status_code)
                    content = await self._read_bounded_response(response)
                    return httpx.Response(
                        response.status_code,
                        headers=response.headers,
                        content=content,
                        request=response.request,
                    )
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                if attempt + 1 == _MAX_ATTEMPTS:
                    raise MarketDataUnavailableError("Alpaca request failed") from exc
                continue
            except httpx.TransportError as exc:
                raise MarketDataUnavailableError("Alpaca request failed") from exc
        raise MarketDataUnavailableError("Alpaca request failed")

    @staticmethod
    def _raise_for_status(status_code: int) -> None:
        if 200 <= status_code < 300:
            return
        if status_code == 404:
            raise InstrumentNotFoundError("Alpaca instrument not found")
        if status_code == 429:
            raise ProviderRateLimitError("Alpaca rate limit reached")
        if status_code in {400, 422}:
            raise InvalidMarketDataRequestError("Alpaca rejected the market-data request")
        raise MarketDataUnavailableError("Alpaca market data is unavailable")

    @staticmethod
    async def _read_bounded_response(response: httpx.Response) -> bytes:
        content_length = response.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > _MAX_RESPONSE_BYTES:
                    raise MarketDataUnavailableError("Alpaca response exceeds size limit")
            except ValueError as exc:
                raise MarketDataUnavailableError("invalid Alpaca response length") from exc
        content = bytearray()
        async for chunk in response.aiter_bytes():
            content.extend(chunk)
            if len(content) > _MAX_RESPONSE_BYTES:
                raise MarketDataUnavailableError("Alpaca response exceeds size limit")
        return bytes(content)

    @staticmethod
    def _decode_json(response: httpx.Response) -> object:
        def reject_constant(_value: str) -> None:
            raise ValueError("non-finite JSON numeric value")

        def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate JSON object key")
                result[key] = value
            return result

        try:
            return json.loads(
                response.content.decode("utf-8"), parse_float=Decimal,
                parse_constant=reject_constant,
                object_pairs_hook=reject_duplicate_keys,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
            raise MarketDataUnavailableError("malformed Alpaca JSON response") from exc

    def _require_available(self) -> None:
        self._require_open()
        if not self._settings.available:
            raise MarketDataUnavailableError("Alpaca provider is not configured")

    def _require_open(self) -> None:
        if self._closed:
            raise MarketDataUnavailableError("Alpaca provider is closed")
        if self._client.is_closed:
            raise MarketDataUnavailableError("Alpaca HTTP client is closed")
