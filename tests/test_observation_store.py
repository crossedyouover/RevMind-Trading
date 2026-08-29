"""Adversarial tests for the durable append-only observation store."""

import json
import socket
import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

import app.data.observation_store as store_module
from app.core.schemas import AssetClass, Instrument, MarketBar, MarketSnapshot, Timeframe
from app.data.observation_store import (
    ObservationConflictError,
    ObservationCorruptionError,
    ObservationStoreUnavailableError,
    SQLiteObservationStore,
)
from app.data.observations import ObservedMarketData, SourceIdentity


def _instrument(*, exchange: str = "XNAS", currency: str = "USD") -> Instrument:
    return Instrument(
        symbol="NVDA", asset_class=AssetClass.EQUITY, exchange=exchange, currency=currency
    )


def _bar(
    *,
    timestamp: datetime = datetime(2026, 1, 2, 10, 0, tzinfo=UTC),
    close: Decimal = Decimal("123.456789012345678901"),
) -> MarketBar:
    return MarketBar(
        instrument=_instrument(),
        timeframe=Timeframe.FIVE_MINUTES,
        timestamp=timestamp,
        open=Decimal("123.1"),
        high=Decimal("124.0"),
        low=Decimal("122.9"),
        close=close,
        volume=Decimal("987654321.123456789"),
    )


def _snapshot(
    *, timestamp: datetime = datetime(2026, 1, 2, 10, 0, tzinfo=UTC)
) -> MarketSnapshot:
    return MarketSnapshot(
        instrument=_instrument(exchange="ARCX", currency="EUR"),
        timestamp=timestamp,
        last_price=Decimal("123.456789012345678901"),
        day_volume=Decimal("999.000000000000001"),
        percent_change=Decimal("-0.000000000000001"),
    )


def _observation(
    *,
    payload: MarketBar | MarketSnapshot | None = None,
    observed_at: datetime = datetime(2026, 1, 2, 10, 5, tzinfo=UTC),
    source: str = "provider-a",
    source_record_id: str | None = "record-1",
    observation_id: UUID | None = None,
) -> ObservedMarketData:
    values: dict[str, object] = {
        "payload": payload or _bar(),
        "observed_at": observed_at,
        "source": SourceIdentity(name=source),
        "source_record_id": source_record_id,
    }
    if observation_id is not None:
        values["observation_id"] = observation_id
    return ObservedMarketData.model_validate(values)


def _mutate(path: Path, sql: str, parameters: tuple[object, ...] = ()) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(sql, parameters)


def test_append_and_get_market_bar_preserves_full_canonical_model(tmp_path: Path) -> None:
    observation = _observation()
    with SQLiteObservationStore(tmp_path / "store.db") as store:
        store.append(observation)
        restored = store.get(observation.observation_id)
    assert restored == observation
    assert restored is not None
    assert isinstance(restored.payload, MarketBar)
    assert restored.payload.close == Decimal("123.456789012345678901")
    assert restored.payload.volume == Decimal("987654321.123456789")
    assert restored.payload.timeframe is Timeframe.FIVE_MINUTES
    assert restored.payload.instrument == _instrument()
    assert restored.observation_id.version == 4


def test_append_and_get_snapshot_preserves_type_venue_currency_and_decimals(
    tmp_path: Path,
) -> None:
    observation = _observation(payload=_snapshot(), source_record_id=None)
    with SQLiteObservationStore(tmp_path / "store.db") as store:
        store.append(observation)
        restored = store.get(observation.observation_id)
    assert restored == observation
    assert restored is not None
    assert isinstance(restored.payload, MarketSnapshot)
    assert restored.payload.instrument.exchange == "ARCX"
    assert restored.payload.instrument.currency == "EUR"
    assert restored.payload.last_price == Decimal("123.456789012345678901")
    assert restored.source_record_id is None


def test_get_missing_uuid_returns_none_and_empty_batch_is_noop(tmp_path: Path) -> None:
    with SQLiteObservationStore(tmp_path / "store.db") as store:
        store.append_many([])
        assert store.get(uuid4()) is None
        assert store.available_at(datetime.max.replace(tzinfo=UTC)) == []


def test_append_many_persists_across_repeated_reopen_cycles(tmp_path: Path) -> None:
    path = tmp_path / "store.db"
    observations = [_observation(source_record_id=None), _observation(payload=_snapshot())]
    with SQLiteObservationStore(path) as store:
        store.append_many(observations)
    for _ in range(3):
        with SQLiteObservationStore(path) as store:
            assert [store.get(item.observation_id) for item in observations] == observations


