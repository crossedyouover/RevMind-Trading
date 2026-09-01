"""Adversarial offline tests for the read-only Alpaca market-data adapter."""

import inspect
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

import httpx
import pytest
from pydantic import ValidationError

import app.data.providers.alpaca.provider as provider_module
from app.core.schemas import AssetClass, Instrument, MarketBar, Timeframe
from app.data.market import (
    InstrumentNotFoundError,
    InvalidMarketDataRequestError,
    MarketDataUnavailableError,
    ProviderRateLimitError,
)
from app.data.providers.alpaca import (
    AlpacaDataFeed,
    AlpacaInstrumentBinding,
    AlpacaMarketDataProvider,
    AlpacaMarketDataSettings,
)
from app.data.providers.alpaca.wire import parse_alpaca_timestamp

_START = datetime(2025, 1, 2, 14, 30, tzinfo=UTC)
_INSTRUMENT = Instrument(
    symbol="AAPL", asset_class=AssetClass.EQUITY, exchange="XNAS", currency="USD"
)
_BINDING = AlpacaInstrumentBinding(instrument=_INSTRUMENT, provider_symbol="AAPL")


def _settings(*, configured: bool = True) -> AlpacaMarketDataSettings:
    values: dict[str, object] = {
        "api_key_id": None,
        "api_secret_key": None,
        "data_feed": AlpacaDataFeed.IEX,
    }
    if configured:
        values.update(api_key_id="distinct-key", api_secret_key="distinct-secret")
    return AlpacaMarketDataSettings.model_validate(values)


def _bar(
    timestamp: str = "2025-01-02T14:30:00.123456789Z",
    **updates: object,
) -> dict[str, object]:
    value: dict[str, object] = {
        "t": timestamp,
        "o": 100.123456789,
        "h": 102.5,
        "l": 99,
        "c": 101.000000001,
        "v": 1234,
        "n": 42,
        "vw": 100.75,
    }
    value.update(updates)
    return value


def _bars_response(
    bars: list[dict[str, object]], *, symbol: str = "AAPL", token: str | None = None
) -> dict[str, object]:
    return {"bars": bars, "symbol": symbol, "next_page_token": token}


def _snapshot(timestamp: str = "2025-01-02T14:30:00.987654321Z") -> dict[str, object]:
    return {
        "latestTrade": {
            "t": timestamp, "p": 101.123456789, "s": 10, "x": "V",
            "c": ["@"], "i": 123, "z": "C",
        },
        "dailyBar": {"t": "2025-01-02T05:00:00Z", "v": 999999},
    }


def _client(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="https://data.alpaca.markets", transport=handler,
        follow_redirects=False,
    )


def _provider(
    client: httpx.AsyncClient, *bindings: AlpacaInstrumentBinding
) -> AlpacaMarketDataProvider:
    return AlpacaMarketDataProvider(_settings(), bindings or (_BINDING,), client=client)


async def _get_bars(
    provider: AlpacaMarketDataProvider,
    timeframe: Timeframe = Timeframe.ONE_MINUTE,
) -> list[MarketBar]:
    return await provider.get_bars(
        _INSTRUMENT, _START, _START + timedelta(hours=1), timeframe
    )


def test_configuration_absent_present_partial_feed_and_secret_safety() -> None:
    absent = _settings(configured=False)
    present = _settings()
    assert not absent.available
    assert present.available
    assert absent.data_feed is AlpacaDataFeed.IEX
    sip = AlpacaMarketDataSettings.model_validate(
        {
            "api_key_id": "distinct-key",
            "api_secret_key": "distinct-secret",
            "data_feed": "sip",
        }
    )
    assert sip.data_feed is AlpacaDataFeed.SIP
    for partial in (
        {"api_key_id": "distinct-key"}, {"api_secret_key": "distinct-secret"}
    ):
        with pytest.raises(ValidationError):
            AlpacaMarketDataSettings.model_validate(partial)
    rendered = repr(present) + present.model_dump_json()
    assert "distinct-key" not in rendered
    assert "distinct-secret" not in rendered


def test_blank_credentials_are_absent_and_validation_hides_raw_input() -> None:
    for blank in ("", "   "):
        settings = AlpacaMarketDataSettings.model_validate(
            {"api_key_id": blank, "api_secret_key": blank}
        )
        assert not settings.available
        assert settings.api_key_id is None and settings.api_secret_key is None
    distinctive = "DISTINCTIVE-RAW-CREDENTIAL-DO-NOT-LEAK"
    with pytest.raises(ValidationError) as partial:
        AlpacaMarketDataSettings.model_validate(
            {"api_key_id": distinctive, "api_secret_key": None}
        )
    assert distinctive not in str(partial.value)
    assert "DISTINCTIVE" not in str(partial.value)
    with pytest.raises(ValidationError) as whitespace:
        AlpacaMarketDataSettings.model_validate(
            {"api_key_id": f" {distinctive}", "api_secret_key": distinctive}
        )
    assert distinctive not in str(whitespace.value)


