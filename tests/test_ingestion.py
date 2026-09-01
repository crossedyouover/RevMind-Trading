"""Adversarial tests for provider-independent observation coordination."""

import asyncio
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import cast
from uuid import NAMESPACE_DNS, UUID, uuid1, uuid3, uuid5

import pytest
from pydantic import ValidationError

from app.core.schemas import AssetClass, Instrument, MarketBar, MarketSnapshot, Timeframe
from app.data.ingestion import (
    IngestionError,
    IngestionResult,
    MarketDataIngestionCoordinator,
    SystemUtcClock,
)
from app.data.market import (
    BarRequest,
    InstrumentNotFoundError,
    InvalidMarketDataRequestError,
    MarketDataProvider,
    MarketDataUnavailableError,
    ProviderRateLimitError,
)
from app.data.observation_store import (
    ObservationConflictError,
    ObservationStore,
    ObservationStoreError,
    ObservationStoreUnavailableError,
)
from app.data.observations import ObservedMarketData, SourceIdentity

INSTRUMENT = Instrument(
    symbol="AAPL", asset_class=AssetClass.EQUITY, exchange="NASDAQ", currency="USD"
)
OTHER = Instrument(
    symbol="MSFT", asset_class=AssetClass.EQUITY, exchange="NASDAQ", currency="USD"
)
EVENT_TIME = datetime(2026, 8, 1, 14, 30, tzinfo=UTC)
OBSERVED_AT = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
SOURCE = SourceIdentity(name="alpaca-market-data")
IDS = tuple(UUID(f"00000000-0000-4000-8000-{value:012d}") for value in range(1, 20))


def snapshot(
    instrument: Instrument = INSTRUMENT, timestamp: datetime = EVENT_TIME
) -> MarketSnapshot:
    return MarketSnapshot(
        instrument=instrument,
        timestamp=timestamp,
        last_price=Decimal("201.125"),
        day_volume=Decimal("123456.75"),
    )


def bar(
    timestamp: datetime = EVENT_TIME,
    instrument: Instrument = INSTRUMENT,
    timeframe: Timeframe = Timeframe.ONE_MINUTE,
) -> MarketBar:
    return MarketBar(
        instrument=instrument,
        timeframe=timeframe,
        timestamp=timestamp,
        open=Decimal("100.1"),
        high=Decimal("101.2"),
        low=Decimal("99.9"),
        close=Decimal("100.7"),
        volume=Decimal("1000.5"),
    )


def request() -> BarRequest:
    return BarRequest(
        instrument=INSTRUMENT,
        start=EVENT_TIME,
        end=EVENT_TIME + timedelta(hours=1),
        timeframe=Timeframe.ONE_MINUTE,
    )


class StubProvider(MarketDataProvider):
    def __init__(
        self,
        *,
        snapshot_value: object | None = None,
        batch_value: object | None = None,
        bars_value: object | None = None,
        error: Exception | None = None,
    ) -> None:
        self.snapshot_value = snapshot() if snapshot_value is None else snapshot_value
        self.batch_value = [] if batch_value is None else batch_value
        self.bars_value = [] if bars_value is None else bars_value
        self.error = error
        self.snapshot_calls = 0
        self.batch_calls = 0
        self.bar_calls = 0

    async def get_snapshot(self, instrument: Instrument) -> MarketSnapshot:
        self.snapshot_calls += 1
        if self.error is not None:
            raise self.error
        return cast(MarketSnapshot, self.snapshot_value)

    async def _get_bars(self, request: BarRequest) -> list[MarketBar]:
        self.bar_calls += 1
        if self.error is not None:
            raise self.error
        return cast(list[MarketBar], self.bars_value)

    async def get_batch_snapshots(
        self, instruments: list[Instrument]
    ) -> list[MarketSnapshot]:
        self.batch_calls += 1
        if self.error is not None:
            raise self.error
        return cast(list[MarketSnapshot], self.batch_value)


class RuntimeViolationProvider(StubProvider):
    """Bypass the frozen wrapper to emulate a bad third-party implementation."""

    async def get_bars(
        self,
        instrument: Instrument,
        start: datetime,
        end: datetime,
        timeframe: Timeframe,
    ) -> list[MarketBar]:
        self.bar_calls += 1
        return cast(list[MarketBar], self.bars_value)


