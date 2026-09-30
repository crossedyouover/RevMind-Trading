"""Append-only durable storage and PIT materialization for global events."""

import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Protocol
from uuid import UUID

from pydantic import ValidationError

from app.intelligence.events.engine import DeterministicGlobalEventMaterializationEngine
from app.intelligence.events.models import (
    GlobalEventMaterializationRequest,
    MaterializedGlobalEventHistory,
    ObservedGlobalEvent,
)

DATABASE_SCHEMA_VERSION = 1
DOMAIN_SCHEMA_VERSION = 1
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

_CREATE_TABLE_SQL = """
CREATE TABLE global_events (
    observation_id TEXT PRIMARY KEY NOT NULL,
    event_json TEXT NOT NULL,
    observed_at_utc_us INTEGER NOT NULL,
    source_name TEXT NOT NULL CHECK (length(trim(source_name)) > 0),
    source_event_id TEXT,
    source_revision_id TEXT,
    domain_schema_version INTEGER NOT NULL CHECK (domain_schema_version >= 1),
    CHECK (source_event_id IS NULL OR length(trim(source_event_id)) > 0),
    CHECK (source_revision_id IS NULL OR length(trim(source_revision_id)) > 0)
) STRICT
"""
_CREATE_INDEX_SQL = """
CREATE INDEX idx_global_events_available
ON global_events (observed_at_utc_us, observation_id)
"""
_INSERT_SQL = """
INSERT INTO global_events (
    observation_id, event_json, observed_at_utc_us, source_name,
    source_event_id, source_revision_id, domain_schema_version
) VALUES (?, ?, ?, ?, ?, ?, ?)
"""
_SELECT_COLUMNS = """
observation_id, event_json, observed_at_utc_us, source_name,
source_event_id, source_revision_id, domain_schema_version
"""

type _StoredEvent = tuple[str, str, int, str, str | None, str | None, int]


class GlobalEventStoreError(Exception):
    """Base exception for durable global-event storage."""


class GlobalEventConflictError(GlobalEventStoreError):
    """Raised when an observation identity already exists."""


class GlobalEventCorruptionError(GlobalEventStoreError):
    """Raised when durable data or schema cannot be trusted."""


class GlobalEventStoreUnavailableError(GlobalEventStoreError):
    """Raised when SQLite cannot safely complete an operation."""


class GlobalEventStore(Protocol):
    def append(self, event: ObservedGlobalEvent) -> None: ...

    def append_many(self, events: Iterable[ObservedGlobalEvent]) -> None: ...

    def get(self, observation_id: UUID) -> ObservedGlobalEvent | None: ...

    def available_at(self, as_of: datetime) -> tuple[ObservedGlobalEvent, ...]: ...

    def materialize(
        self, request: GlobalEventMaterializationRequest
    ) -> MaterializedGlobalEventHistory: ...


