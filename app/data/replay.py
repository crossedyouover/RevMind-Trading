"""Deterministic point-in-time replay over durable market-data observations."""

import sqlite3
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Protocol

from pydantic import UUID4

import app.data.observation_store as store_module
from app.core.schemas import CanonicalModel, UtcDatetime
from app.data.observations import ObservedMarketData

DEFAULT_REPLAY_LIMIT = 1_000
MAX_REPLAY_LIMIT = 10_000


class ReplayError(Exception):
    """Base exception for deterministic historical replay failures."""


class ReplayInvalidRequestError(ReplayError):
    """Raised when replay request parameters are invalid."""


class ReplayUnavailableError(ReplayError):
    """Raised when the historical replay source cannot be read safely."""


class ReplayCorruptionError(ReplayError):
    """Raised when persisted historical data cannot be trusted."""


class ReplayCursor(CanonicalModel):
    """Deterministic continuation position in knowledge-time order."""

    observed_at: UtcDatetime
    observation_id: UUID4


class ReplayBatch(CanonicalModel):
    """Immutable batch of canonical observations from a fixed historical snapshot."""

    observations: tuple[ObservedMarketData, ...]
    next_cursor: ReplayCursor | None
    exhausted: bool


class HistoricalObservationReader(Protocol):
    """RevMind-owned boundary for point-in-time historical observation reads."""

    def read_batch(
        self,
        *,
        as_of: datetime,
        after: ReplayCursor | None = None,
        limit: int = DEFAULT_REPLAY_LIMIT,
    ) -> ReplayBatch:
        """Read one deterministic batch known by ``as_of`` after an optional cursor."""
        ...


class SQLiteHistoricalObservationReader:
    """Read a fixed SQLite snapshot in canonical knowledge-time order.

    The read transaction is established during construction and remains open for the reader
    lifecycle. Later commits from other connections therefore do not become visible to an active
    replay. The first valid ``as_of`` passed to ``read_batch`` is also bound to that reader
    lifecycle, so pagination cannot silently widen or narrow the historical knowledge cutoff.
    ``observed_at`` alone controls historical eligibility; payload event time never does.
    """

    def __init__(self, path: Path) -> None:
        if not isinstance(path, Path):
            raise TypeError("database path must be pathlib.Path")
        if str(path) == ":memory:":
            raise ReplayUnavailableError("replay database must be file-backed")
        if not path.exists() or not path.is_file():
            raise ReplayUnavailableError("replay database does not exist or is not a file")

        self._connection: sqlite3.Connection | None = None
        self._as_of_utc_us: int | None = None
        try:
            connection = sqlite3.connect(
                str(path),
                timeout=5.0,
                isolation_level=None,
                uri=False,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("BEGIN")
            self._connection = connection
            self._validate_snapshot_schema()
        except ReplayError:
            self.close()
            raise
        except sqlite3.Error as exc:
            self.close()
            raise ReplayUnavailableError("unable to open historical replay source") from exc

    def __enter__(self) -> "SQLiteHistoricalObservationReader":
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
        """Close the fixed read snapshot; repeated calls are harmless."""
        connection = self._connection
        self._connection = None
        if connection is not None:
            connection.close()

    def read_batch(
        self,
        *,
        as_of: datetime,
        after: ReplayCursor | None = None,
        limit: int = DEFAULT_REPLAY_LIMIT,
    ) -> ReplayBatch:
        """Read canonical observations from one fixed snapshot and knowledge-time cutoff."""
        cutoff = self._request_time_to_microseconds(as_of)
        validated_limit = self._validate_limit(limit)
        connection = self._require_connection()
        self._bind_as_of(cutoff)

        parameters: tuple[object, ...]
        if after is None:
            where_sql = "observed_at_utc_us <= ?"
            parameters = (cutoff, validated_limit + 1)
        else:
            if not isinstance(after, ReplayCursor):
                raise ReplayInvalidRequestError("after must be a ReplayCursor or None")
            cursor_time = store_module._to_utc_microseconds(after.observed_at)
            where_sql = """
                observed_at_utc_us <= ?
                AND (
                    observed_at_utc_us > ?
                    OR (
                        observed_at_utc_us = ?
                        AND observation_id > ?
                    )
                )
            """
            parameters = (
                cutoff,
                cursor_time,
                cursor_time,
                str(after.observation_id),
                validated_limit + 1,
            )

        try:
            rows = connection.execute(
                f"""
                SELECT {store_module._SELECT_COLUMNS}
                FROM observations
                WHERE {where_sql}
                ORDER BY observed_at_utc_us ASC, observation_id ASC
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        except sqlite3.Error as exc:
            raise ReplayUnavailableError("unable to read historical replay source") from exc

        has_more = len(rows) > validated_limit
        selected_rows = rows[:validated_limit]
        try:
            observations = tuple(
                store_module.SQLiteObservationStore._decode(row) for row in selected_rows
            )
        except store_module.ObservationCorruptionError as exc:
            raise ReplayCorruptionError("historical observation is corrupted") from exc

        if has_more and observations:
            final = observations[-1]
            next_cursor = ReplayCursor(
                observed_at=final.observed_at,
                observation_id=final.observation_id,
            )
            return ReplayBatch(
                observations=observations,
                next_cursor=next_cursor,
                exhausted=False,
            )

        return ReplayBatch(observations=observations, next_cursor=None, exhausted=True)

    def _bind_as_of(self, cutoff: int) -> None:
        if self._as_of_utc_us is None:
            self._as_of_utc_us = cutoff
            return
        if cutoff != self._as_of_utc_us:
            raise ReplayInvalidRequestError(
                "as_of must remain fixed for the historical replay reader lifecycle"
            )

    def _validate_snapshot_schema(self) -> None:
        connection = self._require_connection()
        try:
            version_row = connection.execute("PRAGMA user_version").fetchone()
            if version_row is None or int(version_row[0]) != store_module.DATABASE_SCHEMA_VERSION:
                raise ReplayCorruptionError("unsupported historical replay schema version")
            store_module.SQLiteObservationStore._validate_schema_shape(connection)
        except store_module.ObservationCorruptionError as exc:
            raise ReplayCorruptionError("historical replay schema is corrupted") from exc
        except sqlite3.Error as exc:
            raise ReplayUnavailableError("unable to validate historical replay schema") from exc

    @staticmethod
    def _request_time_to_microseconds(value: datetime) -> int:
        if not isinstance(value, datetime):
            raise ReplayInvalidRequestError("as_of must be a timezone-aware datetime")
        try:
            return store_module._to_utc_microseconds(value)
        except ValueError as exc:
            raise ReplayInvalidRequestError("as_of must be a timezone-aware datetime") from exc

    @staticmethod
    def _validate_limit(limit: int) -> int:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ReplayInvalidRequestError("limit must be an integer")
        if limit <= 0 or limit > MAX_REPLAY_LIMIT:
            raise ReplayInvalidRequestError(
                f"limit must be between 1 and {MAX_REPLAY_LIMIT}"
            )
        return limit

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise ReplayUnavailableError("historical replay reader is closed")
        return self._connection