def test_history_preserves_repeated_records_payloads_providers_and_correction(
    tmp_path: Path,
) -> None:
    payload = _bar()
    observations = [
        _observation(payload=payload, source_record_id="same"),
        _observation(payload=payload, source_record_id="same"),
        _observation(payload=payload, source="provider-b", source_record_id="same"),
        _observation(payload=_bar(close=Decimal("123.5")), source_record_id="same"),
    ]
    with SQLiteObservationStore(tmp_path / "store.db") as store:
        store.append_many(observations)
        actual = store.available_at(datetime(2026, 1, 2, 10, 5, tzinfo=UTC))
    assert {item.observation_id for item in actual} == {
        item.observation_id for item in observations
    }
    assert len(actual) == 4


@pytest.mark.parametrize("same_content", [True, False])
def test_duplicate_observation_id_is_always_rejected(
    tmp_path: Path, same_content: bool
) -> None:
    first = _observation()
    second = first if same_content else _observation(
        observation_id=first.observation_id, payload=_snapshot()
    )
    with SQLiteObservationStore(tmp_path / "store.db") as store:
        store.append(first)
        with pytest.raises(ObservationConflictError):
            store.append(second)
        assert store.get(first.observation_id) == first


def test_duplicate_inside_batch_rolls_back_every_new_row(tmp_path: Path) -> None:
    existing = _observation()
    new = _observation(payload=_snapshot())
    duplicate = _observation(observation_id=existing.observation_id, source="other")
    with SQLiteObservationStore(tmp_path / "store.db") as store:
        store.append(existing)
        with pytest.raises(ObservationConflictError):
            store.append_many([new, duplicate])
        assert store.get(existing.observation_id) == existing
        assert store.get(new.observation_id) is None


def test_duplicate_ids_within_new_batch_leave_no_partial_data(tmp_path: Path) -> None:
    repeated_id = uuid4()
    first = _observation(observation_id=repeated_id)
    second = _observation(observation_id=repeated_id, payload=_snapshot())
    with SQLiteObservationStore(tmp_path / "store.db") as store:
        with pytest.raises(ObservationConflictError):
            store.append_many([first, second])
        assert store.available_at(datetime.max.replace(tzinfo=UTC)) == []


def test_late_duplicate_in_large_batch_rolls_back_all_new_rows(tmp_path: Path) -> None:
    existing = _observation()
    new_rows = [
        _observation(observed_at=datetime(2026, 1, 2, 11, minute, tzinfo=UTC))
        for minute in range(20)
    ]
    duplicate = _observation(observation_id=existing.observation_id, payload=_snapshot())
    with SQLiteObservationStore(tmp_path / "store.db") as store:
        store.append(existing)
        with pytest.raises(ObservationConflictError):
            store.append_many([*new_rows, duplicate])
        assert store.available_at(datetime.max.replace(tzinfo=UTC)) == [existing]


def test_preparation_failure_occurs_before_any_batch_write(tmp_path: Path) -> None:
    existing = _observation()
    valid_new = _observation(payload=_snapshot())
    invalid_batch = cast(list[ObservedMarketData], [valid_new, object()])
    with SQLiteObservationStore(tmp_path / "store.db") as store:
        store.append(existing)
        with pytest.raises(TypeError, match="ObservedMarketData"):
            store.append_many(invalid_batch)
        assert store.available_at(datetime.max.replace(tzinfo=UTC)) == [existing]


def test_available_at_orders_by_observed_time_then_uuid_not_insertion_or_event_time(
    tmp_path: Path,
) -> None:
    tie_low = UUID("00000000-0000-4000-8000-000000000001")
    tie_high = UUID("ffffffff-ffff-4fff-bfff-ffffffffffff")
    early = _observation(
        payload=_bar(timestamp=datetime(2026, 1, 2, 12, 0, tzinfo=UTC)),
        observed_at=datetime(2026, 1, 2, 10, 1, tzinfo=UTC),
    )
    later_low = _observation(
        payload=_bar(timestamp=datetime(2026, 1, 2, 8, 0, tzinfo=UTC)),
        observed_at=datetime(2026, 1, 2, 10, 2, tzinfo=UTC),
        observation_id=tie_low,
    )
    later_high = _observation(
        observed_at=datetime(2026, 1, 2, 10, 2, tzinfo=UTC), observation_id=tie_high
    )
    path = tmp_path / "store.db"
    with SQLiteObservationStore(path) as store:
        store.append_many([later_high, later_low, early])
        actual = store.available_at(datetime(2026, 1, 2, 10, 2, tzinfo=UTC))
    assert actual == [early, later_low, later_high]
    assert str(tie_low) < str(tie_high)
    assert tie_low.int < tie_high.int
    assert str(tie_low) == str(tie_low).lower()
    with SQLiteObservationStore(path) as store:
        assert store.available_at(datetime(2026, 1, 2, 10, 2, tzinfo=UTC)) == actual


