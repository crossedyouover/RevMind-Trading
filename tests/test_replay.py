"""Adversarial tests for deterministic point-in-time historical replay."""

import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.core.schemas import AssetClass, Instrument, MarketBar, Timeframe
from app.data.observation_store import SQLiteObservationStore
from app.data.observations import ObservedMarketData, SourceIdentity
from app.data.replay import (
    MAX_REPLAY_LIMIT,
    ReplayCorruptionError,
    ReplayCursor,
    ReplayInvalidRequestError,
    ReplayUnavailableError,
    SQLiteHistoricalObservationReader,
)


def _bar(timestamp: datetime) -> MarketBar:
    return MarketBar(
        instrument=Instrument(
            symbol="NVDA", asset_class=AssetClass.EQUITY, exchange="XNAS", currency="USD"
        ),
        timeframe=Timeframe.FIVE_MINUTES,
        timestamp=timestamp,
        open=Decimal("100"),
        high=Decimal("102"),
        low=Decimal("99"),
        close=Decimal("101"),
        volume=Decimal("1000"),
    )


def _obs(
    *, event: datetime, observed: datetime, uid: UUID, source: str = "a", record: str = "same"
) -> ObservedMarketData:
    return ObservedMarketData.model_validate(
        {
            "observation_id": uid,
            "payload": _bar(event),
            "observed_at": observed,
            "source": SourceIdentity(name=source),
            "source_record_id": record,
        }
    )


def _ids(batch: object) -> list[UUID]:
    return [item.observation_id for item in batch.observations]  # type: ignore[attr-defined]


def test_knowledge_time_not_event_time_controls_visibility(tmp_path: Path) -> None:
    path = tmp_path / "store.db"
    a = _obs(
        event=datetime(2026, 1, 1, 10, tzinfo=UTC),
        observed=datetime(2026, 1, 1, 10, 5, tzinfo=UTC),
        uid=UUID("00000000-0000-4000-8000-000000000001"),
    )
    b = _obs(
        event=datetime(2026, 1, 1, 10, 2, tzinfo=UTC),
        observed=datetime(2026, 1, 1, 10, 3, tzinfo=UTC),
        uid=UUID("00000000-0000-4000-8000-000000000002"),
    )
    with SQLiteObservationStore(path) as store:
        store.append_many([a, b])
    with SQLiteHistoricalObservationReader(path) as reader:
        batch = reader.read_batch(as_of=datetime(2026, 1, 1, 10, 4, tzinfo=UTC))
    assert _ids(batch) == [b.observation_id]


def test_order_is_observed_at_then_uuid_not_insertion_order(tmp_path: Path) -> None:
    path = tmp_path / "store.db"
    when = datetime(2026, 1, 1, 10, tzinfo=UTC)
    low = _obs(event=when, observed=when, uid=UUID("00000000-0000-4000-8000-000000000001"))
    high = _obs(
        event=when - timedelta(hours=1),
        observed=when,
        uid=UUID("ffffffff-ffff-4fff-bfff-ffffffffffff"),
    )
    with SQLiteObservationStore(path) as store:
        store.append_many([high, low])
    with SQLiteHistoricalObservationReader(path) as reader:
        assert _ids(reader.read_batch(as_of=when)) == [low.observation_id, high.observation_id]


def test_cursor_pagination_has_no_gaps_or_duplicates(tmp_path: Path) -> None:
    path = tmp_path / "store.db"
    when = datetime(2026, 1, 1, 10, tzinfo=UTC)
    observations = [
        _obs(event=when, observed=when, uid=UUID(f"00000000-0000-4000-8000-{i:012d}"))
        for i in range(1, 6)
    ]
    with SQLiteObservationStore(path) as store:
        store.append_many(list(reversed(observations)))
    with SQLiteHistoricalObservationReader(path) as reader:
        first = reader.read_batch(as_of=when, limit=2)
        second = reader.read_batch(as_of=when, after=first.next_cursor, limit=2)
        third = reader.read_batch(as_of=when, after=second.next_cursor, limit=2)
    assert _ids(first) + _ids(second) + _ids(third) == [o.observation_id for o in observations]
    assert not first.exhausted and not second.exhausted and third.exhausted
    assert third.next_cursor is None


