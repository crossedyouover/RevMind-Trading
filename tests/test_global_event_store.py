"""Adversarial tests for append-only global-event persistence and PIT reads."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest

from app.data.observations import SourceIdentity
from app.intelligence.events import (
    GlobalEventCategory,
    GlobalEventConflictError,
    GlobalEventCorruptionError,
    GlobalEventMaterializationRequest,
    GlobalEventStoreUnavailableError,
    ObservedGlobalEvent,
    SQLiteGlobalEventStore,
)

KNOWN = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
SOURCE = SourceIdentity(name="USGS Earthquake Hazards Program")
OTHER = SourceIdentity(name="Other official source")


def event(
    *,
    observation_id: UUID | None = None,
    observed_at: datetime = KNOWN,
    source: SourceIdentity = SOURCE,
    source_event_id: str | None = "us7000test",
    revision: str | None = "1759233000000",
    headline: str = "M 4.2 - Test Region",
) -> ObservedGlobalEvent:
    values: dict[str, object] = {
        "observation_id": observation_id or uuid4(),
        "observed_at": observed_at,
        "source": source,
        "category": GlobalEventCategory.NATURAL_DISASTER,
        "headline": headline,
        "occurred_at": KNOWN - timedelta(minutes=10),
        "published_at": KNOWN - timedelta(minutes=5),
        "source_event_id": source_event_id,
        "source_revision_id": revision,
        "source_url": "https://earthquake.usgs.gov/earthquakes/eventpage/us7000test",
    }
    return ObservedGlobalEvent.model_validate(values)


def mutate(path: Path, sql: str, parameters: tuple[object, ...] = ()) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(sql, parameters)


def test_append_get_and_reopen_preserve_exact_canonical_event(tmp_path: Path) -> None:
    path = tmp_path / "events.db"
    original = event()
    with SQLiteGlobalEventStore(path) as store:
        store.append(original)
        assert store.get(original.observation_id) == original
    with SQLiteGlobalEventStore(path) as reopened:
        assert reopened.get(original.observation_id) == original


def test_empty_batch_and_missing_identity_are_honest(tmp_path: Path) -> None:
    with SQLiteGlobalEventStore(tmp_path / "events.db") as store:
        store.append_many(())
        assert store.get(uuid4()) is None
        assert store.available_at(KNOWN) == ()


def test_append_many_is_atomic_when_duplicate_exists(tmp_path: Path) -> None:
    existing = event()
    new = event(source_event_id="new", revision="2")
    duplicate = event(observation_id=existing.observation_id, source_event_id="other", revision="3")
    with SQLiteGlobalEventStore(tmp_path / "events.db") as store:
        store.append(existing)
        with pytest.raises(GlobalEventConflictError):
            store.append_many((new, duplicate))
        assert store.available_at(KNOWN) == (existing,)


def test_duplicate_inside_new_batch_rolls_back_every_row(tmp_path: Path) -> None:
    repeated = uuid4()
    first = event(observation_id=repeated)
    second = event(observation_id=repeated, headline="Correction")
    with SQLiteGlobalEventStore(tmp_path / "events.db") as store:
        with pytest.raises(GlobalEventConflictError):
            store.append_many((first, second))
        assert store.available_at(KNOWN) == ()


def test_preparation_failure_happens_before_batch_write(tmp_path: Path) -> None:
    good = event()
    supplied = cast(tuple[ObservedGlobalEvent, ...], (good, object()))
    with SQLiteGlobalEventStore(tmp_path / "events.db") as store:
        with pytest.raises(TypeError, match="ObservedGlobalEvent"):
            store.append_many(supplied)
        assert store.available_at(KNOWN) == ()


def test_available_at_uses_knowledge_time_and_canonical_tie_order(tmp_path: Path) -> None:
    low = UUID("00000000-0000-4000-8000-000000000001")
    high = UUID("ffffffff-ffff-4fff-bfff-ffffffffffff")
    early = event(observed_at=KNOWN - timedelta(seconds=1), source_event_id="early")
    tied_high = event(observation_id=high, source_event_id="high")
    tied_low = event(observation_id=low, source_event_id="low")
    future = event(observed_at=KNOWN + timedelta(microseconds=1), source_event_id="future")
    with SQLiteGlobalEventStore(tmp_path / "events.db") as store:
        store.append_many((future, tied_high, early, tied_low))
        assert store.available_at(KNOWN) == (early, tied_low, tied_high)


def test_materialize_enforces_as_of_and_latest_known_revision(tmp_path: Path) -> None:
    first = event(observed_at=KNOWN - timedelta(minutes=2), revision="1", headline="Initial")
    correction = event(observed_at=KNOWN - timedelta(minutes=1), revision="2", headline="Corrected")
    future = event(observed_at=KNOWN + timedelta(minutes=1), revision="3", headline="Future")
    request = GlobalEventMaterializationRequest(as_of=KNOWN, source=SOURCE)
    with SQLiteGlobalEventStore(tmp_path / "events.db") as store:
        store.append_many((future, correction, first))
        result = store.materialize(request)
    assert result.events == (correction,)
    assert result.inspected_event_count == 2
    assert result.eligible_event_count == 2


def test_materialization_keeps_other_source_in_inspected_count_only(tmp_path: Path) -> None:
    selected = event(source_event_id="selected")
    other = event(source=OTHER, source_event_id="other", revision="x")
    with SQLiteGlobalEventStore(tmp_path / "events.db") as store:
        store.append_many((other, selected))
        result = store.materialize(GlobalEventMaterializationRequest(as_of=KNOWN, source=SOURCE))
    assert result.events == (selected,)
    assert result.inspected_event_count == 2
    assert result.eligible_event_count == 1


def test_naive_cutoff_and_closed_store_fail_closed(tmp_path: Path) -> None:
    store = SQLiteGlobalEventStore(tmp_path / "events.db")
    with pytest.raises(ValueError, match="timezone"):
        store.available_at(datetime(2026, 9, 30, 12, 0))
    store.close()
    with pytest.raises(GlobalEventStoreUnavailableError, match="closed"):
        store.available_at(KNOWN)


@pytest.mark.parametrize(
    "path_factory, message",
    [
        (lambda root: root / "missing" / "events.db", "parent"),
        (lambda root: root, "directory"),
    ],
)
def test_invalid_database_paths_fail_closed(
    tmp_path: Path, path_factory: object, message: str
) -> None:
    factory = cast(object, path_factory)
    path = factory(tmp_path)  # type: ignore[operator]
    with pytest.raises(GlobalEventStoreUnavailableError, match=message):
        SQLiteGlobalEventStore(path)


def test_unsupported_schema_and_extra_objects_are_rejected(tmp_path: Path) -> None:
    wrong_version = tmp_path / "wrong.db"
    with sqlite3.connect(wrong_version) as connection:
        connection.execute("PRAGMA user_version = 999")
    with pytest.raises(GlobalEventCorruptionError, match="unsupported"):
        SQLiteGlobalEventStore(wrong_version)

    path = tmp_path / "extra.db"
    with SQLiteGlobalEventStore(path):
        pass
    mutate(path, "CREATE TABLE injected (value TEXT)")
    with pytest.raises(GlobalEventCorruptionError, match="objects"):
        SQLiteGlobalEventStore(path)


@pytest.mark.parametrize(
    "sql, parameters",
    [
        (
            "UPDATE global_events SET source_name = ? WHERE observation_id = ?",
            ("tampered", "{id}"),
        ),
        (
            "UPDATE global_events SET event_json = ? WHERE observation_id = ?",
            ("not-json", "{id}"),
        ),
        (
            "UPDATE global_events SET domain_schema_version = 2 WHERE observation_id = ?",
            ("{id}",),
        ),
    ],
)
def test_projection_json_and_domain_version_corruption_fail_closed(
    tmp_path: Path, sql: str, parameters: tuple[object, ...]
) -> None:
    path = tmp_path / "events.db"
    original = event()
    with SQLiteGlobalEventStore(path) as store:
        store.append(original)
    identity = str(original.observation_id)
    bound = tuple(identity if value == "{id}" else value for value in parameters)
    mutate(path, sql, bound)
    with SQLiteGlobalEventStore(path) as store:
        with pytest.raises(GlobalEventCorruptionError):
            store.get(original.observation_id)
