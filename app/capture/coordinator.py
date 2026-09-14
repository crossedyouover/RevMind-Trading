"""Offline single-cycle acquisition with durable sealed replay inputs.

Only finite canonical batches are accepted. No provider instance, credentials, live
transport, paper proposal or control-plane capability is accepted by this slice.
"""

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from app.capture.models import CycleRequest, CycleResult, SealedInputs, digest
from app.core.schemas import Instrument, MarketBar, MarketSnapshot
from app.data.ingestion import Clock, MarketDataIngestionCoordinator
from app.data.market import BarRequest, MarketDataProvider
from app.data.observation_store import SQLiteObservationStore
from app.data.observations import ObservedMarketData
from app.data.replay import SQLiteHistoricalObservationReader
from app.materialization.engine import DeterministicBarMaterializationEngine
from app.materialization.models import BarSeriesRequest
from app.regime.engine import DeterministicTrendRegimeEngine
from app.regime.models import TrendRegimeRequest
from app.research.engine import DeterministicSingleSeriesResearchEngine
from app.research.models import SingleSeriesResearchRequest


class CaptureBlocked(ValueError):
    """Explicit coverage or freshness policy was not satisfied."""


class CaptureUnresolved(RuntimeError):
    """An earlier acquisition might have persisted; never automatically reacquire."""


class _BatchProvider(MarketDataProvider):
    def __init__(self, request: CycleRequest) -> None:
        self.request = request

    async def _get_bars(self, request: BarRequest) -> list[MarketBar]:
        if request != BarRequest(
            instrument=self.request.policy.instrument,
            timeframe=self.request.policy.timeframe,
            start=self.request.start,
            end=self.request.end,
        ):
            raise ValueError("offline batch request mismatch")
        return list(self.request.bars)

    async def get_snapshot(self, instrument: Instrument) -> MarketSnapshot:
        raise ValueError("snapshot acquisition unavailable")

    async def get_batch_snapshots(self, instruments: list[Instrument]) -> list[MarketSnapshot]:
        raise ValueError("snapshot acquisition unavailable")


class _ReceiptClock:
    def __init__(self, capture: "OfflineCaptureCoordinator") -> None:
        self.capture = capture

    def now(self) -> datetime:
        return self.capture._now()


class OfflineCaptureCoordinator:
    def __init__(
        self,
        directory: Path,
        *,
        clock: Clock,
        observation_id_factory: Callable[[], UUID],
        allowed_policy_digests: tuple[str, ...],
    ) -> None:
        if type(allowed_policy_digests) is not tuple or not allowed_policy_digests:
            raise ValueError("explicit host-owned policy authority required")
        if any(
            type(v) is not str or len(v) != 64 or any(c not in "0123456789abcdef" for c in v)
            for v in allowed_policy_digests
        ):
            raise ValueError("invalid authorized policy digest")
        self._allowed = allowed_policy_digests
        self._clock = clock
        self._ids = observation_id_factory
        directory.mkdir(parents=True, exist_ok=True)
        self._observations_path = directory / "observations.db"
        self._store = SQLiteObservationStore(self._observations_path)
        self._db = sqlite3.connect(str(directory / "capture.db"), isolation_level=None)
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("""CREATE TABLE IF NOT EXISTS cycles (
            id TEXT PRIMARY KEY, request TEXT NOT NULL, request_digest TEXT NOT NULL,
            state TEXT NOT NULL, sealed TEXT, sealed_digest TEXT, result TEXT, result_digest TEXT
        )""")
        self._db.execute("""CREATE TABLE IF NOT EXISTS events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, cycle_id TEXT NOT NULL,
            stage TEXT NOT NULL, at TEXT NOT NULL)""")
        self._db.execute("CREATE TABLE IF NOT EXISTS clock_state (id INTEGER PRIMARY KEY, at TEXT)")
        # Separate lock transaction spans one finite invocation. OS/process exit releases it;
        # durable cycle claims remain. No lease expiry, unsafe lock stealing or live I/O.
        self._guard = sqlite3.connect(
            str(directory / "capture-lock.db"), isolation_level=None, timeout=0.1
        )
        self._guard.execute("CREATE TABLE IF NOT EXISTS writer_guard (id INTEGER PRIMARY KEY)")

    def close(self) -> None:
        self._guard.close()
        self._db.close()
        self._store.close()

    def _now(self) -> datetime:
        value = self._clock.now()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("aware host clock required")
        value = value.astimezone(UTC)
        row = self._db.execute("SELECT at FROM clock_state WHERE id=1").fetchone()
        if row is not None and value < datetime.fromisoformat(row[0]):
            raise ValueError("host clock moved backwards")
        self._db.execute("INSERT OR REPLACE INTO clock_state VALUES (1,?)", (value.isoformat(),))
        return value

    def _event(self, cid: str, stage: str, at: datetime) -> None:
        self._db.execute(
            "INSERT INTO events(cycle_id,stage,at) VALUES (?,?,?)", (cid, stage, at.isoformat())
        )

    def _transition(
        self,
        cid: str,
        stage: str,
        at: datetime,
        *,
        sealed: str | None = None,
        result: str | None = None,
    ) -> None:
        self._db.execute("BEGIN IMMEDIATE")
        try:
            row = self._db.execute(
                "SELECT state,sealed,result FROM cycles WHERE id=?", (cid,)
            ).fetchone()
            allowed = {
                "INPUTS_SEALED": ("ACQUIRING",),
                "COMPLETE": ("INPUTS_SEALED",),
                "BLOCKED": ("ACQUIRING", "INPUTS_SEALED"),
                "UNRESOLVED": ("ACQUIRING",),
            }
            if row is None or row[0] not in allowed.get(stage, ()):
                raise ValueError("invalid durable cycle transition")
            if (sealed is not None) != (stage == "INPUTS_SEALED"):
                raise ValueError("invalid seal transition")
            if (result is not None) != (stage == "COMPLETE"):
                raise ValueError("invalid result transition")
            if (sealed is not None and row[1] is not None) or row[2] is not None:
                raise ValueError("immutable artifact already recorded")
            if sealed is not None:
                self._db.execute(
                    "UPDATE cycles SET sealed=?,sealed_digest=? WHERE id=?",
                    (sealed, digest(sealed), cid),
                )
            if result is not None:
                self._db.execute(
                    "UPDATE cycles SET result=?,result_digest=? WHERE id=?",
                    (result, digest(result), cid),
                )
            self._db.execute("UPDATE cycles SET state=? WHERE id=?", (stage, cid))
            self._event(cid, stage, at)
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            raise

    def status(self, cycle_id: UUID) -> str:
        row = self._db.execute("SELECT state FROM cycles WHERE id=?", (str(cycle_id),)).fetchone()
        if row is None:
            raise KeyError(cycle_id)
        state = str(row[0])
        if state not in {
            "ACQUIRING",
            "INPUTS_SEALED",
            "COMPLETE",
            "BLOCKED",
            "UNRESOLVED",
        }:
            raise ValueError("corrupt cycle state")
        return state

    def audit(self, cycle_id: UUID) -> tuple[tuple[int, str, str], ...]:
        self.status(cycle_id)
        return tuple(
            self._db.execute(
                "SELECT sequence,stage,at FROM events WHERE cycle_id=? ORDER BY sequence",
                (str(cycle_id),),
            ).fetchall()
        )

    @staticmethod
    def _bounded(payload: str, request: CycleRequest) -> str:
        if len(payload.encode("utf-8")) > request.policy.max_artifact_bytes:
            raise CaptureBlocked("artifact byte cap exceeded")
        return payload

    def _seal(self, cid: str, request: CycleRequest) -> SealedInputs:
        row = self._db.execute(
            "SELECT sealed,sealed_digest FROM cycles WHERE id=?", (cid,)
        ).fetchone()
        if row is None or row[0] is None or digest(row[0]) != row[1]:
            raise ValueError("missing or corrupt sealed inputs")
        seal = SealedInputs.model_validate_json(self._bounded(row[0], request))
        if seal.request != request:
            raise ValueError("seal request mismatch")
        return seal

    def _last_time(self) -> datetime:
        row = self._db.execute("SELECT at FROM clock_state WHERE id=1").fetchone()
        if row is None:
            raise ValueError("missing durable clock boundary")
        return datetime.fromisoformat(row[0])

    async def execute(self, request: CycleRequest) -> CycleResult:
        request = CycleRequest.model_validate(request)
        if request.policy_digest not in self._allowed:
            raise PermissionError("capture policy not authorized by host")
        payload = self._bounded(request.model_dump_json(), request)
        cid = str(request.cycle_id)
        self._guard.execute("BEGIN IMMEDIATE")
        try:
            row = self._db.execute(
                "SELECT request,request_digest,state,result,result_digest FROM cycles WHERE id=?",
                (cid,),
            ).fetchone()
            if row is not None:
                if row[0] != payload or row[1] != digest(payload):
                    raise ValueError("cycle ID content conflict or corruption")
                state = self.status(request.cycle_id)
                if state == "COMPLETE":
                    seal = self._seal(cid, request)
                    if row[3] is None or digest(row[3]) != row[4]:
                        raise ValueError("corrupt cycle result")
                    result = CycleResult.model_validate_json(self._bounded(row[3], request))
                    if (
                        result.cycle_id != request.cycle_id
                        or result.sealed_digest != digest(seal.model_dump_json())
                        or result.research.request.history.request.as_of != seal.as_of
                        or result.completed_at < seal.sealed_at
                        or result.research.request.technical_config
                        != request.policy.technical_config
                        or result.research.request.evidence_config != request.policy.evidence_config
                        or result.trend.request.config != request.policy.trend_config
                    ):
                        raise ValueError("result provenance mismatch")
                    expected_history = DeterministicBarMaterializationEngine().materialize(
                        seal.observations,
                        BarSeriesRequest(
                            instrument=request.policy.instrument,
                            timeframe=request.policy.timeframe,
                            source=request.policy.source,
                            as_of=seal.as_of,
                            start=request.start,
                            end=request.end,
                        ),
                    )
                    if result.research.request.history != expected_history:
                        raise ValueError("result history does not match sealed evidence")
                    return result
                if state in {"ACQUIRING", "UNRESOLVED"}:
                    if state == "ACQUIRING":
                        self._transition(cid, "UNRESOLVED", self._now())
                    raise CaptureUnresolved("acquisition claim requires inspection, not retry")
                if state != "INPUTS_SEALED":
                    raise CaptureBlocked("terminal cycle; inspection required")
                return self._research(self._seal(cid, request))
            started = self._now()
            if started < request.scheduled_at:
                raise ValueError("cycle is not due")
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._db.execute(
                    "INSERT INTO cycles VALUES (?,?,?,'ACQUIRING',NULL,NULL,NULL,NULL)",
                    (cid, payload, digest(payload)),
                )
                self._event(cid, "ACQUIRING", started)
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            # Exceptions before sealing preserve an unresolved claim and any committed receipts.
            ingested = await MarketDataIngestionCoordinator(
                _BatchProvider(request),
                self._store,
                request.policy.source,
                clock=_ReceiptClock(self),
                observation_id_factory=self._ids,
            ).ingest_bars(
                BarRequest(
                    instrument=request.policy.instrument,
                    timeframe=request.policy.timeframe,
                    start=request.start,
                    end=request.end,
                )
            )
            committed = self._now()
            cutoff = self._now()
            observations: list[ObservedMarketData] = []
            cursor = None
            byte_count = 0
            with SQLiteHistoricalObservationReader(self._observations_path) as reader:
                for pages in range(1, request.policy.max_pages + 1):
                    batch = reader.read_batch(
                        as_of=cutoff,
                        after=cursor,
                        limit=min(
                            request.policy.page_size,
                            request.policy.max_observations - len(observations) + 1,
                        ),
                    )
                    for observation in batch.observations:
                        byte_count += len(observation.model_dump_json().encode("utf-8"))
                        if (
                            len(observations) >= request.policy.max_observations
                            or byte_count > request.policy.max_artifact_bytes
                        ):
                            raise CaptureBlocked("replay cap exceeded before sealing")
                        observations.append(observation)
                    if batch.exhausted:
                        break
                    cursor = batch.next_cursor
                else:
                    raise CaptureBlocked("replay page cap exceeded before sealing")
            seal = SealedInputs(
                request=request,
                algorithm_version="CAPTURE_RESEARCH_V1",
                acquired_ids=tuple(o.observation_id for o in ingested.observations),
                receipt_at=ingested.observed_at,
                committed_at=committed,
                as_of=cutoff,
                sealed_at=self._now(),
                observations=tuple(observations),
                replay_pages=pages,
            )
            self._transition(
                cid,
                "INPUTS_SEALED",
                seal.sealed_at,
                sealed=self._bounded(seal.model_dump_json(), request),
            )
            return self._research(seal)
        except CaptureBlocked:
            # A bounded rejection before sealing is known failure, not a successful capture.
            if self._db.execute("SELECT 1 FROM cycles WHERE id=?", (cid,)).fetchone():
                if self.status(request.cycle_id) in {"ACQUIRING", "INPUTS_SEALED"}:
                    self._transition(cid, "BLOCKED", self._last_time())
            raise
        finally:
            self._guard.execute("ROLLBACK")

    def _research(self, seal: SealedInputs) -> CycleResult:
        request, p = seal.request, seal.request.policy
        cid = str(request.cycle_id)
        try:
            history = DeterministicBarMaterializationEngine().materialize(
                seal.observations,
                BarSeriesRequest(
                    instrument=p.instrument,
                    timeframe=p.timeframe,
                    source=p.source,
                    as_of=seal.as_of,
                    start=request.start,
                    end=request.end,
                ),
            )
            actual = tuple(item.bar.timestamp for item in history.bars)
            expected = request.expected_times()
            if any(t not in expected for t in actual):
                raise CaptureBlocked("stored bars outside declared session grid")
            if p.require_every_interval and actual != expected:
                raise CaptureBlocked("declared session coverage incomplete")
            if actual and seal.as_of - actual[-1] > timedelta(microseconds=p.max_bar_age_us):
                raise CaptureBlocked("selected history stale")
            research = DeterministicSingleSeriesResearchEngine().analyze(
                SingleSeriesResearchRequest(
                    history=history,
                    technical_config=p.technical_config,
                    evidence_config=p.evidence_config,
                )
            )
            trend = DeterministicTrendRegimeEngine().analyze(
                TrendRegimeRequest(
                    history=history,
                    config=p.trend_config,
                    evaluation_at=seal.as_of,
                )
            )
            completed = self._now()
            result = CycleResult(
                cycle_id=request.cycle_id,
                sealed_digest=digest(seal.model_dump_json()),
                completed_at=completed,
                research=research,
                trend=trend,
            )
            encoded = self._bounded(result.model_dump_json(), request)
        except CaptureBlocked:
            self._transition(cid, "BLOCKED", self._last_time())
            raise
        # Result and COMPLETE event commit together; there is no external decision journal in
        # capture-only mode and thus no separate research-recorded/checkpoint transaction.
        self._transition(cid, "COMPLETE", completed, result=encoded)
        return result
