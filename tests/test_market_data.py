"""Tests for the provider-neutral market-data foundation."""

import socket
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.schemas import AssetClass, Instrument, MarketBar, MarketSnapshot, Timeframe
from app.data.fake_market import FakeMarketDataProvider
from app.data.market import (
    BarRequest,
    InstrumentNotFoundError,
    InvalidMarketDataRequestError,
    MarketDataError,
    MarketDataProvider,
    MarketDataUnavailableError,
    ProviderRateLimitError,
)

START = datetime(2026, 8, 29, 9, 0, tzinfo=UTC)


@pytest.fixture
def instrument() -> Instrument:
    return Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY, exchange="NASDAQ")


def make_snapshot(
    instrument: Instrument, price: Decimal = Decimal("123456789.123456789")
) -> MarketSnapshot:
    return MarketSnapshot(
        instrument=instrument,
        timestamp=START,
        last_price=price,
        day_volume=Decimal("1000000.1"),
    )


def make_bar(
    instrument: Instrument,
    minute: int,
    price: str = "100.1",
    timeframe: Timeframe = Timeframe.ONE_MINUTE,
) -> MarketBar:
    value = Decimal(price)
    return MarketBar(
        instrument=instrument,
        timeframe=timeframe,
        timestamp=START + timedelta(minutes=minute),
        open=value,
        high=value,
        low=value,
        close=value,
        volume=Decimal("10.1"),
    )


class MalformedMarketDataProvider(MarketDataProvider):
    """Return scripted malformed bars so base output validation can be attacked."""

    def __init__(self, bars: list[MarketBar]) -> None:
        self._scripted_bars = bars

    async def get_snapshot(self, instrument: Instrument) -> MarketSnapshot:
        raise InstrumentNotFoundError("not configured")

    async def _get_bars(self, request: BarRequest) -> list[MarketBar]:
        return list(self._scripted_bars)

    async def get_batch_snapshots(
        self, instruments: list[Instrument]
    ) -> list[MarketSnapshot]:
        raise InstrumentNotFoundError("not configured")


def test_market_bar_requires_timeframe(instrument: Instrument) -> None:
    values = make_bar(instrument, 0).model_dump()
    del values["timeframe"]

    with pytest.raises(ValidationError, match="timeframe"):
        MarketBar.model_validate(values)


@pytest.mark.asyncio
async def test_fake_provider_returns_known_snapshot(instrument: Instrument) -> None:
    snapshot = make_snapshot(instrument)
    provider = FakeMarketDataProvider(snapshots=[snapshot])

    assert await provider.get_snapshot(instrument) is snapshot


@pytest.mark.asyncio
async def test_unknown_snapshot_raises(instrument: Instrument) -> None:
    provider = FakeMarketDataProvider()

    with pytest.raises(InstrumentNotFoundError):
        await provider.get_snapshot(instrument)


@pytest.mark.asyncio
async def test_get_bars_filters_half_open_range(instrument: Instrument) -> None:
    provider = FakeMarketDataProvider(
        bars=[
            make_bar(instrument, minute, timeframe=Timeframe.FIVE_MINUTES)
            for minute in (0, 5, 10, 15)
        ]
    )

    bars = await provider.get_bars(
        instrument,
        START + timedelta(minutes=5),
        START + timedelta(minutes=15),
        Timeframe.FIVE_MINUTES,
    )

    assert [bar.timestamp for bar in bars] == [
        START + timedelta(minutes=5),
        START + timedelta(minutes=10),
    ]


@pytest.mark.asyncio
async def test_get_bars_sorts_oldest_to_newest(instrument: Instrument) -> None:
    provider = FakeMarketDataProvider(bars=[make_bar(instrument, 10), make_bar(instrument, 0)])

    bars = await provider.get_bars(
        instrument, START, START + timedelta(minutes=15), Timeframe.ONE_MINUTE
    )

    assert [bar.timestamp for bar in bars] == [START, START + timedelta(minutes=10)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("start", "end"),
    [(START, START), (START, START - timedelta(seconds=1))],
)
async def test_invalid_bar_ranges_are_rejected(
    instrument: Instrument, start: datetime, end: datetime
) -> None:
    provider = FakeMarketDataProvider(snapshots=[make_snapshot(instrument)])

    with pytest.raises(InvalidMarketDataRequestError):
        await provider.get_bars(instrument, start, end, Timeframe.ONE_MINUTE)


