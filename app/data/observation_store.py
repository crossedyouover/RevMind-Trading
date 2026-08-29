"""Append-only durable storage for canonical market-data observations."""

import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Protocol
from uuid import UUID

from pydantic import ValidationError

from app.core.schemas import MarketBar, MarketSnapshot
from app.data.observations import ObservedMarketData

DATABASE_SCHEMA_VERSION = 1
DOMAIN_SCHEMA_VERSION = 1
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_PAYLOAD_BAR = "MARKET_BAR"
_PAYLOAD_SNAPSHOT = "MARKET_SNAPSHOT"

_CREATE_TABLE_SQL = """
CREATE TABLE observations (
    observation_id TEXT PRIMARY KEY NOT NULL,
    payload_type TEXT NOT NULL
        CHECK (payload_type IN ('MARKET_BAR', 'MARKET_SNAPSHOT')),
    observation_json TEXT NOT NULL,
    event_time_utc_us INTEGER NOT NULL,
    observed_at_utc_us INTEGER NOT NULL,
    source_name TEXT NOT NULL
        CHECK (length(trim(source_name)) > 0),
    source_record_id TEXT,
    domain_schema_version INTEGER NOT NULL
        CHECK (domain_schema_version >= 1),
    CHECK (
        source_record_id IS NULL
        OR length(trim(source_record_id)) > 0
    )
) STRICT
"""
_CREATE_INDEX_SQL = """
CREATE INDEX idx_observations_available
ON observations (observed_at_utc_us, observation_id)
"""
_INSERT_SQL = """
INSERT INTO observations (
    observation_id,
    payload_type,
    observation_json,
    event_time_utc_us,
    observed_at_utc_us,
    source_name,
    source_record_id,
    domain_schema_version
) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""
_SELECT_COLUMNS = """
observation_id,
payload_type,
observation_json,
event_time_utc_us,
observed_at_utc_us,
source_name,
source_record_id,
domain_schema_version
"""

type _StoredObservation = tuple[str, str, str, int, int, str, str | None, int]


class ObservationStoreError(Exception):
    """Base exception for observation-store failures."""


class ObservationConflictError(ObservationStoreError):
    """Raised when an observation ID already exists in the append-only store."""


class ObservationCorruptionError(ObservationStoreError):
    """Raised when stored data or schema cannot be trusted."""


class ObservationStoreUnavailableError(ObservationStoreError):
    """Raised when SQLite cannot safely complete a store operation."""


class ObservationStore(Protocol):
    """Narrow RevMind-owned contract for durable canonical observations."""

    def append(self, observation: ObservedMarketData) -> None:
        """Append one observation or reject its duplicate identity."""
        ...

    def append_many(self, observations: Iterable[ObservedMarketData]) -> None:
        """Atomically append a collection of observations."""
        ...

    def get(self, observation_id: UUID) -> ObservedMarketData | None:
        """Return one observation by identity, or ``None`` when absent."""
        ...

    def available_at(self, evaluation_clock: datetime) -> list[ObservedMarketData]:
        """Return observations known by an aware evaluation clock."""
        ...


def _to_utc_microseconds(value: datetime) -> int:
    """Convert an aware datetime to exact microseconds from the UNIX epoch."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include timezone information")
    delta = value.astimezone(UTC) - _EPOCH
    return ((delta.days * 86_400 + delta.seconds) * 1_000_000) + delta.microseconds


def _payload_type(observation: ObservedMarketData) -> str:
    """Return the closed-set storage discriminator for a canonical payload."""
    if isinstance(observation.payload, MarketBar):
        return _PAYLOAD_BAR
    if isinstance(observation.payload, MarketSnapshot):
        return _PAYLOAD_SNAPSHOT
    raise TypeError("unsupported market payload type")


def _project(observation: ObservedMarketData) -> _StoredObservation:
    """Serialize an observation and derive query/integrity projections."""
    if not isinstance(observation, ObservedMarketData):
        raise TypeError("observation must be ObservedMarketData")
    return (
        str(observation.observation_id),
        _payload_type(observation),
        observation.model_dump_json(),
        _to_utc_microseconds(observation.event_time),
        _to_utc_microseconds(observation.observed_at),
        observation.source.name,
        observation.source_record_id,
        DOMAIN_SCHEMA_VERSION,
    )