@pytest.mark.asyncio
async def test_missing_credentials_disable_only_adapter() -> None:
    client = _client(httpx.MockTransport(lambda _request: pytest.fail("network used")))
    provider = AlpacaMarketDataProvider(_settings(configured=False), (_BINDING,), client=client)
    with pytest.raises(MarketDataUnavailableError, match="not configured"):
        await _get_bars(provider)
    assert await provider.get_batch_snapshots([]) == []
    await client.aclose()


def test_binding_scope_identity_and_immutability() -> None:
    assert _BINDING.provider_symbol == "AAPL"
    with pytest.raises(ValidationError):
        _BINDING.provider_symbol = "MSFT"
    for instrument in (
        Instrument(symbol="BTC", asset_class=AssetClass.CRYPTO, exchange="X", currency="USD"),
        Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY, exchange="XNAS", currency="EUR"),
        Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY, currency="USD"),
    ):
        with pytest.raises(ValidationError):
            AlpacaInstrumentBinding(instrument=instrument, provider_symbol="AAPL")
    with pytest.raises(ValidationError):
        AlpacaInstrumentBinding(instrument=_INSTRUMENT, provider_symbol="bad symbol?")
    with pytest.raises(ValidationError):
        AlpacaInstrumentBinding(instrument=_INSTRUMENT, provider_symbol=" AAPL ")
    etf = Instrument(symbol="SPY", asset_class=AssetClass.ETF, exchange="ARCX", currency="USD")
    assert AlpacaInstrumentBinding(instrument=etf, provider_symbol="SPY").instrument == etf


def test_binding_never_collapses_complete_identity() -> None:
    other_exchange = _INSTRUMENT.model_copy(update={"exchange": "XNYS"})
    other_currency = _INSTRUMENT.model_copy(update={"currency": "EUR"})
    with pytest.raises(InvalidMarketDataRequestError):
        AlpacaMarketDataProvider(
            _settings(),
            (_BINDING, AlpacaInstrumentBinding(instrument=other_exchange, provider_symbol="AAPL")),
        )
    with pytest.raises(ValidationError):
        AlpacaInstrumentBinding(instrument=other_currency, provider_symbol="AAPL-EUR")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("timeframe", "wire"),
    tuple(zip(tuple(Timeframe), ("1Min", "5Min", "15Min", "1Hour", "1Day"), strict=True)),
)
async def test_exact_timeframe_mapping(timeframe: Timeframe, wire: str) -> None:
    seen: list[str] = []
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["timeframe"])
        return httpx.Response(200, json=_bars_response([]))
    client = _client(httpx.MockTransport(handler))
    provider = _provider(client)
    await _get_bars(provider, timeframe)
    assert seen == [wire]
    await client.aclose()


@pytest.mark.asyncio
async def test_single_page_decimal_integer_volume_time_and_request_semantics() -> None:
    requests: list[httpx.Request] = []
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=json.dumps(_bars_response([_bar()])).encode())
    client = _client(httpx.MockTransport(handler))
    provider = _provider(client)
    original = _BINDING.model_dump_json()
    bars = await _get_bars(provider)
    assert bars[0].open == Decimal("100.123456789")
    assert bars[0].close == Decimal("101.000000001")
    assert bars[0].volume == Decimal(1234)
    assert bars[0].timestamp == datetime(2025, 1, 2, 14, 30, 0, 123456, tzinfo=UTC)
    params = requests[0].url.params
    assert params["sort"] == "asc" and params["adjustment"] == "raw"
    assert params["feed"] == "iex" and params["start"].endswith("Z")
    assert requests[0].headers["APCA-API-KEY-ID"] == "distinct-key"
    assert requests[0].headers["APCA-API-SECRET-KEY"] == "distinct-secret"
    assert "distinct-key" not in str(requests[0].url)
    assert "distinct-secret" not in str(requests[0].url)
    assert _BINDING.model_dump_json() == original
    await client.aclose()


@pytest.mark.asyncio
async def test_multiple_pages_and_empty_response() -> None:
    calls = 0
    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, json=_bars_response([_bar()], token="NEXT"))
        assert request.url.params["page_token"] == "NEXT"
        return httpx.Response(
            200, json=_bars_response([_bar("2025-01-02T14:31:00Z")])
        )
    client = _client(httpx.MockTransport(handler))
    assert len(await _get_bars(_provider(client))) == 2
    await client.aclose()
    empty = _client(httpx.MockTransport(lambda _r: httpx.Response(200, json=_bars_response([]))))
    assert await _get_bars(_provider(empty)) == []
    await empty.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    (
        _bars_response([_bar(t="2025-01-02T14:30:00")]),
        _bars_response([_bar(t="not-time")]),
        _bars_response([_bar(o=None)]),
        _bars_response([_bar(o=True)]),
        _bars_response([_bar(o=-1)]),
        _bars_response([_bar()], symbol="MSFT"),
        {"bars": [{}], "symbol": "AAPL", "next_page_token": None},
    ),
)
async def test_invalid_bar_payloads_fail_closed(payload: object) -> None:
    client = _client(httpx.MockTransport(lambda _r: httpx.Response(200, json=payload)))
    with pytest.raises(MarketDataUnavailableError):
        await _get_bars(_provider(client))
    await client.aclose()


@pytest.mark.asyncio
async def test_boolean_optional_wire_integer_is_rejected() -> None:
    client = _client(
        httpx.MockTransport(
            lambda _request: httpx.Response(
                200, json=_bars_response([_bar(n=True)])
            )
        )
    )
    with pytest.raises(MarketDataUnavailableError):
        await _get_bars(_provider(client))
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("constant", ("NaN", "Infinity", "-Infinity"))
async def test_nonfinite_and_malformed_json_fail_without_retry(constant: str) -> None:
    calls = 0
    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            content=(f'{{"bars":[{{"t":"2025-01-02T14:30:00Z","o":{constant},'
                     '"h":2,"l":1,"c":1,"v":1}],"symbol":"AAPL",'
                     '"next_page_token":null}').encode(),
        )
    client = _client(httpx.MockTransport(handler))
    with pytest.raises(MarketDataUnavailableError):
        await _get_bars(_provider(client))
    assert calls == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_duplicate_reversed_and_out_of_range_are_not_repaired() -> None:
    payloads = (
        _bars_response([_bar(), _bar()]),
        _bars_response([_bar("2025-01-02T14:31:00Z"), _bar()]),
        _bars_response([_bar("2025-01-02T16:00:00Z")]),
    )
    for payload in payloads:
        client = _client(httpx.MockTransport(lambda _r, p=payload: httpx.Response(200, json=p)))
        with pytest.raises(MarketDataUnavailableError):
            await _get_bars(_provider(client))
        await client.aclose()


@pytest.mark.asyncio
async def test_pagination_token_page_record_and_response_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for token in ("BAD TOKEN", "REPEAT"):
        calls = 0
        def handler(_request: httpx.Request, token: str = token) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json=_bars_response([], token=token))
        client = _client(httpx.MockTransport(handler))
        with pytest.raises(MarketDataUnavailableError):
            await _get_bars(_provider(client))
        await client.aclose()
    monkeypatch.setattr(provider_module, "_MAX_RECORDS", 0)
    client = _client(
        httpx.MockTransport(
            lambda _r: httpx.Response(200, json=_bars_response([_bar()]))
        )
    )
    with pytest.raises(MarketDataUnavailableError, match="record"):
        await _get_bars(_provider(client))
    await client.aclose()
    oversized = _client(
        httpx.MockTransport(
            lambda _r: httpx.Response(
                200, headers={"content-length": "5000001"}, content=b"{}"
            )
        )
    )
    with pytest.raises(MarketDataUnavailableError, match="size"):
        await _get_bars(_provider(oversized))
    await oversized.aclose()


@pytest.mark.asyncio
async def test_unique_tokens_cannot_exceed_page_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(provider_module, "_MAX_PAGES", 2)
    calls = 0
    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_bars_response([], token=f"TOKEN{calls}"))
    client = _client(httpx.MockTransport(handler))
    with pytest.raises(MarketDataUnavailableError, match="page limit"):
        await _get_bars(_provider(client))
    assert calls == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_malformed_json_syntax_is_not_retried() -> None:
    calls = 0
    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b'{"bars":')
    client = _client(httpx.MockTransport(handler))
    with pytest.raises(MarketDataUnavailableError, match="JSON"):
        await _get_bars(_provider(client))
    assert calls == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_duplicate_json_object_keys_fail_closed() -> None:
    duplicate = (
        b'{"AAPL":{"latestTrade":{"t":"2025-01-02T14:30:00Z","p":1}},'
        b'"AAPL":{"latestTrade":{"t":"2025-01-02T14:31:00Z","p":2}}}'
    )
    client = _client(
        httpx.MockTransport(lambda _request: httpx.Response(200, content=duplicate))
    )
    with pytest.raises(MarketDataUnavailableError, match="JSON"):
        await _provider(client).get_snapshot(_INSTRUMENT)
    await client.aclose()