class BlockingBatchProvider(StubProvider):
    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.received: list[Instrument] | None = None

    async def get_batch_snapshots(
        self, instruments: list[Instrument]
    ) -> list[MarketSnapshot]:
        self.batch_calls += 1
        self.received = instruments
        self.entered.set()
        await self.release.wait()
        return [snapshot(instrument) for instrument in instruments]


class FixedClock:
    def __init__(self, *values: object) -> None:
        self.values = list(values or (OBSERVED_AT,))
        self.calls = 0

    def now(self) -> datetime:
        value = self.values[min(self.calls, len(self.values) - 1)]
        self.calls += 1
        return cast(datetime, value)


class SequentialIds:
    def __init__(self, values: tuple[UUID, ...] = IDS) -> None:
        self.values = iter(values)

    def __call__(self) -> UUID:
        return next(self.values)


class SpyStore:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.observations: list[ObservedMarketData] = []
        self.append_calls = 0
        self.append_many_calls = 0

    def append(self, observation: ObservedMarketData) -> None:
        self.append_calls += 1
        if self.error is not None:
            raise self.error
        self.observations.append(observation)

    def append_many(self, observations: object) -> None:
        self.append_many_calls += 1
        if self.error is not None:
            raise self.error
        self.observations.extend(cast(tuple[ObservedMarketData, ...], observations))


def coordinator(
    provider: MarketDataProvider,
    store: SpyStore,
    *,
    clock: FixedClock | None = None,
    ids: Callable[[], UUID] | None = None,
) -> MarketDataIngestionCoordinator:
    return MarketDataIngestionCoordinator(
        provider,
        cast(ObservationStore, store),
        SOURCE,
        clock=clock or FixedClock(),
        observation_id_factory=ids or SequentialIds(),
    )


@pytest.mark.asyncio
async def test_snapshot_receipt_boundary_and_persisted_result_are_exact() -> None:
    payload = snapshot()
    provider = StubProvider(snapshot_value=payload)
    store = SpyStore()
    clock = FixedClock(OBSERVED_AT)
    result = await coordinator(provider, store, clock=clock).ingest_snapshot(INSTRUMENT)

    assert provider.snapshot_calls == 1
    assert clock.calls == 1
    assert result.count == 1
    assert result.source == SOURCE
    assert result.observed_at == OBSERVED_AT
    assert result.observations[0].payload is payload
    assert result.observations[0].event_time == EVENT_TIME
    assert result.observations[0].observation_id == IDS[0]
    assert result.observations[0].source_record_id is None
    assert store.observations == list(result.observations)
    assert store.append_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [timedelta(hours=2), timedelta(hours=-5)])
async def test_clock_offset_normalizes_to_utc_and_event_time_is_not_substituted(
    offset: timedelta,
) -> None:
    offset_time = OBSERVED_AT.astimezone(timezone(offset))
    result = await coordinator(
        StubProvider(), SpyStore(), clock=FixedClock(offset_time)
    ).ingest_snapshot(INSTRUMENT)
    assert result.observed_at == OBSERVED_AT
    assert result.observations[0].observed_at == OBSERVED_AT
    assert result.observations[0].event_time == EVENT_TIME


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid", [datetime(2026, 9, 1, 12), date(2026, 9, 1), "2026-09-01", None]
)
async def test_invalid_clock_output_fails_before_persistence(invalid: object) -> None:
    store = SpyStore()
    with pytest.raises(IngestionError, match="timezone-aware"):
        await coordinator(
            StubProvider(), store, clock=FixedClock(invalid)
        ).ingest_snapshot(INSTRUMENT)
    assert store.append_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        InvalidMarketDataRequestError("invalid"),
        InstrumentNotFoundError("missing"),
        ProviderRateLimitError("limited"),
        MarketDataUnavailableError("unavailable"),
        RuntimeError("programmer failure"),
    ],
)
async def test_provider_errors_propagate_and_clock_is_not_called(error: Exception) -> None:
    clock = FixedClock()
    store = SpyStore()
    with pytest.raises(type(error), match=str(error)):
        await coordinator(StubProvider(error=error), store, clock=clock).ingest_snapshot(
            INSTRUMENT
        )
    assert clock.calls == 0
    assert store.append_calls == 0


