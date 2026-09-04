"""Append-only decision and outcome records, with explicit journal knowledge time."""

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path

from app.evaluation.outcomes import OutcomeMeasurement
from app.orchestration.models import HeadOfDeskResult


def _time(value: datetime) -> datetime:
    from datetime import UTC

    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("journal time must be an aware datetime")
    return value.astimezone(UTC)


def _key(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class SQLiteEvaluationJournal:
    def __init__(self, path: str | Path) -> None:
        self._db = sqlite3.connect(str(path), isolation_level=None)
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("""CREATE TABLE IF NOT EXISTS evaluation_decisions (
            key TEXT PRIMARY KEY, payload TEXT NOT NULL, recorded_at TEXT NOT NULL)""")
        self._db.execute("""CREATE TABLE IF NOT EXISTS evaluation_outcomes (
            key TEXT PRIMARY KEY, decision_key TEXT NOT NULL,
            payload TEXT NOT NULL, recorded_at TEXT NOT NULL)""")

    def close(self) -> None:
        self._db.close()

    def record(self, decision: HeadOfDeskResult, recorded_at: datetime) -> str:
        decision = HeadOfDeskResult.model_validate(decision)
        recorded_at = _time(recorded_at)
        if recorded_at < decision.request.evaluation_at:
            raise ValueError("journal cannot backdate decision")
        payload = decision.model_dump_json()
        key = _key(payload)
        self._db.execute(
            "INSERT OR IGNORE INTO evaluation_decisions VALUES (?,?,?)",
            (key, payload, recorded_at.isoformat()),
        )
        row = self._db.execute(
            "SELECT payload, recorded_at FROM evaluation_decisions WHERE key=?", (key,)
        ).fetchone()
        if row is None or row[0] != payload or recorded_at < _time(datetime.fromisoformat(row[1])):
            raise ValueError("journal decision collision or backwards receipt")
        return key

    def decision(self, key: str, as_of: datetime) -> HeadOfDeskResult:
        as_of = _time(as_of)
        row = self._db.execute(
            "SELECT payload, recorded_at FROM evaluation_decisions WHERE key=?", (key,)
        ).fetchone()
        if row is None:
            raise KeyError(key)
        if _key(row[0]) != key:
            raise ValueError("corrupt decision digest")
        value = HeadOfDeskResult.model_validate_json(row[0])
        recorded = _time(datetime.fromisoformat(row[1]))
        if recorded < value.request.evaluation_at:
            raise ValueError("corrupt journal receipt")
        if recorded > as_of:
            raise KeyError("decision not known at journal cutoff")
        return value

    def keys(self, as_of: datetime) -> tuple[str, ...]:
        as_of = _time(as_of)
        selected: list[tuple[datetime, str]] = []
        for key, when in self._db.execute("SELECT key, recorded_at FROM evaluation_decisions"):
            recorded = _time(datetime.fromisoformat(when))
            if recorded <= as_of:
                self.decision(key, as_of)
                selected.append((recorded, key))
        return tuple(key for _, key in sorted(selected))

    def record_outcome(self, measurement: OutcomeMeasurement, recorded_at: datetime) -> str:
        measurement = OutcomeMeasurement.model_validate(measurement)
        recorded_at = _time(recorded_at)
        if measurement.as_of > recorded_at:
            raise ValueError("outcome cannot precede its knowledge cutoff")
        decision_key = _key(measurement.decision.model_dump_json())
        stored = self.decision(decision_key, measurement.as_of)
        if stored.model_dump_json() != measurement.decision.model_dump_json():
            raise ValueError("outcome decision provenance mismatch")
        payload = measurement.model_dump_json()
        key = _key(payload)
        self._db.execute(
            "INSERT OR IGNORE INTO evaluation_outcomes VALUES (?,?,?,?)",
            (key, decision_key, payload, recorded_at.isoformat()),
        )
        row = self._db.execute(
            "SELECT payload,recorded_at FROM evaluation_outcomes WHERE key=?", (key,)
        ).fetchone()
        if row is None or row[0] != payload or recorded_at < _time(datetime.fromisoformat(row[1])):
            raise ValueError("outcome collision or backwards receipt")
        return key

    def outcomes(self, decision_key: str, as_of: datetime) -> tuple[OutcomeMeasurement, ...]:
        as_of = _time(as_of)
        self.decision(decision_key, as_of)
        selected: list[tuple[datetime, str, OutcomeMeasurement]] = []
        rows = self._db.execute(
            "SELECT key,payload,recorded_at FROM evaluation_outcomes WHERE decision_key=?",
            (decision_key,),
        ).fetchall()
        for key, payload, when in rows:
            recorded = _time(datetime.fromisoformat(when))
            if _key(payload) != key:
                raise ValueError("corrupt outcome digest")
            value = OutcomeMeasurement.model_validate_json(payload)
            if _key(value.decision.model_dump_json()) != decision_key or recorded < value.as_of:
                raise ValueError("corrupt outcome provenance")
            if recorded <= as_of:
                selected.append((recorded, key, value))
        return tuple(value for _, _, value in sorted(selected, key=lambda row: (row[0], row[1])))
