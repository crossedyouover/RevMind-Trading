"""Append-only local journal for concise imported-market research summaries."""

import sqlite3
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import UUID4, Field, ValidationError

from app.core.schemas import CanonicalModel, Timeframe, UtcDatetime


class ImportHistoryError(Exception):
    """Raised when the local import journal cannot be trusted."""


class ImportHistoryRecord(CanonicalModel):
    schema_version: int = Field(default=1, ge=1, le=1, strict=True)
    import_id: UUID4 = Field(default_factory=uuid4)
    symbol: str = Field(min_length=1, max_length=40)
    asset_class: str = Field(min_length=1, max_length=20)
    timeframe: Timeframe
    source: str = Field(min_length=1, max_length=80)
    received_at: UtcDatetime
    latest_event_at: UtcDatetime
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    imported_bar_count: int = Field(ge=1, le=10_000, strict=True)
    analyzed_bar_count: int = Field(ge=1, le=1_500, strict=True)
    readiness: Literal["READY_FOR_RISK_CHECK", "CAUTION", "WAIT"]
    trend: Annotated[str, Field(pattern=r"^(UPWARD|DOWNWARD|FLAT|MIXED)$")] | None
    active_setups: tuple[str, ...] = Field(max_length=2)


class ImportHistoryStore:
    """Small SQLite journal that never stores uploaded CSV contents or file paths."""

    def __init__(self, path: Path) -> None:
        if not path.parent.is_dir():
            raise ImportHistoryError("import journal parent does not exist")
        self._path = path
        try:
            with sqlite3.connect(path) as db:
                db.execute(
                    """CREATE TABLE IF NOT EXISTS imports (
                    import_id TEXT PRIMARY KEY NOT NULL,
                    received_at TEXT NOT NULL,
                    record_json TEXT NOT NULL
                    ) STRICT"""
                )
        except sqlite3.Error as exc:
            raise ImportHistoryError("unable to open import journal") from exc

    def append(self, record: ImportHistoryRecord) -> None:
        try:
            with sqlite3.connect(self._path) as db:
                db.execute(
                    "INSERT INTO imports VALUES (?,?,?)",
                    (
                        str(record.import_id),
                        record.received_at.isoformat(),
                        record.model_dump_json(),
                    ),
                )
        except sqlite3.Error as exc:
            raise ImportHistoryError("unable to append import journal") from exc

    def recent(self, limit: int = 50) -> tuple[ImportHistoryRecord, ...]:
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("history limit must be an integer from 1 to 50")
        try:
            with sqlite3.connect(self._path) as db:
                rows = db.execute(
                    "SELECT import_id,received_at,record_json FROM imports "
                    "ORDER BY received_at DESC, import_id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise ImportHistoryError("unable to read import journal") from exc
        records = []
        for import_id, received_at, payload in rows:
            try:
                record = ImportHistoryRecord.model_validate_json(payload)
            except (ValidationError, ValueError, TypeError) as exc:
                raise ImportHistoryError("import journal record is invalid") from exc
            if (
                str(record.import_id) != import_id
                or not isinstance(record.import_id, UUID)
                or record.received_at.isoformat() != received_at
            ):
                raise ImportHistoryError("import journal identity mismatch")
            records.append(record)
        return tuple(records)