def _to_utc_microseconds(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include timezone information")
    delta = value.astimezone(UTC) - _EPOCH
    return ((delta.days * 86_400 + delta.seconds) * 1_000_000) + delta.microseconds


def _project(event: ObservedGlobalEvent) -> _StoredEvent:
    if not isinstance(event, ObservedGlobalEvent):
        raise TypeError("event must be ObservedGlobalEvent")
    trusted = ObservedGlobalEvent.model_validate(
        event.model_dump(mode="python", round_trip=True, warnings="none")
    )
    return (
        str(trusted.observation_id),
        trusted.model_dump_json(),
        _to_utc_microseconds(trusted.observed_at),
        trusted.source.name,
        trusted.source_event_id,
        trusted.source_revision_id,
        DOMAIN_SCHEMA_VERSION,
    )


class SQLiteGlobalEventStore:
    """File-backed append-only store with deterministic point-in-time reads."""

    def __init__(self, path: Path) -> None:
        if not isinstance(path, Path):
            raise TypeError("database path must be pathlib.Path")
        if str(path) == ":memory:":
            raise GlobalEventStoreUnavailableError("database path must be file-backed")
        if path.exists() and path.is_dir():
            raise GlobalEventStoreUnavailableError("database path must not be a directory")
        if not path.parent.is_dir():
            raise GlobalEventStoreUnavailableError("database parent directory does not exist")
        self._connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(str(path), timeout=5.0, isolation_level=None, uri=False)
            connection.row_factory = sqlite3.Row
            self._connection = connection
            self._initialize_or_validate_schema()
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
        except GlobalEventStoreError:
            self.close()
            raise
        except sqlite3.Error as exc:
            self.close()
            raise GlobalEventStoreUnavailableError("unable to open global-event store") from exc

    def __enter__(self) -> "SQLiteGlobalEventStore":
        self._require_connection()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        connection = self._connection
        self._connection = None
        if connection is not None:
            connection.close()

    def append(self, event: ObservedGlobalEvent) -> None:
        self._append_projected([_project(event)])

    def append_many(self, events: Iterable[ObservedGlobalEvent]) -> None:
        projected = [_project(event) for event in events]
        if not projected:
            self._require_connection()
            return
        self._append_projected(projected)

    def get(self, observation_id: UUID) -> ObservedGlobalEvent | None:
        if not isinstance(observation_id, UUID):
            raise TypeError("observation_id must be UUID")
        connection = self._require_connection()
        try:
            row = connection.execute(
                f"SELECT {_SELECT_COLUMNS} FROM global_events WHERE observation_id = ?",
                (str(observation_id),),
            ).fetchone()
        except sqlite3.Error as exc:
            raise GlobalEventStoreUnavailableError("unable to read global-event store") from exc
        return None if row is None else self._decode(row)

    def available_at(self, as_of: datetime) -> tuple[ObservedGlobalEvent, ...]:
        cutoff = _to_utc_microseconds(as_of)
        connection = self._require_connection()
        try:
            rows = connection.execute(
                f"""
                SELECT {_SELECT_COLUMNS}
                FROM global_events
                WHERE observed_at_utc_us <= ?
                ORDER BY observed_at_utc_us ASC, observation_id ASC
                """,
                (cutoff,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise GlobalEventStoreUnavailableError("unable to query global-event store") from exc
        return tuple(self._decode(row) for row in rows)

    def materialize(
        self, request: GlobalEventMaterializationRequest
    ) -> MaterializedGlobalEventHistory:
        if not isinstance(request, GlobalEventMaterializationRequest):
            raise TypeError("request must be GlobalEventMaterializationRequest")
        return DeterministicGlobalEventMaterializationEngine().materialize(
            self.available_at(request.as_of), request
        )

    def _initialize_or_validate_schema(self) -> None:
        connection = self._require_connection()
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        objects = connection.execute(
            """
            SELECT type, name, sql FROM sqlite_master
            WHERE type IN ('table', 'index', 'view', 'trigger')
              AND name NOT LIKE 'sqlite_%'
            """
        ).fetchall()
        if version == 0 and not objects:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(_CREATE_TABLE_SQL)
                connection.execute(_CREATE_INDEX_SQL)
                connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}")
                connection.execute("COMMIT")
            except sqlite3.Error as exc:
                self._rollback_quietly(connection)
                raise GlobalEventStoreUnavailableError(
                    "unable to initialize global-event store"
                ) from exc
            return
        if version != DATABASE_SCHEMA_VERSION:
            raise GlobalEventCorruptionError("unsupported global-event schema version")
        actual = {(str(row[0]), str(row[1])) for row in objects}
        if actual != {
            ("table", "global_events"),
            ("index", "idx_global_events_available"),
        }:
            raise GlobalEventCorruptionError("global-event schema objects are malformed")
        table_sql = next(row[2] for row in objects if row[1] == "global_events")
        index_sql = next(row[2] for row in objects if row[1] == "idx_global_events_available")
        if self._normalized_sql(table_sql) != self._normalized_sql(_CREATE_TABLE_SQL):
            raise GlobalEventCorruptionError("global-event table definition is malformed")
        if self._normalized_sql(index_sql) != self._normalized_sql(_CREATE_INDEX_SQL):
            raise GlobalEventCorruptionError("global-event index definition is malformed")
        tables = connection.execute("PRAGMA table_list").fetchall()
        matches = [row for row in tables if row[1] == "global_events"]
        if len(matches) != 1 or int(matches[0][5]) != 1:
            raise GlobalEventCorruptionError("global-event table must be STRICT")

    def _append_projected(self, projected: list[_StoredEvent]) -> None:
        connection = self._require_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.executemany(_INSERT_SQL, projected)
            connection.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            self._rollback_quietly(connection)
            if exc.sqlite_errorcode in {
                sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY,
                sqlite3.SQLITE_CONSTRAINT_UNIQUE,
            }:
                raise GlobalEventConflictError("observation ID already exists") from exc
            raise GlobalEventStoreError("global event violates store integrity") from exc
        except sqlite3.Error as exc:
            self._rollback_quietly(connection)
            raise GlobalEventStoreUnavailableError("unable to append global events") from exc

    @staticmethod
    def _decode(row: sqlite3.Row) -> ObservedGlobalEvent:
        try:
            if int(row["domain_schema_version"]) != DOMAIN_SCHEMA_VERSION:
                raise GlobalEventCorruptionError("unsupported event domain schema version")
            event = ObservedGlobalEvent.model_validate_json(row["event_json"])
            matches = (
                row["observation_id"] == str(event.observation_id)
                and row["observed_at_utc_us"] == _to_utc_microseconds(event.observed_at)
                and row["source_name"] == event.source.name
                and row["source_event_id"] == event.source_event_id
                and row["source_revision_id"] == event.source_revision_id
            )
        except GlobalEventCorruptionError:
            raise
        except (ValidationError, KeyError, TypeError, ValueError, IndexError) as exc:
            raise GlobalEventCorruptionError("invalid canonical global-event row") from exc
        if not matches:
            raise GlobalEventCorruptionError("canonical global-event projections disagree")
        return event

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise GlobalEventStoreUnavailableError("global-event store is closed")
        return self._connection

    @staticmethod
    def _normalized_sql(sql: object) -> str:
        return "" if not isinstance(sql, str) else " ".join(sql.split()).rstrip(";").upper()

    @staticmethod
    def _rollback_quietly(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass
