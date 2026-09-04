"""Durable alert claims and explicit transport outcomes; no live adapter."""

import hashlib
import json
import sqlite3
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Protocol, Self

from pydantic import BeforeValidator, ConfigDict, ValidationInfo, field_validator, model_validator

from app.core.schemas import CanonicalModel, NonBlankStr, UtcDatetime
from app.orchestration.models import HeadOfDeskDisposition, HeadOfDeskResult


def _time(value: object, info: ValidationInfo) -> object:
    if isinstance(value, datetime):
        return value
    if info.mode == "json" and isinstance(value, str):
        return datetime.fromisoformat(value)
    raise ValueError("expected aware datetime")


type DeliveryTime = Annotated[UtcDatetime, BeforeValidator(_time)]


class AlertEnvelope(CanonicalModel):
    model_config = ConfigDict(revalidate_instances="always")
    decision: HeadOfDeskResult
    destination: NonBlankStr
    expires_at: DeliveryTime

    @field_validator("decision", mode="before")
    @classmethod
    def validated_decision(cls, value: object, info: ValidationInfo) -> object:
        if info.mode == "python":
            if not isinstance(value, HeadOfDeskResult):
                raise ValueError("expected actual decision")
            return HeadOfDeskResult.model_validate(value)
        return value

    @model_validator(mode="after")
    def only_alert(self) -> Self:
        if self.decision.disposition is not HeadOfDeskDisposition.ALERT:
            raise ValueError("only validated ALERT dispositions may be delivered")
        if self.expires_at < self.decision.request.evaluation_at:
            raise ValueError("expiry precedes decision")
        return self

    def key(self) -> str:
        content = json.dumps(
            [self.decision.model_dump_json(), self.destination],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(content.encode("utf-8")).hexdigest()


class DeliveryStatus(StrEnum):
    STARTED = "STARTED"
    DELIVERED = "DELIVERED"
    DEFINITELY_NOT_SENT = "DEFINITELY_NOT_SENT"
    UNCERTAIN = "UNCERTAIN"
    EXPIRED = "EXPIRED"


class TransportOutcome(StrEnum):
    DELIVERED = "DELIVERED"
    DEFINITELY_NOT_SENT = "DEFINITELY_NOT_SENT"


class AlertTransport(Protocol):
    def send(self, envelope: AlertEnvelope) -> TransportOutcome: ...


class RecordingAlertTransport:
    """Local test/shadow sink only; no external delivery."""

    def __init__(self) -> None:
        self.envelopes: list[AlertEnvelope] = []

    def send(self, envelope: AlertEnvelope) -> TransportOutcome:
        self.envelopes.append(envelope)
        return TransportOutcome.DELIVERED


class DeliveryEvent(CanonicalModel):
    key: str
    attempt: int
    status: DeliveryStatus
    attempted_at: DeliveryTime


class SQLiteAlertOutbox:
    """One connection per instance; independent instances coordinate via SQLite claims."""

    def __init__(self, path: str | Path) -> None:
        self._db = sqlite3.connect(str(path), isolation_level=None)
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("""CREATE TABLE IF NOT EXISTS alert_outbox (
            key TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL,
            attempt INTEGER NOT NULL, attempted_at TEXT NOT NULL)""")
        self._db.execute("""CREATE TABLE IF NOT EXISTS alert_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL,
            attempt INTEGER NOT NULL, status TEXT NOT NULL, attempted_at TEXT NOT NULL)""")

    def close(self) -> None:
        self._db.close()

    def _event(self, row: tuple[object, ...]) -> DeliveryEvent:
        key, attempt, status, when = row
        if not isinstance(key, str) or type(attempt) is not int or attempt < 1:
            raise ValueError("corrupt delivery record")
        if not isinstance(status, str) or not isinstance(when, str):
            raise ValueError("corrupt delivery state")
        return DeliveryEvent(
            key=key,
            attempt=attempt,
            status=DeliveryStatus(status),
            attempted_at=datetime.fromisoformat(when),
        )

    def events(self, key: str) -> tuple[DeliveryEvent, ...]:
        rows = self._db.execute(
            "SELECT key, attempt, status, attempted_at FROM alert_events "
            "WHERE key=? ORDER BY sequence",
            (key,),
        ).fetchall()
        return tuple(self._event(row) for row in rows)

    def claim(
        self, envelope: AlertEnvelope, attempted_at: datetime, retry_failed: bool
    ) -> tuple[DeliveryEvent, bool]:
        envelope = AlertEnvelope.model_validate(envelope)
        key, payload = envelope.key(), envelope.model_dump_json()
        # Validate and normalize the timestamp before SQL or transport.
        event = DeliveryEvent(
            key=key, attempt=1, status=DeliveryStatus.STARTED, attempted_at=attempted_at
        )
        if event.attempted_at < envelope.decision.request.evaluation_at:
            raise ValueError("attempt precedes decision")
        if type(retry_failed) is not bool:
            raise ValueError("retry_failed must be a strict boolean")
        self._db.execute("BEGIN IMMEDIATE")
        try:
            row = self._db.execute(
                "SELECT payload,status,attempt,attempted_at FROM alert_outbox WHERE key=?", (key,)
            ).fetchone()
            attempt = 1
            if row is not None:
                stored = AlertEnvelope.model_validate_json(row[0])
                if stored.key() != key or stored.model_dump_json() != payload:
                    raise ValueError("immutable alert payload conflict")
                prior = self._event((key, row[2], row[1], row[3]))
                if event.attempted_at < prior.attempted_at:
                    raise ValueError("attempt time cannot move backwards")
                if prior.status is not DeliveryStatus.DEFINITELY_NOT_SENT or not retry_failed:
                    self._db.execute("COMMIT")
                    return prior, False
                attempt = prior.attempt + 1
            status = (
                DeliveryStatus.EXPIRED
                if event.attempted_at > envelope.expires_at
                else DeliveryStatus.STARTED
            )
            event = DeliveryEvent(
                key=key, attempt=attempt, status=status, attempted_at=event.attempted_at
            )
            when = event.attempted_at.isoformat()
            if row is None:
                self._db.execute(
                    "INSERT INTO alert_outbox VALUES (?,?,?,?,?)",
                    (key, payload, status.value, attempt, when),
                )
            else:
                self._db.execute(
                    "UPDATE alert_outbox SET status=?,attempt=?,attempted_at=? WHERE key=?",
                    (status.value, attempt, when, key),
                )
            self._db.execute(
                "INSERT INTO alert_events(key,attempt,status,attempted_at) VALUES (?,?,?,?)",
                (key, attempt, status.value, when),
            )
            self._db.execute("COMMIT")
            return event, status is DeliveryStatus.STARTED
        except BaseException:
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")
            raise

    def finish(self, claim: DeliveryEvent, status: DeliveryStatus) -> DeliveryEvent:
        if status not in (
            DeliveryStatus.DELIVERED,
            DeliveryStatus.DEFINITELY_NOT_SENT,
            DeliveryStatus.UNCERTAIN,
        ):
            raise ValueError("invalid terminal transport outcome")
        self._db.execute("BEGIN IMMEDIATE")
        try:
            changed = self._db.execute(
                """UPDATE alert_outbox SET status=? WHERE key=? AND status=?
                   AND attempt=? AND attempted_at=?""",
                (
                    status.value,
                    claim.key,
                    DeliveryStatus.STARTED.value,
                    claim.attempt,
                    claim.attempted_at.isoformat(),
                ),
            ).rowcount
            if changed != 1:
                raise ValueError("delivery claim is no longer current")
            self._db.execute(
                "INSERT INTO alert_events(key,attempt,status,attempted_at) VALUES (?,?,?,?)",
                (claim.key, claim.attempt, status.value, claim.attempted_at.isoformat()),
            )
            self._db.execute("COMMIT")
            return DeliveryEvent(
                key=claim.key, attempt=claim.attempt, status=status, attempted_at=claim.attempted_at
            )
        except BaseException:
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")
            raise


class AlertDeliveryCoordinator:
    def __init__(
        self,
        outbox: SQLiteAlertOutbox,
        transport: AlertTransport,
        allowed_destinations: tuple[str, ...],
    ) -> None:
        if type(allowed_destinations) is not tuple or not allowed_destinations:
            raise ValueError("explicit immutable destination authority required")
        if any(
            type(v) is not str or not v.strip() or v != v.strip() for v in allowed_destinations
        ) or len(set(allowed_destinations)) != len(allowed_destinations):
            raise ValueError("destinations must be canonical and unique")
        self._outbox, self._transport = outbox, transport
        self._allowed = allowed_destinations

    def dispatch(
        self, envelope: AlertEnvelope, attempted_at: datetime, *, retry_failed: bool = False
    ) -> DeliveryEvent:
        envelope = AlertEnvelope.model_validate(envelope)
        if envelope.destination not in self._allowed:
            raise ValueError("destination not authorized")
        claim, send = self._outbox.claim(envelope, attempted_at, retry_failed)
        if not send:
            return claim
        try:
            outcome = self._transport.send(envelope)
            if not isinstance(outcome, TransportOutcome):
                status = DeliveryStatus.UNCERTAIN
            else:
                status = DeliveryStatus(outcome.value)
        except Exception:
            # At the external effect boundary an exception cannot prove nothing was delivered.
            status = DeliveryStatus.UNCERTAIN
        return self._outbox.finish(claim, status)
