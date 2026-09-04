"""Audited capability-scoped local commands; no domain overrides."""

import hashlib
import sqlite3
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import (
    UUID4,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from app.core.schemas import CanonicalModel, NonBlankStr, UtcDatetime
from app.runtime.shadow import RunState, RunStatus, ShadowRunPlan, ShadowRuntime


def _time(value: object, info: ValidationInfo) -> object:
    if isinstance(value, datetime):
        return value
    if info.mode == "json" and isinstance(value, str):
        return datetime.fromisoformat(value)
    raise ValueError("explicit aware command time required")


type Digest = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]


class ControlAction(StrEnum):
    REGISTER = "REGISTER"
    START = "START"
    PAUSE = "PAUSE"
    TICK = "TICK"
    STATUS = "STATUS"
    HEALTH = "HEALTH"
    AUDIT = "AUDIT"


class ControlGrant(CanonicalModel):
    model_config = ConfigDict(revalidate_instances="always")
    principal_id: NonBlankStr
    run_id: UUID4
    plan_digest: Digest
    actions: tuple[ControlAction, ...]

    @field_validator("actions", mode="before")
    @classmethod
    def tuple_actions(cls, value: object, info: ValidationInfo) -> object:
        if info.mode == "python" and type(value) is not tuple:
            raise ValueError("immutable explicit action grants required")
        return value

    @model_validator(mode="after")
    def unique_actions(self) -> Self:
        if not self.actions or len(set(self.actions)) != len(self.actions):
            raise ValueError("grant actions must be nonempty and unique")
        return self


class ControlCommand(CanonicalModel):
    model_config = ConfigDict(revalidate_instances="always")
    schema_version: Literal[1] = 1
    command_id: UUID4
    principal_id: NonBlankStr
    run_id: UUID4
    plan_digest: Digest
    action: ControlAction
    issued_at: Annotated[UtcDatetime, BeforeValidator(_time)]
    max_jobs: Annotated[int, Field(strict=True, ge=1, le=10000)]
    plan: ShadowRunPlan | None

    @field_validator("schema_version", mode="before")
    @classmethod
    def strict_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("schema version must be integer 1")
        return value

    @field_validator("plan", mode="before")
    @classmethod
    def trusted_plan(cls, value: object, info: ValidationInfo) -> object:
        if value is None or info.mode == "json":
            return value
        if not isinstance(value, ShadowRunPlan):
            raise ValueError("actual run plan required")
        return ShadowRunPlan.model_validate(value)

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        if self.action is ControlAction.REGISTER:
            if (
                self.plan is None
                or self.plan.run_id != self.run_id
                or self.plan.digest() != self.plan_digest
            ):
                raise ValueError("REGISTER requires exact bound manifest")
        elif self.plan is not None:
            raise ValueError("only REGISTER may carry configuration")
        return self


class ControlOutcome(StrEnum):
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    UNCERTAIN = "UNCERTAIN"


class ControlResponse(CanonicalModel):
    schema_version: Literal[1] = 1
    command: ControlCommand
    outcome: ControlOutcome
    reason: Literal[
        "OK",
        "READABLE",
        "RUN_FAILED",
        "AUTHORITY_DENIED",
        "PLAN_MISMATCH",
        "RUNTIME_REJECTED",
        "RUNTIME_ERROR",
        "UNRESOLVED_COMMAND",
    ]
    status: RunStatus | None
    audit: tuple[tuple[int, str, int, str, str], ...] = ()

    @field_validator("schema_version", mode="before")
    @classmethod
    def strict_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("response schema version must be integer 1")
        return value


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class LocalControlPlane:
    def __init__(
        self, runtime: ShadowRuntime, ledger_path: str | Path, grants: tuple[ControlGrant, ...]
    ) -> None:
        if type(grants) is not tuple or not all(isinstance(v, ControlGrant) for v in grants):
            raise ValueError("host-owned immutable grants required")
        self._grants = tuple(ControlGrant.model_validate(v) for v in grants)
        self._runtime = runtime
        self._db = sqlite3.connect(str(ledger_path), isolation_level=None)
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("""CREATE TABLE IF NOT EXISTS control_commands (
            command_id TEXT PRIMARY KEY, payload TEXT NOT NULL,
            response TEXT, response_digest TEXT)""")
        self._db.execute("""CREATE TABLE IF NOT EXISTS control_denials (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL)""")

    def close(self) -> None:
        self._db.close()

    def _authorized(self, command: ControlCommand) -> bool:
        return any(
            g.principal_id == command.principal_id
            and g.run_id == command.run_id
            and g.plan_digest == command.plan_digest
            and command.action in g.actions
            for g in self._grants
        )

    def execute(self, command: ControlCommand) -> ControlResponse:
        command = ControlCommand.model_validate(command)
        # Authority is checked before lookup so revocation also blocks replay reads.
        if not self._authorized(command):
            self._db.execute(
                "INSERT INTO control_denials(payload) VALUES (?)", (command.model_dump_json(),)
            )
            return ControlResponse(
                command=command,
                outcome=ControlOutcome.REJECTED,
                reason="AUTHORITY_DENIED",
                status=None,
            )
        payload = command.model_dump_json()
        cid = str(command.command_id)
        self._db.execute("BEGIN IMMEDIATE")
        try:
            row = self._db.execute(
                "SELECT payload,response,response_digest FROM control_commands WHERE command_id=?",
                (cid,),
            ).fetchone()
            if row is not None:
                if row[0] != payload:
                    raise ValueError("command_id content conflict")
                self._db.execute("COMMIT")
                if row[1] is None:
                    return ControlResponse(
                        command=command,
                        outcome=ControlOutcome.UNCERTAIN,
                        reason="UNRESOLVED_COMMAND",
                        status=None,
                    )
                if _digest(row[1]) != row[2]:
                    raise ValueError("corrupt command response")
                result = ControlResponse.model_validate_json(row[1])
                if result.command.model_dump_json() != payload:
                    raise ValueError("command response provenance mismatch")
                return result
            self._db.execute("INSERT INTO control_commands VALUES (?,?,NULL,NULL)", (cid, payload))
            self._db.execute("COMMIT")
        except BaseException:
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")
            raise
        try:
            response = self._dispatch(command)
        except (ValueError, KeyError):
            response = ControlResponse(
                command=command,
                outcome=ControlOutcome.REJECTED,
                reason="RUNTIME_REJECTED",
                status=None,
            )
        except Exception:
            response = ControlResponse(
                command=command,
                outcome=ControlOutcome.UNCERTAIN,
                reason="RUNTIME_ERROR",
                status=None,
            )
        encoded = response.model_dump_json()
        changed = self._db.execute(
            "UPDATE control_commands SET response=?,response_digest=? "
            "WHERE command_id=? AND response IS NULL",
            (encoded, _digest(encoded), cid),
        ).rowcount
        if changed != 1:
            raise ValueError("command claim is no longer current")
        return response

    def _dispatch(self, command: ControlCommand) -> ControlResponse:
        rid = str(command.run_id)
        if command.action is ControlAction.REGISTER:
            if command.plan is None:
                raise ValueError("missing manifest")
            status = self._runtime.register(command.plan, command.issued_at)
        else:
            status = self._runtime.status(rid)
            if status.plan_digest != command.plan_digest:
                return ControlResponse(
                    command=command,
                    outcome=ControlOutcome.REJECTED,
                    reason="PLAN_MISMATCH",
                    status=None,
                )
            if command.action is ControlAction.START:
                status = self._runtime.set_running(rid, True, command.issued_at)
            elif command.action is ControlAction.PAUSE:
                status = self._runtime.set_running(rid, False, command.issued_at)
            elif command.action is ControlAction.TICK:
                status = self._runtime.tick(rid, command.issued_at, command.max_jobs)
        audit = self._runtime.audit(rid) if command.action is ControlAction.AUDIT else ()
        reason: Literal["OK", "READABLE", "RUN_FAILED"] = "OK"
        if command.action is ControlAction.HEALTH:
            self._runtime.journal.keys(command.issued_at)
            self._runtime.outbox.events("")
            reason = "RUN_FAILED" if status.state is RunState.FAILED else "READABLE"
        outcome = ControlOutcome.COMPLETED
        if status.state is RunState.FAILED:
            reason = "RUN_FAILED"
            if command.action is ControlAction.TICK:
                outcome = ControlOutcome.REJECTED
        return ControlResponse(
            command=command,
            outcome=outcome,
            reason=reason,
            status=status,
            audit=audit,
        )