def test_point_in_time_boundaries_and_event_time_do_not_control_visibility(
    tmp_path: Path,
) -> None:
    before = _observation(observed_at=datetime(2026, 1, 2, 10, 3, tzinfo=UTC))
    equal = _observation(
        payload=_bar(timestamp=datetime(2030, 1, 1, tzinfo=UTC)),
        observed_at=datetime(2026, 1, 2, 10, 4, tzinfo=UTC),
    )
    after = _observation(
        payload=_bar(timestamp=datetime(2020, 1, 1, tzinfo=UTC)),
        observed_at=datetime(2026, 1, 2, 10, 5, tzinfo=UTC),
    )
    with SQLiteObservationStore(tmp_path / "store.db") as store:
        store.append_many([after, equal, before])
        assert store.available_at(datetime(2026, 1, 2, 10, 4, tzinfo=UTC)) == [before, equal]


def test_mandatory_anti_lookahead_case_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "store.db"
    a = _observation(
        payload=_bar(timestamp=datetime(2026, 1, 2, 10, 0, tzinfo=UTC)),
        observed_at=datetime(2026, 1, 2, 10, 5, tzinfo=UTC),
    )
    b = _observation(
        payload=_bar(timestamp=datetime(2026, 1, 2, 10, 2, tzinfo=UTC)),
        observed_at=datetime(2026, 1, 2, 10, 3, tzinfo=UTC),
    )
    clock = datetime(2026, 1, 2, 10, 4, tzinfo=UTC)
    with SQLiteObservationStore(path) as store:
        store.append_many([a, b])
        assert store.available_at(clock) == [b]
    with SQLiteObservationStore(path) as store:
        assert store.available_at(clock) == [b]


def test_query_clock_rejects_naive_and_normalizes_offset(tmp_path: Path) -> None:
    observation = _observation(observed_at=datetime(2026, 1, 2, 10, 4, tzinfo=UTC))
    with SQLiteObservationStore(tmp_path / "store.db") as store:
        store.append(observation)
        with pytest.raises(ValueError, match="timezone"):
            store.available_at(datetime(2026, 1, 2, 10, 4))
        offset_clock = datetime(2026, 1, 2, 11, 4, tzinfo=timezone(timedelta(hours=1)))
        assert store.available_at(offset_clock) == [observation]


def test_exact_microsecond_projections_use_utc_normalization(tmp_path: Path) -> None:
    path = tmp_path / "store.db"
    event = datetime(1969, 12, 31, 23, 59, 59, 999999, tzinfo=UTC)
    observed = datetime(2026, 1, 2, 11, 4, 5, 123456, tzinfo=timezone(timedelta(hours=1)))
    observation = _observation(payload=_bar(timestamp=event), observed_at=observed)
    with SQLiteObservationStore(path) as store:
        store.append(observation)
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT event_time_utc_us, observed_at_utc_us FROM observations"
        ).fetchone()
    assert row == (-1, 1_767_348_245_123_456)


@pytest.mark.parametrize(
    ("instant", "expected"),
    [
        (datetime(1970, 1, 1, tzinfo=UTC), 0),
        (datetime(1970, 1, 1, 0, 0, 0, 1, tzinfo=UTC), 1),
        (datetime(1970, 1, 1, 0, 0, 0, 999999, tzinfo=UTC), 999_999),
        (datetime(1969, 12, 31, 23, 59, 59, 1, tzinfo=UTC), -999_999),
        (
            datetime(1970, 1, 1, 1, 0, 0, 1, tzinfo=timezone(timedelta(hours=1))),
            1,
        ),
        (
            datetime(1969, 12, 31, 19, 0, 0, 1, tzinfo=timezone(timedelta(hours=-5))),
            1,
        ),
    ],
)
def test_timestamp_projection_is_exact_across_epoch_offsets_and_microseconds(
    tmp_path: Path, instant: datetime, expected: int
) -> None:
    path = tmp_path / "store.db"
    observation = _observation(payload=_bar(timestamp=instant), observed_at=instant)
    with SQLiteObservationStore(path) as store:
        store.append(observation)
    with sqlite3.connect(path) as connection:
        projections = connection.execute(
            "SELECT event_time_utc_us, observed_at_utc_us FROM observations"
        ).fetchone()
    assert projections == (expected, expected)