def test_exact_cutoff_included_future_microsecond_excluded(tmp_path: Path) -> None:
    path = tmp_path / "store.db"
    cutoff = datetime(2026, 1, 1, 10, tzinfo=UTC)
    exact = _obs(event=cutoff, observed=cutoff, uid=UUID("00000000-0000-4000-8000-000000000001"))
    future = _obs(
        event=cutoff - timedelta(hours=1),
        observed=cutoff + timedelta(microseconds=1),
        uid=UUID("00000000-0000-4000-8000-000000000002"),
    )
    with SQLiteObservationStore(path) as store:
        store.append_many([future, exact])
    with SQLiteHistoricalObservationReader(path) as reader:
        assert _ids(reader.read_batch(as_of=cutoff)) == [exact.observation_id]


def test_fixed_snapshot_does_not_see_later_commit_but_new_reader_does(tmp_path: Path) -> None:
    path = tmp_path / "store.db"
    when = datetime(2026, 1, 1, 10, tzinfo=UTC)
    first = _obs(event=when, observed=when, uid=UUID("00000000-0000-4000-8000-000000000001"))
    later = _obs(event=when, observed=when, uid=UUID("00000000-0000-4000-8000-000000000002"))
    with SQLiteObservationStore(path) as store:
        store.append(first)
    with SQLiteHistoricalObservationReader(path) as reader:
        assert _ids(reader.read_batch(as_of=when)) == [first.observation_id]
        with SQLiteObservationStore(path) as writer:
            writer.append(later)
        assert _ids(reader.read_batch(as_of=when)) == [first.observation_id]
    with SQLiteHistoricalObservationReader(path) as reopened:
        assert _ids(reopened.read_batch(as_of=when)) == [first.observation_id, later.observation_id]


def test_repeated_record_multi_source_and_corrections_remain_distinct(tmp_path: Path) -> None:
    path = tmp_path / "store.db"
    when = datetime(2026, 1, 1, 10, tzinfo=UTC)
    observations = [
        _obs(
            event=when,
            observed=when,
            uid=UUID(f"00000000-0000-4000-8000-{i:012d}"),
            source=("a" if i < 3 else "b"),
        )
        for i in range(1, 4)
    ]
    with SQLiteObservationStore(path) as store:
        store.append_many(observations)
    with SQLiteHistoricalObservationReader(path) as reader:
        assert len(reader.read_batch(as_of=when).observations) == 3


@pytest.mark.parametrize("limit", [0, -1, MAX_REPLAY_LIMIT + 1, True])
def test_invalid_limits_rejected(tmp_path: Path, limit: object) -> None:
    path = tmp_path / "store.db"
    with SQLiteObservationStore(path):
        pass
    with SQLiteHistoricalObservationReader(path) as reader:
        with pytest.raises(ReplayInvalidRequestError):
            reader.read_batch(as_of=datetime.now(UTC), limit=limit)  # type: ignore[arg-type]


def test_naive_as_of_rejected_and_offset_as_of_normalized(tmp_path: Path) -> None:
    path = tmp_path / "store.db"
    when = datetime(2026, 1, 1, 10, tzinfo=UTC)
    observation = _obs(event=when, observed=when, uid=UUID("00000000-0000-4000-8000-000000000001"))
    with SQLiteObservationStore(path) as store:
        store.append(observation)
    with SQLiteHistoricalObservationReader(path) as reader:
        with pytest.raises(ReplayInvalidRequestError):
            reader.read_batch(as_of=datetime(2026, 1, 1, 10))
        offset = datetime(2026, 1, 1, 11, tzinfo=timezone(timedelta(hours=1)))
        assert _ids(reader.read_batch(as_of=offset)) == [observation.observation_id]


def test_cursor_model_rejects_naive_time() -> None:
    with pytest.raises(ValidationError):
        ReplayCursor(
            observed_at=datetime(2026, 1, 1),
            observation_id=UUID("00000000-0000-4000-8000-000000000001"),
        )


def test_close_is_idempotent_and_reads_after_close_fail(tmp_path: Path) -> None:
    path = tmp_path / "store.db"
    with SQLiteObservationStore(path):
        pass
    reader = SQLiteHistoricalObservationReader(path)
    reader.close()
    reader.close()
    with pytest.raises(ReplayUnavailableError):
        reader.read_batch(as_of=datetime(2026, 1, 1, tzinfo=UTC))


def test_missing_or_wrong_schema_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(ReplayUnavailableError):
        SQLiteHistoricalObservationReader(tmp_path / "missing.db")
    path = tmp_path / "wrong.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE wrong (id INTEGER)")
        connection.execute("PRAGMA user_version = 1")
    with pytest.raises(ReplayCorruptionError):
        SQLiteHistoricalObservationReader(path)