def test_unsupported_runtime_timeframe_is_rejected_without_approximation() -> None:
    with pytest.raises(InvalidMarketDataRequestError, match="timeframe"):
        AlpacaMarketDataProvider._map_timeframe(cast(Timeframe, object()))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error"),
    ((400, InvalidMarketDataRequestError), (401, MarketDataUnavailableError),
     (403, MarketDataUnavailableError), (404, InstrumentNotFoundError),
     (422, InvalidMarketDataRequestError), (429, ProviderRateLimitError),
     (500, MarketDataUnavailableError)),
)
async def test_http_error_mapping_has_no_retry(status: int, error: type[Exception]) -> None:
    calls = 0
    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, text="distinct-secret distinct-key")
    client = _client(httpx.MockTransport(handler))
    with pytest.raises(error) as captured:
        await _get_bars(_provider(client))
    assert calls == 1
    assert "distinct-secret" not in str(captured.value)
    assert "distinct-key" not in str(captured.value)
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", (502, 503, 504))
async def test_retryable_statuses_retry_at_most_twice(status: int) -> None:
    calls = 0
    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status)
    client = _client(httpx.MockTransport(handler))
    with pytest.raises(MarketDataUnavailableError):
        await _get_bars(_provider(client))
    assert calls == 3
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ("connect", "timeout"))
async def test_transport_failures_retry_at_most_twice(failure: str) -> None:
    calls = 0
    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if failure == "connect":
            raise httpx.ConnectError("private failure", request=request)
        raise httpx.ReadTimeout("private failure", request=request)
    client = _client(httpx.MockTransport(handler))
    with pytest.raises(MarketDataUnavailableError):
        await _get_bars(_provider(client))
    assert calls == 3
    await client.aclose()


@pytest.mark.asyncio
async def test_other_httpx_transport_errors_do_not_escape_neutral_taxonomy() -> None:
    calls = 0
    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.RemoteProtocolError("malformed transport", request=request)
    client = _client(httpx.MockTransport(handler))
    with pytest.raises(MarketDataUnavailableError):
        await _get_bars(_provider(client))
    assert calls == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_snapshot_uses_only_latest_trade_and_truncates_nanoseconds() -> None:
    client = _client(
        httpx.MockTransport(
            lambda _r: httpx.Response(200, json={"AAPL": _snapshot()})
        )
    )
    result = await _provider(client).get_snapshot(_INSTRUMENT)
    assert result.instrument == _INSTRUMENT
    assert result.last_price == Decimal("101.123456789")
    assert result.timestamp.microsecond == 987654
    assert result.day_volume is None and result.percent_change is None
    await client.aclose()


@pytest.mark.asyncio
async def test_snapshot_missing_or_malformed_trade_fails() -> None:
    values: tuple[dict[str, object], ...] = (
        {"dailyBar": {}},
        {"latestTrade": {"t": "bad", "p": 1}},
    )
    for value in values:
        client = _client(
            httpx.MockTransport(
                lambda _r, v=value: httpx.Response(200, json={"AAPL": v})
            )
        )
        with pytest.raises(MarketDataUnavailableError):
            await _provider(client).get_snapshot(_INSTRUMENT)
        await client.aclose()


@pytest.mark.asyncio
async def test_batch_preserves_order_duplicates_and_requires_complete_response() -> None:
    msft = Instrument(symbol="MSFT", asset_class=AssetClass.EQUITY, exchange="XNAS", currency="USD")
    binding = AlpacaInstrumentBinding(instrument=msft, provider_symbol="MSFT")
    requests: list[httpx.Request] = []
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"AAPL": _snapshot(), "MSFT": _snapshot()})
    client = _client(httpx.MockTransport(handler))
    result = await _provider(client, _BINDING, binding).get_batch_snapshots(
        [msft, _INSTRUMENT, msft]
    )
    assert [item.instrument for item in result] == [msft, _INSTRUMENT, msft]
    assert requests[0].url.params["symbols"] == "MSFT,AAPL"
    await client.aclose()
    missing = _client(
        httpx.MockTransport(
            lambda _r: httpx.Response(200, json={"AAPL": _snapshot()})
        )
    )
    with pytest.raises(InstrumentNotFoundError):
        await _provider(missing, _BINDING, binding).get_batch_snapshots([_INSTRUMENT, msft])
    await missing.aclose()


@pytest.mark.asyncio
async def test_unbound_complete_identity_is_not_symbol_match() -> None:
    other = Instrument(
        symbol="AAPL", asset_class=AssetClass.EQUITY,
        exchange="XNYS", currency="USD",
    )
    client = _client(httpx.MockTransport(lambda _r: pytest.fail("network used")))
    with pytest.raises(InstrumentNotFoundError):
        await _provider(client).get_snapshot(other)
    await client.aclose()


@pytest.mark.asyncio
async def test_owned_and_injected_client_lifecycle() -> None:
    owned = AlpacaMarketDataProvider(_settings(), (_BINDING,))
    assert not owned._client.is_closed
    assert not owned._client.follow_redirects
    await owned.aclose()
    assert owned._client.is_closed
    with pytest.raises(MarketDataUnavailableError, match="closed"):
        await owned.get_snapshot(_INSTRUMENT)
    injected = _client(httpx.MockTransport(lambda _r: httpx.Response(200, json={})))
    provider = _provider(injected)
    await provider.aclose()
    assert not injected.is_closed
    await injected.aclose()


def test_injected_client_cannot_change_origin_or_enable_redirects() -> None:
    wrong_origin = httpx.AsyncClient(base_url="https://example.com")
    with pytest.raises(InvalidMarketDataRequestError, match="security-safe"):
        AlpacaMarketDataProvider(_settings(), (_BINDING,), client=wrong_origin)
    redirects = httpx.AsyncClient(
        base_url="https://data.alpaca.markets", follow_redirects=True
    )
    with pytest.raises(InvalidMarketDataRequestError, match="security-safe"):
        AlpacaMarketDataProvider(_settings(), (_BINDING,), client=redirects)


@pytest.mark.asyncio
async def test_closed_injected_client_fails_through_neutral_taxonomy() -> None:
    already_closed = _client(httpx.MockTransport(lambda _request: pytest.fail("used")))
    await already_closed.aclose()
    with pytest.raises(InvalidMarketDataRequestError, match="security-safe"):
        AlpacaMarketDataProvider(_settings(), (_BINDING,), client=already_closed)

    client = _client(httpx.MockTransport(lambda _request: pytest.fail("used")))
    provider = _provider(client)
    await client.aclose()
    with pytest.raises(MarketDataUnavailableError, match="client is closed"):
        await provider.get_snapshot(_INSTRUMENT)


def test_timestamp_offsets_naive_and_exact_truncation() -> None:
    assert parse_alpaca_timestamp("2025-01-02T15:30:00.123456999+01:00") == datetime(
        2025, 1, 2, 14, 30, 0, 123456, tzinfo=UTC
    )
    with pytest.raises(ValueError):
        parse_alpaca_timestamp("2025-01-02T14:30:00")


def test_no_forbidden_architecture_or_clock_surface() -> None:
    source = inspect.getsource(provider_module)
    forbidden = (
        "ObservedMarketData", "observed_at", "datetime.now", "utcnow", "time.time",
        "monotonic", "perf_counter", "sqlite", "replay", "app.technical",
        "app.evidence", "app.setups", "app.scanner", "app.desks", "websocket",
        "openai", "anthropic", "fincept", "alpaca-py", "order", "execution",
    )
    assert not any(term.lower() in source.lower() for term in forbidden)


@pytest.mark.asyncio
async def test_identical_mocked_exchange_is_deterministic_and_models_immutable() -> None:
    payload = _bars_response([_bar()])
    client = _client(httpx.MockTransport(lambda _r: httpx.Response(200, json=payload)))
    provider = _provider(client)
    first = await _get_bars(provider)
    second = await _get_bars(provider)
    assert first == second
    assert first[0].model_dump_json() == second[0].model_dump_json()
    with pytest.raises(ValidationError):
        first[0].close = Decimal(1)
    await client.aclose()


@pytest.mark.asyncio
async def test_wrong_runtime_input_types_fail_neutrally() -> None:
    client = _client(httpx.MockTransport(lambda _r: pytest.fail("network used")))
    provider = _provider(client)
    with pytest.raises(InvalidMarketDataRequestError):
        await provider.get_snapshot(cast(Instrument, object()))
    with pytest.raises(InvalidMarketDataRequestError):
        await provider.get_batch_snapshots(cast(list[Instrument], ()))
    await client.aclose()