@pytest.mark.asyncio
@pytest.mark.parametrize("naive_field", ["start", "end"])
async def test_naive_bar_range_is_rejected(instrument: Instrument, naive_field: str) -> None:
    provider = FakeMarketDataProvider(snapshots=[make_snapshot(instrument)])
    values = {"start": START, "end": START + timedelta(minutes=1)}
    values[naive_field] = datetime(2026, 8, 29, 9, 0)

    with pytest.raises(InvalidMarketDataRequestError):
        await provider.get_bars(
            instrument, values["start"], values["end"], Timeframe.ONE_MINUTE
        )


@pytest.mark.asyncio
async def test_bar_range_offsets_normalize_to_utc(instrument: Instrument) -> None:
    provider = FakeMarketDataProvider(bars=[make_bar(instrument, 0)])
    offset_start = datetime.fromisoformat("2026-08-29T11:00:00+02:00")
    offset_end = datetime.fromisoformat("2026-08-29T04:01:00-05:00")

    bars = await provider.get_bars(
        instrument, offset_start, offset_end, Timeframe.ONE_MINUTE
    )

    assert bars == [make_bar(instrument, 0)]


@pytest.mark.asyncio
async def test_batch_preserves_order_and_duplicates(instrument: Instrument) -> None:
    second = Instrument(symbol="MSFT", asset_class=AssetClass.EQUITY, exchange="NASDAQ")
    first_snapshot = make_snapshot(instrument)
    second_snapshot = make_snapshot(second)
    provider = FakeMarketDataProvider(snapshots=[first_snapshot, second_snapshot])

    result = await provider.get_batch_snapshots([second, instrument, second])

    assert result == [second_snapshot, first_snapshot, second_snapshot]


@pytest.mark.asyncio
async def test_missing_batch_instrument_raises_without_silent_partial_result(
    instrument: Instrument,
) -> None:
    missing = Instrument(symbol="MISSING", asset_class=AssetClass.EQUITY)
    provider = FakeMarketDataProvider(snapshots=[make_snapshot(instrument)])

    with pytest.raises(InstrumentNotFoundError):
        await provider.get_batch_snapshots([instrument, missing])


@pytest.mark.asyncio
async def test_fake_provider_makes_no_network_calls(
    instrument: Instrument, monkeypatch: pytest.MonkeyPatch
) -> None:
    def reject_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    provider = FakeMarketDataProvider(snapshots=[make_snapshot(instrument)])

    assert await provider.get_snapshot(instrument) == make_snapshot(instrument)


@pytest.mark.asyncio
async def test_fake_provider_does_not_fabricate_missing_bars(instrument: Instrument) -> None:
    provider = FakeMarketDataProvider()

    with pytest.raises(InstrumentNotFoundError):
        await provider.get_bars(
            instrument, START, START + timedelta(minutes=1), Timeframe.ONE_MINUTE
        )


def test_duplicate_bar_timestamps_are_rejected(instrument: Instrument) -> None:
    with pytest.raises(InvalidMarketDataRequestError, match="duplicate"):
        FakeMarketDataProvider(bars=[make_bar(instrument, 0), make_bar(instrument, 0)])


def test_market_bars_remain_immutable(instrument: Instrument) -> None:
    bar = make_bar(instrument, 0)

    with pytest.raises(ValidationError, match="frozen"):
        bar.close = Decimal("1")


@pytest.mark.asyncio
async def test_decimal_precision_survives_provider_round_trip(instrument: Instrument) -> None:
    precise = Decimal("123456789.123456789")
    snapshot = make_snapshot(instrument, precise)
    bar = make_bar(instrument, 0, price=str(precise))
    provider = FakeMarketDataProvider(snapshots=[snapshot], bars=[bar])

    snapshot_result = await provider.get_snapshot(instrument)
    bar_result = await provider.get_bars(
        instrument, START, START + timedelta(minutes=1), Timeframe.ONE_MINUTE
    )

    assert snapshot_result.last_price == precise
    assert bar_result[0].close == precise


@pytest.mark.asyncio
async def test_same_timestamp_different_timeframes_can_coexist(instrument: Instrument) -> None:
    one_minute = make_bar(instrument, 0, timeframe=Timeframe.ONE_MINUTE)
    one_hour = make_bar(instrument, 0, timeframe=Timeframe.ONE_HOUR)
    provider = FakeMarketDataProvider(bars=[one_minute, one_hour])

    minute_result = await provider.get_bars(
        instrument, START, START + timedelta(hours=1), Timeframe.ONE_MINUTE
    )
    hour_result = await provider.get_bars(
        instrument, START, START + timedelta(hours=1), Timeframe.ONE_HOUR
    )

    assert minute_result == [one_minute]
    assert hour_result == [one_hour]