@pytest.mark.asyncio
async def test_snapshot_wrong_type_or_instrument_is_unavailable_and_unwritten() -> None:
    for invalid in (object(), snapshot(OTHER)):
        store = SpyStore()
        with pytest.raises(MarketDataUnavailableError):
            await coordinator(StubProvider(snapshot_value=invalid), store).ingest_snapshot(
                INSTRUMENT
            )
        assert store.append_calls == 0


@pytest.mark.asyncio
async def test_store_failures_and_conflicts_propagate_unchanged() -> None:
    for error in (
        ObservationStoreError("store failure"),
        ObservationStoreUnavailableError("offline"),
        ObservationConflictError("collision"),
    ):
        with pytest.raises(type(error), match=str(error)):
            await coordinator(StubProvider(), SpyStore(error)).ingest_snapshot(INSTRUMENT)


@pytest.mark.asyncio
async def test_batch_preserves_order_duplicates_and_one_shared_boundary() -> None:
    payloads = [snapshot(OTHER), snapshot(INSTRUMENT), snapshot(OTHER)]
    provider = StubProvider(batch_value=payloads)
    store = SpyStore()
    clock = FixedClock()
    result = await coordinator(provider, store, clock=clock).ingest_batch_snapshots(
        [OTHER, INSTRUMENT, OTHER]
    )
    assert provider.batch_calls == 1
    assert clock.calls == 1
    assert store.append_many_calls == 1
    assert tuple(item.payload for item in result.observations) == tuple(payloads)
    assert tuple(item.observation_id for item in result.observations) == IDS[:3]
    assert len({item.observation_id for item in result.observations}) == 3
    assert {item.observed_at for item in result.observations} == {OBSERVED_AT}
    assert store.observations == list(result.observations)


@pytest.mark.asyncio
async def test_empty_batch_has_completion_boundary_and_no_store_call() -> None:
    provider = StubProvider(batch_value=[])
    store = SpyStore()
    clock = FixedClock()
    result = await coordinator(provider, store, clock=clock).ingest_batch_snapshots([])
    assert result.observations == ()
    assert result.count == 0
    assert result.observed_at == OBSERVED_AT
    assert provider.batch_calls == 1
    assert clock.calls == 1
    assert store.append_many_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payloads,instruments",
    [
        ([snapshot()], [INSTRUMENT, OTHER]),
        ([snapshot(OTHER)], [INSTRUMENT]),
        ([object()], [INSTRUMENT]),
    ],
)
async def test_invalid_batch_is_rejected_before_any_write(
    payloads: list[object], instruments: list[Instrument]
) -> None:
    store = SpyStore()
    with pytest.raises(MarketDataUnavailableError):
        await coordinator(StubProvider(batch_value=payloads), store).ingest_batch_snapshots(
            instruments
        )
    assert store.append_many_calls == 0


@pytest.mark.asyncio
async def test_batch_request_is_isolated_from_caller_mutation_during_provider_await() -> None:
    provider = BlockingBatchProvider()
    store = SpyStore()
    requested = [INSTRUMENT]
    task = asyncio.create_task(coordinator(provider, store).ingest_batch_snapshots(requested))
    await provider.entered.wait()
    requested[0] = OTHER
    requested.append(OTHER)
    provider.release.set()
    result = await task

    assert provider.received == [INSTRUMENT]
    assert [item.payload.instrument for item in result.observations] == [INSTRUMENT]
    assert result.count == 1


@pytest.mark.asyncio
async def test_invalid_uuid_mid_batch_prevents_the_atomic_store_call() -> None:
    ids = SequentialIds((IDS[0], uuid1()))
    store = SpyStore()
    with pytest.raises(IngestionError, match="UUID4"):
        await coordinator(
            StubProvider(batch_value=[snapshot(), snapshot(OTHER)]), store, ids=ids
        ).ingest_batch_snapshots([INSTRUMENT, OTHER])
    assert store.append_many_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_id",
    [uuid1(), uuid3(NAMESPACE_DNS, "x"), uuid5(NAMESPACE_DNS, "x"), cast(UUID, "bad")],
)
async def test_invalid_observation_identity_fails_before_persistence(
    invalid_id: UUID,
) -> None:
    store = SpyStore()
    with pytest.raises(IngestionError, match="UUID4"):
        await coordinator(StubProvider(), store, ids=lambda: invalid_id).ingest_snapshot(
            INSTRUMENT
        )
    assert store.append_calls == 0


@pytest.mark.asyncio
async def test_uuid_factory_failure_is_narrowly_classified_before_persistence() -> None:
    def fail() -> UUID:
        raise ValueError("factory failed")

    store = SpyStore()
    with pytest.raises(IngestionError, match="factory failed"):
        await coordinator(StubProvider(), store, ids=fail).ingest_snapshot(INSTRUMENT)
    assert store.append_calls == 0


@pytest.mark.asyncio
async def test_duplicate_valid_uuid4_is_not_regenerated_and_store_conflict_propagates() -> None:
    class DuplicateRejectingStore(SpyStore):
        def append_many(self, observations: object) -> None:
            values = cast(tuple[ObservedMarketData, ...], observations)
            self.append_many_calls += 1
            if len({item.observation_id for item in values}) != len(values):
                raise ObservationConflictError("duplicate identity")
            self.observations.extend(values)

    store = DuplicateRejectingStore()
    with pytest.raises(ObservationConflictError, match="duplicate identity"):
        await coordinator(
            StubProvider(batch_value=[snapshot(), snapshot(OTHER)]),
            store,
            ids=lambda: IDS[0],
        ).ingest_batch_snapshots([INSTRUMENT, OTHER])
    assert store.append_many_calls == 1
    assert store.observations == []


@pytest.mark.asyncio
async def test_successful_stage_order_is_provider_clock_uuid_store() -> None:
    trace: list[str] = []

    class OrderedProvider(StubProvider):
        async def get_snapshot(self, instrument: Instrument) -> MarketSnapshot:
            trace.append("provider-return")
            return snapshot(instrument)

    class OrderedClock:
        def now(self) -> datetime:
            trace.append("clock")
            return OBSERVED_AT

    class OrderedStore(SpyStore):
        def append(self, value: ObservedMarketData) -> None:
            trace.append("store")
            super().append(value)

    def next_id() -> UUID:
        trace.append("uuid")
        return IDS[0]

    store = OrderedStore()
    service = MarketDataIngestionCoordinator(
        OrderedProvider(),
        cast(ObservationStore, store),
        SOURCE,
        clock=OrderedClock(),
        observation_id_factory=next_id,
    )
    await service.ingest_snapshot(INSTRUMENT)
    assert trace == ["provider-return", "clock", "uuid", "store"]


@pytest.mark.asyncio
async def test_bars_preserve_event_time_order_instrument_and_timeframe() -> None:
    bars = [bar(EVENT_TIME), bar(EVENT_TIME + timedelta(minutes=1))]
    provider = StubProvider(bars_value=bars)
    store = SpyStore()
    clock = FixedClock()
    result = await coordinator(provider, store, clock=clock).ingest_bars(request())
    assert provider.bar_calls == 1
    assert clock.calls == 1
    assert store.append_many_calls == 1
    assert tuple(item.payload for item in result.observations) == tuple(bars)
    assert tuple(item.event_time for item in result.observations) == tuple(
        item.timestamp for item in bars
    )
    assert all(item.observed_at == OBSERVED_AT for item in result.observations)


@pytest.mark.asyncio
async def test_empty_bars_return_empty_result_without_store_call() -> None:
    store = SpyStore()
    result = await coordinator(StubProvider(bars_value=[]), store).ingest_bars(request())
    assert result.count == 0
    assert result.observed_at == OBSERVED_AT
    assert store.append_many_calls == 0


@pytest.mark.asyncio
async def test_malformed_bar_item_is_rejected_before_write() -> None:
    provider = RuntimeViolationProvider(bars_value=cast(list[MarketBar], [object()]))
    store = SpyStore()
    with pytest.raises(MarketDataUnavailableError):
        await coordinator(provider, store).ingest_bars(request())
    assert store.append_many_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_bars",
    [
        [bar(EVENT_TIME + timedelta(minutes=1)), bar(EVENT_TIME)],
        [bar(EVENT_TIME), bar(EVENT_TIME)],
        [bar(EVENT_TIME - timedelta(microseconds=1))],
        [bar(EVENT_TIME + timedelta(hours=1))],
    ],
)
async def test_runtime_provider_cannot_bypass_frozen_bar_order_and_range_contract(
    invalid_bars: list[MarketBar],
) -> None:
    store = SpyStore()
    provider = RuntimeViolationProvider(bars_value=invalid_bars)
    with pytest.raises(MarketDataUnavailableError):
        await coordinator(provider, store).ingest_bars(request())
    assert store.append_many_calls == 0