class SQLiteObservationStore:
    """SQLite-backed append-only store for ``ObservedMarketData``.

    The canonical JSON is authoritative. Other columns are query and integrity projections which
    are checked against the reconstructed immutable domain model on every read.
    """

    def __init__(self, path: Path) -> None:
        if not isinstance(path, Path):
            raise TypeError("database path must be pathlib.Path")
        if str(path) == ":memory:":
            raise ObservationStoreUnavailableError("database path must be file-backed")
        if path.exists() and path.is_dir():
            raise ObservationStoreUnavailableError("database path must not be a directory")
        if not path.parent.is_dir():
            raise ObservationStoreUnavailableError("database parent directory does not exist")

        self._path = path
        self._connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                str(path),
                timeout=5.0,
                isolation_level=None,
                uri=False,
            )
            connection.row_factory = sqlite3.Row
            self._connection = connection
            self._initialize_or_validate_schema()
            self._configure_connection()
        except ObservationStoreError:
            self.close()
            raise
        except sqlite3.Error as exc:
            self.close()
            raise ObservationStoreUnavailableError("unable to open observation store") from exc

    def __enter__(self) -> "SQLiteObservationStore":
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
        """Close the owned connection; repeated calls are harmless."""
        connection = self._connection
        self._connection = None
        if connection is not None:
            connection.close()

    def append(self, observation: ObservedMarketData) -> None:
        """Append one observation in a short explicit transaction."""
        self._append_projected([_project(observation)])

    def append_many(self, observations: Iterable[ObservedMarketData]) -> None:
        """Validate first, then atomically append the complete batch."""
        projected = [_project(observation) for observation in observations]
        if not projected:
            self._require_connection()
            return
        self._append_projected(projected)

    def get(self, observation_id: UUID) -> ObservedMarketData | None:
        """Return one fully validated canonical observation."""
        connection = self._require_connection()
        try:
            row = connection.execute(
                f"SELECT {_SELECT_COLUMNS} FROM observations WHERE observation_id = ?",
                (str(observation_id),),
            ).fetchone()
        except sqlite3.Error as exc:
            raise ObservationStoreUnavailableError("unable to read observation store") from exc
        return None if row is None else self._decode(row)

    def available_at(self, evaluation_clock: datetime) -> list[ObservedMarketData]:
        """Return observations whose knowledge time is at or before the clock."""
        cutoff = _to_utc_microseconds(evaluation_clock)
        connection = self._require_connection()
        try:
            rows = connection.execute(
                f"""
                SELECT {_SELECT_COLUMNS}
                FROM observations
                WHERE observed_at_utc_us <= ?
                ORDER BY observed_at_utc_us ASC, observation_id ASC
                """,
                (cutoff,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise ObservationStoreUnavailableError("unable to query observation store") from exc
        return [self._decode(row) for row in rows]

    def _configure_connection(self) -> None:
        connection = self._require_connection()
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")

    def _initialize_or_validate_schema(self) -> None:
        connection = self._require_connection()
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        objects = connection.execute(
            """
            SELECT name
            FROM sqlite_master
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
                raise ObservationStoreUnavailableError(
                    "unable to initialize observation store"
                ) from exc
            return

        if version != DATABASE_SCHEMA_VERSION:
            raise ObservationCorruptionError("unsupported observation-store schema version")
        self._validate_schema_shape(connection)

    @staticmethod
    def _validate_schema_shape(connection: sqlite3.Connection) -> None:
        objects = connection.execute(
            """
            SELECT type, name
            FROM sqlite_master
            WHERE type IN ('table', 'index', 'view', 'trigger')
              AND name NOT LIKE 'sqlite_%'
            """
        ).fetchall()
        if {(str(row[0]), str(row[1])) for row in objects} != {
            ("table", "observations"),
            ("index", "idx_observations_available"),
        }:
            raise ObservationCorruptionError("observation-store schema objects are malformed")

        expected_columns = {
            "observation_id": ("TEXT", 1, 1),
            "payload_type": ("TEXT", 1, 0),
            "observation_json": ("TEXT", 1, 0),
            "event_time_utc_us": ("INTEGER", 1, 0),
            "observed_at_utc_us": ("INTEGER", 1, 0),
            "source_name": ("TEXT", 1, 0),
            "source_record_id": ("TEXT", 0, 0),
            "domain_schema_version": ("INTEGER", 1, 0),
        }
        columns = connection.execute("PRAGMA table_info(observations)").fetchall()
        actual_columns = {
            str(row[1]): (str(row[2]).upper(), int(row[3]), int(row[5])) for row in columns
        }
        if actual_columns != expected_columns:
            raise ObservationCorruptionError("observation-store table schema is malformed")

        table_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'observations'"
        ).fetchone()
        if table_row is None or SQLiteObservationStore._normalized_sql(table_row[0]) != (
            SQLiteObservationStore._normalized_sql(_CREATE_TABLE_SQL)
        ):
            raise ObservationCorruptionError("observation-store table definition is malformed")

        table_rows = connection.execute("PRAGMA table_list").fetchall()
        observation_tables = [row for row in table_rows if row[1] == "observations"]
        if len(observation_tables) != 1 or int(observation_tables[0][5]) != 1:
            raise ObservationCorruptionError("observation-store table must be STRICT")

        indexes = connection.execute("PRAGMA index_list(observations)").fetchall()
        if not any(row[1] == "idx_observations_available" for row in indexes):
            raise ObservationCorruptionError("observation-store index is missing")
        index_columns = connection.execute(
            "PRAGMA index_info(idx_observations_available)"
        ).fetchall()
        if [row[2] for row in index_columns] != ["observed_at_utc_us", "observation_id"]:
            raise ObservationCorruptionError("observation-store index is malformed")
        index_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' "
            "AND name = 'idx_observations_available'"
        ).fetchone()
        if index_row is None or SQLiteObservationStore._normalized_sql(index_row[0]) != (
            SQLiteObservationStore._normalized_sql(_CREATE_INDEX_SQL)
        ):
            raise ObservationCorruptionError("observation-store index definition is malformed")

    @staticmethod
    def _normalized_sql(sql: object) -> str:
        if not isinstance(sql, str):
            return ""
        return " ".join(sql.split()).rstrip(";").upper()

    def _append_projected(self, projected: list[_StoredObservation]) -> None:
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
                raise ObservationConflictError("observation ID already exists") from exc
            raise ObservationStoreError("observation violates store integrity") from exc
        except sqlite3.Error as exc:
            self._rollback_quietly(connection)
            raise ObservationStoreUnavailableError("unable to append observations") from exc

    @staticmethod
    def _decode(row: sqlite3.Row) -> ObservedMarketData:
        try:
            domain_version = int(row["domain_schema_version"])
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise ObservationCorruptionError("invalid domain schema projection") from exc
        if domain_version != DOMAIN_SCHEMA_VERSION:
            raise ObservationCorruptionError("unsupported observation domain schema version")

        try:
            observation = ObservedMarketData.model_validate_json(row["observation_json"])
        except (ValidationError, ValueError, TypeError) as exc:
            raise ObservationCorruptionError("invalid canonical observation JSON") from exc

        try:
            projections_match = (
                row["observation_id"] == str(observation.observation_id)
                and row["payload_type"] == _payload_type(observation)
                and row["event_time_utc_us"] == _to_utc_microseconds(observation.event_time)
                and row["observed_at_utc_us"] == _to_utc_microseconds(observation.observed_at)
                and row["source_name"] == observation.source.name
                and row["source_record_id"] == observation.source_record_id
            )
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise ObservationCorruptionError("invalid observation projection") from exc
        if not projections_match:
            raise ObservationCorruptionError("canonical observation projections disagree")
        return observation

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise ObservationStoreUnavailableError("observation store is closed")
        return self._connection

    @staticmethod
    def _rollback_quietly(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass
