"""Restartable bounded shadow scheduling with local-only effects."""

import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Self

from pydantic import UUID4, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from app.alerts.outbox import (
    AlertDeliveryCoordinator,
    AlertEnvelope,
    DeliveryStatus,
    RecordingAlertTransport,
    SQLiteAlertOutbox,
)
from app.core.schemas import CanonicalModel, NonBlankStr
from app.evaluation.journal import SQLiteEvaluationJournal
from app.orchestration.desk import DeterministicHeadOfDeskEngine
from app.orchestration.models import HeadOfDeskDisposition, HeadOfDeskRequest


def _time(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("explicit aware runtime time required")
    return value.astimezone(UTC)


class ShadowRunPlan(CanonicalModel):
    model_config = ConfigDict(revalidate_instances="always")
    run_id: UUID4
    account_id: NonBlankStr
    requests: tuple[HeadOfDeskRequest, ...]
    destination: NonBlankStr
    deliver_alerts: Annotated[bool, Field(strict=True)]
    alert_ttl_us: Annotated[int, Field(strict=True, ge=0, le=86400000000)]

    @field_validator("requests", mode="before")
    @classmethod
    def canonical_requests(cls, value: object, info: ValidationInfo) -> object:
        if info.mode == "python":
            if type(value) is not tuple or not all(isinstance(v, HeadOfDeskRequest) for v in value):
                raise ValueError("immutable canonical request tuple required")
            return tuple(HeadOfDeskRequest.model_validate(v) for v in value)
        return value

    @model_validator(mode="after")
    def validate_schedule(self) -> Self:
        times = tuple(v.evaluation_at for v in self.requests)
        if any(b <= a for a, b in zip(times, times[1:])):
            raise ValueError("schedule must be unique and strictly chronological")
        if any(
            v.policy.account_id != self.account_id or v.proposal.account_id != self.account_id
            for v in self.requests
        ):
            raise ValueError("plan account scope mismatch")
        return self

    def digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode("utf-8")).hexdigest()


class RunState(StrEnum):
    PAUSED = "PAUSED"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


class RunStatus(CanonicalModel):
    run_id: str
    plan_digest: str
    state: RunState
    next_index: int
    total: int
    last_at: datetime