@pytest.mark.asyncio
async def test_provider_failure_prevents_batch_clock_and_store_calls() -> None:
    clock = FixedClock()
    store = SpyStore()
    with pytest.raises(ProviderRateLimitError):
        await coordinator(
            StubProvider(error=ProviderRateLimitError("limited")), store, clock=clock
        ).ingest_batch_snapshots([INSTRUMENT])
    assert clock.calls == 0
    assert store.append_many_calls == 0


@pytest.mark.asyncio
async def test_repeated_acquisition_retains_distinct_truthful_observations() -> None:
    payload = snapshot()
    store = SpyStore()
    clock = FixedClock(OBSERVED_AT, OBSERVED_AT + timedelta(minutes=5))
    service = coordinator(StubProvider(snapshot_value=payload), store, clock=clock)
    first = await service.ingest_snapshot(INSTRUMENT)
    second = await service.ingest_snapshot(INSTRUMENT)
    assert len(store.observations) == 2
    assert first.observations[0].payload == second.observations[0].payload
    assert first.observations[0].event_time == second.observations[0].event_time
    assert first.observations[0].observation_id != second.observations[0].observation_id
    assert first.observed_at != second.observed_at


@pytest.mark.asyncio
async def test_equal_receipt_times_still_preserve_distinct_observations() -> None:
    store = SpyStore()
    service = coordinator(StubProvider(), store, clock=FixedClock(OBSERVED_AT, OBSERVED_AT))
    await service.ingest_snapshot(INSTRUMENT)
    await service.ingest_snapshot(INSTRUMENT)
    assert len(store.observations) == 2
    assert store.observations[0].observation_id != store.observations[1].observation_id


def observation(
    *, source: SourceIdentity = SOURCE, at: datetime = OBSERVED_AT
) -> ObservedMarketData:
    return ObservedMarketData(
        observation_id=IDS[0], payload=snapshot(), observed_at=at, source=source
    )


def test_result_contract_is_immutable_derived_and_rejects_inconsistent_contents() -> None:
    value = IngestionResult(source=SOURCE, observed_at=OBSERVED_AT, observations=(observation(),))
    assert value.count == 1
    with pytest.raises(ValidationError, match="frozen"):
        setattr(value, "source", SourceIdentity(name="other"))
    with pytest.raises(ValidationError, match="source"):
        IngestionResult(
            source=SOURCE,
            observed_at=OBSERVED_AT,
            observations=(observation(source=SourceIdentity(name="other")),),
        )
    with pytest.raises(ValidationError, match="observation time"):
        IngestionResult(
            source=SOURCE,
            observed_at=OBSERVED_AT,
            observations=(observation(at=OBSERVED_AT + timedelta(seconds=1)),),
        )


def test_result_contract_rejects_extras_and_malformed_nested_values() -> None:
    with pytest.raises(ValidationError):
        IngestionResult.model_validate(
            {
                "source": SOURCE,
                "observed_at": OBSERVED_AT,
                "observations": [object()],
                "score": 1,
            }
        )


def test_system_clock_returns_aware_utc() -> None:
    value = SystemUtcClock().now()
    assert value.tzinfo is UTC
    assert value.utcoffset() == timedelta(0)


def test_production_module_respects_architecture_boundary() -> None:
    source = Path("app/data/ingestion.py").read_text(encoding="utf-8").lower()
    forbidden = (
        "alpaca", "httpx", "sqlite", "replay", "app.technical", "app.evidence",
        "app.setups", "app.scanner", "app.desks", "app.risk", "app.portfolio",
        "app.alerts", "app.orchestration", "openai", "anthropic", "websocket",
    )
    assert all(token not in source for token in forbidden)
    assert source.count("datetime.now(") == 1