def test_retrieved_observation_and_nested_payload_are_immutable(tmp_path: Path) -> None:
    observation = _observation()
    with SQLiteObservationStore(tmp_path / "store.db") as store:
        store.append(observation)
        restored = store.get(observation.observation_id)
    assert restored is not None
    with pytest.raises(ValidationError):
        restored.source.name = "changed"
    with pytest.raises(ValidationError):
        restored.payload.instrument.symbol = "AMD"


@pytest.mark.parametrize(
    ("sql", "parameters"),
    [
        ("UPDATE observations SET payload_type = 'MARKET_SNAPSHOT'", ()),
        ("UPDATE observations SET observation_id = ?", (str(uuid4()),)),
        ("UPDATE observations SET event_time_utc_us = event_time_utc_us + 1", ()),
        ("UPDATE observations SET observed_at_utc_us = observed_at_utc_us + 1", ()),
        ("UPDATE observations SET source_name = 'different'", ()),
        ("UPDATE observations SET source_record_id = 'different'", ()),
    ],
)
def test_projection_corruption_is_rejected(
    tmp_path: Path, sql: str, parameters: tuple[object, ...]
) -> None:
    path = tmp_path / "store.db"
    observation = _observation()
    with SQLiteObservationStore(path) as store:
        store.append(observation)
    _mutate(path, sql, parameters)
    with SQLiteObservationStore(path) as store:
        with pytest.raises(ObservationCorruptionError):
            store.available_at(datetime.max.replace(tzinfo=UTC))


def test_invalid_json_and_unsupported_domain_version_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "store.db"
    observation = _observation()
    with SQLiteObservationStore(path) as store:
        store.append(observation)
    _mutate(path, "UPDATE observations SET observation_json = '{bad json'")
    with SQLiteObservationStore(path) as store:
        with pytest.raises(ObservationCorruptionError, match="JSON"):
            store.get(observation.observation_id)
    _mutate(
        path,
        "UPDATE observations SET observation_json = ?, domain_schema_version = 2",
        (observation.model_dump_json(),),
    )
    with SQLiteObservationStore(path) as store:
        with pytest.raises(ObservationCorruptionError, match="domain schema"):
            store.get(observation.observation_id)


def test_unsupported_database_version_is_rejected_without_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "store.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE sentinel (value TEXT)")
        connection.execute("INSERT INTO sentinel VALUES ('keep')")
        connection.execute("PRAGMA user_version = 2")
    with pytest.raises(ObservationCorruptionError, match="schema version"):
        SQLiteObservationStore(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT value FROM sentinel").fetchone() == ("keep",)


def test_invalid_database_is_not_switched_to_wal_before_rejection(tmp_path: Path) -> None:
    path = tmp_path / "foreign.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE sentinel (value TEXT)")
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("delete",)
    with pytest.raises(ObservationCorruptionError):
        SQLiteObservationStore(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("delete",)


def test_nonexistent_and_zero_byte_files_initialize_only_as_genuinely_empty(
    tmp_path: Path,
) -> None:
    paths = [tmp_path / "new.db", tmp_path / "zero.db"]
    paths[1].touch()
    for path in paths:
        with SQLiteObservationStore(path) as store:
            assert store.available_at(datetime.max.replace(tzinfo=UTC)) == []
        with sqlite3.connect(path) as connection:
            assert connection.execute("PRAGMA user_version").fetchone() == (1,)


@pytest.mark.parametrize("extra_object", ["index", "trigger"])
def test_valid_schema_with_unapproved_user_object_is_rejected(
    tmp_path: Path, extra_object: str
) -> None:
    path = tmp_path / "store.db"
    with SQLiteObservationStore(path):
        pass
    with sqlite3.connect(path) as connection:
        if extra_object == "index":
            connection.execute(
                "CREATE INDEX unapproved_source_index ON observations (source_name)"
            )
        else:
            connection.execute(
                """
                CREATE TRIGGER destructive_trigger AFTER INSERT ON observations
                BEGIN
                    DELETE FROM observations WHERE observation_id = NEW.observation_id;
                END
                """
            )
    with pytest.raises(ObservationCorruptionError, match="objects"):
        SQLiteObservationStore(path)


def test_lookalike_schema_with_wrong_constraints_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "store.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE observations (
                observation_id TEXT PRIMARY KEY NOT NULL,
                payload_type TEXT NOT NULL,
                observation_json TEXT NOT NULL,
                event_time_utc_us INTEGER NOT NULL,
                observed_at_utc_us INTEGER NOT NULL,
                source_name TEXT NOT NULL,
                source_record_id TEXT,
                domain_schema_version INTEGER NOT NULL
            ) STRICT
            """
        )
        connection.execute(
            "CREATE INDEX idx_observations_available "
            "ON observations (observed_at_utc_us, observation_id)"
        )
        connection.execute("PRAGMA user_version = 1")
    with pytest.raises(ObservationCorruptionError, match="definition"):
        SQLiteObservationStore(path)


@pytest.mark.parametrize("malformation", ["missing", "incomplete"])
def test_missing_or_malformed_expected_table_is_rejected(
    tmp_path: Path, malformation: str
) -> None:
    path = tmp_path / "store.db"
    with sqlite3.connect(path) as connection:
        if malformation == "incomplete":
            connection.execute("CREATE TABLE observations (observation_id TEXT)")
        else:
            connection.execute("CREATE TABLE unrelated (value TEXT)")
        connection.execute("PRAGMA user_version = 1")
    with pytest.raises(ObservationCorruptionError, match="schema"):
        SQLiteObservationStore(path)


def test_sql_looking_identity_values_are_stored_only_as_data(tmp_path: Path) -> None:
    source = "x'); DROP TABLE observations; --"
    record = "1; DELETE FROM observations;"
    observation = _observation(source=source, source_record_id=record)
    with SQLiteObservationStore(tmp_path / "store.db") as store:
        store.append(observation)
        assert store.get(observation.observation_id) == observation
        assert store.available_at(datetime.max.replace(tzinfo=UTC)) == [observation]


def test_path_controls_reject_directory_missing_parent_and_connection_strings(
    tmp_path: Path,
) -> None:
    with pytest.raises(ObservationStoreUnavailableError, match="directory"):
        SQLiteObservationStore(tmp_path)
    with pytest.raises(ObservationStoreUnavailableError, match="parent"):
        SQLiteObservationStore(tmp_path / "missing" / "store.db")
    with pytest.raises(TypeError, match="Path"):
        SQLiteObservationStore(cast(Path, "file:store.db?mode=memory"))
    with pytest.raises(ObservationStoreUnavailableError, match="file-backed"):
        SQLiteObservationStore(Path(":memory:"))


def test_relative_path_remains_a_file_backed_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = Path("relative.db")
    with SQLiteObservationStore(path):
        pass
    assert path.is_file()


def test_independent_database_files_remain_isolated(tmp_path: Path) -> None:
    observation = _observation()
    with SQLiteObservationStore(tmp_path / "one.db") as one:
        one.append(observation)
    with SQLiteObservationStore(tmp_path / "two.db") as two:
        assert two.get(observation.observation_id) is None


def test_close_and_context_manager_fail_clearly_without_reopening(tmp_path: Path) -> None:
    store = SQLiteObservationStore(tmp_path / "store.db")
    store.close()
    store.close()
    with pytest.raises(ObservationStoreUnavailableError, match="closed"):
        store.get(uuid4())
    with pytest.raises(ObservationStoreUnavailableError, match="closed"):
        store.append_many([])


def test_context_manager_closes_after_body_exception(tmp_path: Path) -> None:
    store: SQLiteObservationStore | None = None
    with pytest.raises(RuntimeError, match="body failure"):
        with SQLiteObservationStore(tmp_path / "store.db") as active_store:
            store = active_store
            active_store.append(_observation())
            raise RuntimeError("body failure")
    assert store is not None
    with pytest.raises(ObservationStoreUnavailableError, match="closed"):
        store.available_at(datetime.max.replace(tzinfo=UTC))


def test_schema_pragmas_and_only_approved_index_are_configured(tmp_path: Path) -> None:
    path = tmp_path / "store.db"
    with SQLiteObservationStore(path) as store:
        connection = store._connection
        assert connection is not None
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (1,)
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='observations'"
        ).fetchone()[0]
        indexes = connection.execute("PRAGMA index_list(observations)").fetchall()
    assert table_sql.strip().endswith("STRICT")
    assert {row[1] for row in indexes} == {
        "idx_observations_available",
        "sqlite_autoindex_observations_1",
    }


def test_no_system_clock_or_network_is_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observation = _observation()

    class ClockTrap:
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            raise AssertionError("system clock used")

        @classmethod
        def utcnow(cls) -> datetime:
            raise AssertionError("system clock used")

    def network_trap(*args: object, **kwargs: object) -> None:
        raise AssertionError("network used")

    monkeypatch.setattr(store_module, "datetime", ClockTrap)
    monkeypatch.setattr(socket, "create_connection", network_trap)
    with SQLiteObservationStore(tmp_path / "store.db") as store:
        store.append(observation)
        assert store.available_at(observation.observed_at) == [observation]


def test_store_has_no_mutation_or_generic_query_api_and_domain_rejects_metadata(
    tmp_path: Path,
) -> None:
    with SQLiteObservationStore(tmp_path / "store.db") as store:
        for name in ("update", "delete", "replace", "upsert", "query"):
            assert not hasattr(store, name)
    data = _observation().model_dump()
    data["metadata"] = {"secret": "not allowed"}
    with pytest.raises(ValidationError):
        ObservedMarketData.model_validate(data)


def test_canonical_json_is_authoritative_not_projection_reconstruction(tmp_path: Path) -> None:
    path = tmp_path / "store.db"
    observation = _observation()
    with SQLiteObservationStore(path) as store:
        store.append(observation)
    changed = json.loads(observation.model_dump_json())
    changed["payload"]["close"] = "123.500"
    _mutate(path, "UPDATE observations SET observation_json = ?", (json.dumps(changed),))
    with SQLiteObservationStore(path) as store:
        restored = store.get(observation.observation_id)
    assert restored is not None
    assert isinstance(restored.payload, MarketBar)
    assert restored.payload.close == Decimal("123.500")


@pytest.mark.parametrize("corruption", ["wrong-id", "non-uuid4", "bad-payload", "extra-field"])
def test_valid_json_domain_corruption_is_rejected(
    tmp_path: Path, corruption: str
) -> None:
    path = tmp_path / "store.db"
    observation = _observation()
    with SQLiteObservationStore(path) as store:
        store.append(observation)
    changed = json.loads(observation.model_dump_json())
    if corruption == "wrong-id":
        changed["observation_id"] = str(uuid4())
    elif corruption == "non-uuid4":
        changed["observation_id"] = "00000000-0000-1000-8000-000000000001"
    elif corruption == "bad-payload":
        changed["payload"]["high"] = "1"
    else:
        changed["unexpected"] = "forbidden"
    _mutate(path, "UPDATE observations SET observation_json = ?", (json.dumps(changed),))
    with SQLiteObservationStore(path) as store:
        with pytest.raises(ObservationCorruptionError):
            store.get(observation.observation_id)


def test_decimal_json_and_reconstruction_preserve_scale_and_nonbinary_values(
    tmp_path: Path,
) -> None:
    path = tmp_path / "store.db"
    values = (
        Decimal("0.0000000000000000000000000001"),
        Decimal("9999999999999999999999999999.123400"),
    )
    observations = [
        _observation(payload=_snapshot(timestamp=datetime(2026, 1, 2, 10, i, tzinfo=UTC)))
        for i in range(2)
    ]
    observations = [
        observation.model_copy(
            update={"payload": observation.payload.model_copy(update={"last_price": value})}
        )
        for observation, value in zip(observations, values, strict=True)
    ]
    with SQLiteObservationStore(path) as store:
        store.append_many(observations)
        restored = [store.get(item.observation_id) for item in observations]
    with sqlite3.connect(path) as connection:
        serialized = dict(
            connection.execute("SELECT observation_id, observation_json FROM observations")
        )
    for expected, original, actual in zip(values, observations, restored, strict=True):
        assert actual is not None
        assert isinstance(actual.payload, MarketSnapshot)
        assert actual.payload.last_price.as_tuple() == expected.as_tuple()
        assert str(expected) in serialized[str(original.observation_id)]