@pytest.mark.asyncio
async def test_missing_timeframe_series_returns_empty_without_resampling(
    instrument: Instrument,
) -> None:
    provider = FakeMarketDataProvider(
        bars=[make_bar(instrument, 0, timeframe=Timeframe.ONE_MINUTE)]
    )

    result = await provider.get_bars(
        instrument, START, START + timedelta(hours=1), Timeframe.ONE_HOUR
    )

    assert result == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformed",
    ["wrong_instrument", "wrong_timeframe", "duplicate", "out_of_range", "unordered"],
)
async def test_malformed_provider_output_is_rejected(
    instrument: Instrument, malformed: str
) -> None:
    other = Instrument(symbol="MSFT", asset_class=AssetClass.EQUITY, exchange="NASDAQ")
    valid = make_bar(instrument, 1)
    malformed_bars = {
        "wrong_instrument": [make_bar(other, 1)],
        "wrong_timeframe": [make_bar(instrument, 1, timeframe=Timeframe.ONE_HOUR)],
        "duplicate": [valid, valid],
        "out_of_range": [make_bar(instrument, 61)],
        "unordered": [make_bar(instrument, 2), make_bar(instrument, 1)],
    }
    provider = MalformedMarketDataProvider(malformed_bars[malformed])

    with pytest.raises(MarketDataUnavailableError):
        await provider.get_bars(
            instrument, START, START + timedelta(hours=1), Timeframe.ONE_MINUTE
        )


@pytest.mark.asyncio
async def test_exchange_identity_remains_isolated_across_timeframes() -> None:
    nasdaq = Instrument(symbol="NVDA", asset_class=AssetClass.EQUITY, exchange="NASDAQ")
    other = Instrument(symbol="NVDA", asset_class=AssetClass.EQUITY, exchange="OTHER")
    nasdaq_bar = make_bar(nasdaq, 0, timeframe=Timeframe.ONE_MINUTE)
    other_bar = make_bar(other, 0, timeframe=Timeframe.ONE_HOUR)
    provider = FakeMarketDataProvider(bars=[nasdaq_bar, other_bar])

    assert await provider.get_bars(
        nasdaq, START, START + timedelta(hours=1), Timeframe.ONE_MINUTE
    ) == [nasdaq_bar]
    assert await provider.get_bars(
        other, START, START + timedelta(hours=1), Timeframe.ONE_HOUR
    ) == [other_bar]
    assert await provider.get_bars(
        nasdaq, START, START + timedelta(hours=1), Timeframe.ONE_HOUR
    ) == []


def test_timeframe_enum_is_predictable() -> None:
    assert Timeframe.ONE_MINUTE.value == "ONE_MINUTE"
    assert Timeframe("ONE_DAY") is Timeframe.ONE_DAY


def test_provider_neutral_exception_hierarchy() -> None:
    assert issubclass(InstrumentNotFoundError, MarketDataError)
    assert issubclass(MarketDataUnavailableError, MarketDataError)
    assert issubclass(InvalidMarketDataRequestError, MarketDataError)
    assert issubclass(ProviderRateLimitError, MarketDataUnavailableError)


@pytest.mark.asyncio
async def test_same_symbol_different_exchanges_are_distinct() -> None:
    nasdaq = Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY, exchange="NASDAQ")
    other = Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY, exchange="OTHER")
    nasdaq_snapshot = make_snapshot(nasdaq, Decimal("100"))
    other_snapshot = make_snapshot(other, Decimal("200"))
    provider = FakeMarketDataProvider(snapshots=[nasdaq_snapshot, other_snapshot])

    assert await provider.get_snapshot(nasdaq) == nasdaq_snapshot
    assert await provider.get_snapshot(other) == other_snapshot


@pytest.mark.asyncio
async def test_known_instrument_empty_interval_returns_empty_list(instrument: Instrument) -> None:
    provider = FakeMarketDataProvider(snapshots=[make_snapshot(instrument)])

    result = await provider.get_bars(
        instrument, START, START + timedelta(minutes=1), Timeframe.ONE_MINUTE
    )

    assert result == []