class ShadowRuntime:
    def __init__(self, directory: str | Path) -> None:
        root = Path(directory).resolve()
        root.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(root / "runtime.db"), isolation_level=None)
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("""CREATE TABLE IF NOT EXISTS shadow_runs (
            run_id TEXT PRIMARY KEY, digest TEXT NOT NULL, plan TEXT NOT NULL,
            state TEXT NOT NULL, next_index INTEGER NOT NULL, last_at TEXT NOT NULL)""")
        self._db.execute("""CREATE TABLE IF NOT EXISTS shadow_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
            event TEXT NOT NULL, step INTEGER NOT NULL, at TEXT NOT NULL, detail TEXT NOT NULL)""")
        self.journal = SQLiteEvaluationJournal(root / "journal.db")
        self.outbox = SQLiteAlertOutbox(root / "outbox.db")
        self.sink = RecordingAlertTransport()

    def close(self) -> None:
        self._db.close()
        self.journal.close()
        self.outbox.close()

    def _load(self, run_id: str) -> tuple[ShadowRunPlan, RunStatus]:
        row = self._db.execute(
            "SELECT digest,plan,state,next_index,last_at FROM shadow_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        plan = ShadowRunPlan.model_validate_json(row[1])
        if plan.digest() != row[0] or str(plan.run_id) != run_id:
            raise ValueError("corrupt runtime manifest")
        if type(row[3]) is not int or not 0 <= row[3] <= len(plan.requests):
            raise ValueError("corrupt runtime checkpoint")
        state = RunState(row[2])
        if state is RunState.COMPLETE and row[3] != len(plan.requests):
            raise ValueError("incomplete COMPLETE checkpoint")
        return plan, RunStatus(
            run_id=run_id,
            plan_digest=row[0],
            state=state,
            next_index=row[3],
            total=len(plan.requests),
            last_at=_time(datetime.fromisoformat(row[4])),
        )

    def status(self, run_id: str) -> RunStatus:
        return self._load(run_id)[1]

    def audit(self, run_id: str) -> tuple[tuple[int, str, int, str, str], ...]:
        self.status(run_id)
        return tuple(
            self._db.execute(
                "SELECT sequence,event,step,at,detail FROM shadow_events "
                "WHERE run_id=? ORDER BY sequence",
                (run_id,),
            ).fetchall()
        )

    def _event(self, run_id: str, event: str, step: int, at: datetime, detail: str = "") -> None:
        self._db.execute(
            "INSERT INTO shadow_events(run_id,event,step,at,detail) VALUES (?,?,?,?,?)",
            (run_id, event, step, at.isoformat(), detail),
        )

    def register(self, plan: ShadowRunPlan, at: datetime) -> RunStatus:
        plan = ShadowRunPlan.model_validate(plan)
        at = _time(at)
        if plan.requests and at > plan.requests[0].evaluation_at:
            raise ValueError("registration must precede first scheduled evaluation")
        run_id = str(plan.run_id)
        self._db.execute("BEGIN IMMEDIATE")
        try:
            row = self._db.execute(
                "SELECT digest FROM shadow_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is not None:
                if row[0] != plan.digest():
                    raise ValueError("run_id manifest conflict")
            else:
                self._db.execute(
                    "INSERT INTO shadow_runs VALUES (?,?,?,?,?,?)",
                    (
                        run_id,
                        plan.digest(),
                        plan.model_dump_json(),
                        RunState.PAUSED.value,
                        0,
                        at.isoformat(),
                    ),
                )
                self._event(run_id, "REGISTERED", 0, at)
            self._db.execute("COMMIT")
        except BaseException:
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")
            raise
        return self.status(run_id)

    def set_running(self, run_id: str, running: bool, at: datetime) -> RunStatus:
        at = _time(at)
        if type(running) is not bool:
            raise ValueError("running flag must be strict bool")
        self._db.execute("BEGIN IMMEDIATE")
        try:
            _, status = self._load(run_id)
            if at < status.last_at:
                raise ValueError("runtime time cannot move backwards")
            if status.state in (RunState.COMPLETE, RunState.FAILED):
                raise ValueError("terminal run cannot resume or change state")
            state = RunState.RUNNING if running else RunState.PAUSED
            if status.state != state:
                self._db.execute(
                    "UPDATE shadow_runs SET state=?,last_at=? WHERE run_id=?",
                    (state.value, at.isoformat(), run_id),
                )
                self._event(run_id, state.value, status.next_index, at)
            else:
                self._db.execute(
                    "UPDATE shadow_runs SET last_at=? WHERE run_id=?", (at.isoformat(), run_id)
                )
            self._db.execute("COMMIT")
        except BaseException:
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")
            raise
        return self.status(run_id)

    def tick(self, run_id: str, at: datetime, max_jobs: int) -> RunStatus:
        at = _time(at)
        if type(max_jobs) is not int or not 1 <= max_jobs <= 10000:
            raise ValueError("max_jobs must be a strict integer in [1,10000]")
        self._db.execute("BEGIN IMMEDIATE")
        try:
            plan, status = self._load(run_id)
            if at < status.last_at:
                raise ValueError("runtime time cannot move backwards")
            index = status.next_index
            if status.state is not RunState.RUNNING:
                self._db.execute("COMMIT")
                return status
            state = RunState.RUNNING
            try:
                for _ in range(max_jobs):
                    if index == len(plan.requests) or plan.requests[index].evaluation_at > at:
                        break
                    request = plan.requests[index]
                    result = DeterministicHeadOfDeskEngine().compose(request)
                    key = self.journal.record(result, at)
                    if result.disposition is HeadOfDeskDisposition.ALERT and plan.deliver_alerts:
                        envelope = AlertEnvelope(
                            decision=result,
                            destination=plan.destination,
                            expires_at=request.evaluation_at
                            + timedelta(microseconds=plan.alert_ttl_us),
                        )
                        delivery = AlertDeliveryCoordinator(
                            self.outbox, self.sink, (plan.destination,)
                        ).dispatch(envelope, at)
                        if delivery.status is not DeliveryStatus.DELIVERED:
                            raise RuntimeError("local alert delivery unresolved")
                    self._event(run_id, "STEP", index, at, key)
                    index += 1
                if index == len(plan.requests):
                    state = RunState.COMPLETE
            except Exception as exc:
                # Persist failure without claiming completion or retrying uncertain effects.
                state = RunState.FAILED
                self._event(run_id, "FAILED", index, at, type(exc).__name__)
            self._db.execute(
                "UPDATE shadow_runs SET state=?,next_index=?,last_at=? WHERE run_id=?",
                (state.value, index, at.isoformat(), run_id),
            )
            if state is RunState.COMPLETE:
                self._event(run_id, "COMPLETE", index, at)
            self._db.execute("COMMIT")
        except BaseException:
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")
            raise
        return self.status(run_id)
