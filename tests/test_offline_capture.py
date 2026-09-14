"""Mock-only capture invariants and durable crash/replay tests."""

import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.capture.coordinator import CaptureBlocked, CaptureUnresolved, OfflineCaptureCoordinator
from app.capture.models import CapturePolicy, CycleRequest, SealedInputs, Session, digest
from app.core.schemas import AssetClass, Instrument, MarketBar, Timeframe
from app.data.observations import ObservedMarketData, SourceIdentity
from app.evidence.models import MarketEvidenceConfig
from app.regime.models import TrendRegimeConfig
from app.technical.models import TechnicalAnalysisConfig

START = datetime(2025, 2, 3, 14, 30, tzinfo=UTC)
END = START + timedelta(minutes=3)
NOW = END + timedelta(seconds=10)
INSTRUMENT = Instrument(
    symbol="TEST", asset_class=AssetClass.EQUITY, exchange="TEST", currency="USD"
)


def uid(n):
    return UUID(f"00000000-0000-4000-8000-{n:012d}")


class Clock:
    def __init__(self, at=NOW):
        self.at = at
        self.calls = 0

    def now(self):
        self.calls += 1
        return self.at


class IDs:
    def __init__(self, start=0):
        self.n = start

    def __call__(self):
        self.n += 1
        return uid(self.n)


def request(*, number=100, policy_updates=None, bars=None):
    p = CapturePolicy(
        version="synthetic-v1",
        source=SourceIdentity(name="offline-test"),
        instrument=INSTRUMENT,
        provider_binding="OFFLINE_BATCH_V1",
        timeframe=Timeframe.ONE_MINUTE,
        calendar_version="explicit-test-intervals-v1",
        sessions=(Session(start=START, end=END),),
        require_every_interval=True,
        finalization_delay_us=0,
        max_bar_age_us=600_000_000,
        max_range_minutes=10,
        max_observations=20,
        page_size=2,
        max_pages=20,
        max_artifact_bytes=1_000_000,
        technical_config=TechnicalAnalysisConfig(),
        evidence_config=MarketEvidenceConfig(),
        trend_config=TrendRegimeConfig(sma_period=2, return_period=1),
    )
    if policy_updates:
        p = CapturePolicy.model_validate(p.model_copy(update=policy_updates))
    if bars is None:
        bars = tuple(
            MarketBar(
                instrument=INSTRUMENT,
                timeframe=Timeframe.ONE_MINUTE,
                timestamp=START + timedelta(minutes=i),
                open=Decimal(100 + i),
                high=Decimal(102 + i),
                low=Decimal(99 + i),
                close=Decimal(101 + i),
                volume=Decimal(10),
            )
            for i in range(3)
        )
    return CycleRequest(
        schema_version="CAPTURE_V1",
        mode="CAPTURE_RESEARCH",
        cycle_id=uid(number),
        policy=p,
        policy_digest=p.digest(),
        scheduled_at=NOW,
        start=START,
        end=END,
        bars=bars,
    )


def coordinator(path, req, clock=None, ids=None):
    return OfflineCaptureCoordinator(
        path,
        clock=clock or Clock(),
        observation_id_factory=ids or IDs(),
        allowed_policy_digests=(req.policy_digest,),
    )


@pytest.mark.asyncio
async def test_complete_reopen_and_exact_idempotence(tmp_path):
    req, ids, clock = request(), IDs(), Clock()
    c = coordinator(tmp_path, req, clock, ids)
    result = await c.execute(req)
    assert c.status(req.cycle_id) == "COMPLETE"
    assert len(result.research.setup_snapshots) == 3
    assert result.trend.request.history == result.research.request.history
    assert all(b.observed_at == NOW for b in result.research.request.history.bars)
    assert [v[1] for v in c.audit(req.cycle_id)] == ["ACQUIRING", "INPUTS_SEALED", "COMPLETE"]
    assert ids.n == 3
    calls = clock.calls
    assert await c.execute(req) == result
    assert clock.calls == calls
    c.close()
    c = coordinator(tmp_path, req, Clock(NOW - timedelta(days=1)))
    assert (await c.execute(req)).model_dump_json() == result.model_dump_json()
    c.close()


@pytest.mark.asyncio
async def test_storage_failure_cannot_claim_completion(tmp_path, monkeypatch):
    req, ids = request(), IDs()
    c = coordinator(tmp_path, req, ids=ids)

    def fail(observations):
        raise OSError("test disk failure")

    monkeypatch.setattr(c._store, "append_many", fail)
    with pytest.raises(OSError):
        await c.execute(req)
    assert c.status(req.cycle_id) == "ACQUIRING"
    assert c._store.get(uid(1)) is None
    with pytest.raises(CaptureUnresolved):
        await c.execute(req)
    assert ids.n == 3
    c.close()


@pytest.mark.asyncio
async def test_complete_transaction_failure_replays_sealed_inputs(tmp_path, monkeypatch):
    req, ids = request(), IDs()
    c = coordinator(tmp_path, req, ids=ids)
    original = c._event

    def fail(cid, stage, at):
        if stage == "COMPLETE":
            raise OSError("test transaction failure")
        return original(cid, stage, at)

    monkeypatch.setattr(c, "_event", fail)
    with pytest.raises(OSError):
        await c.execute(req)
    assert c.status(req.cycle_id) == "INPUTS_SEALED"
    assert c._db.execute("SELECT result FROM cycles").fetchone()[0] is None
    c.close()
    c = coordinator(tmp_path, req, ids=ids)
    assert (await c.execute(req)).cycle_id == req.cycle_id
    assert ids.n == 3
    c.close()


@pytest.mark.asyncio
async def test_later_cycle_retains_distinct_receipts_and_corrections(tmp_path):
    req, ids, clock = request(), IDs(), Clock()
    c = coordinator(tmp_path, req, clock, ids)
    first = await c.execute(req)
    clock.at += timedelta(minutes=1)
    second_req = request(
        number=101, bars=req.bars[:-1] + (req.bars[-1].model_copy(update={"close": Decimal(104)}),)
    )
    second = await c.execute(second_req)
    assert second.research.request.history.bars[-1].observation_id == uid(6)
    assert second.research.request.history.inspected_observation_count == 6
    assert first.research.request.history.bars[-1].observation_id == uid(3)
    assert await c.execute(req) == first
    c.close()


def test_explicit_session_gaps_do_not_create_synthetic_bars():
    req = request()
    p = {
        "sessions": (
            Session(start=START, end=START + timedelta(minutes=1)),
            Session(start=START + timedelta(minutes=2), end=END),
        )
    }
    req = request(policy_updates=p, bars=(req.bars[0], req.bars[2]))
    assert req.expected_times() == (START, START + timedelta(minutes=2))
    with pytest.raises(ValueError, match="session grid"):
        request(policy_updates=p)


def test_no_live_execution_or_frozen_runtime_dependencies():
    import ast
    import inspect

    import app.capture.coordinator as module

    tree = ast.parse(inspect.getsource(module))
    imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    assert all(
        not (name or "").startswith(
            ("app.runtime", "app.control", "app.risk", "app.alerts", "app.data.providers")
        )
        for name in imports
    )
    assert not any(
        isinstance(n, ast.Import)
        and any(v.name in {"httpx", "requests", "socket"} for v in n.names)
        for n in ast.walk(tree)
    )


@pytest.mark.asyncio
async def test_conflict_and_revocation_before_effects(tmp_path):
    req, ids = request(), IDs()
    c = coordinator(tmp_path, req, ids=ids)
    await c.execute(req)
    changed = req.model_copy(update={"scheduled_at": NOW + timedelta(seconds=1)})
    with pytest.raises(ValueError, match="conflict"):
        await c.execute(changed)
    c._allowed = ("0" * 64,)
    with pytest.raises(PermissionError):
        await c.execute(req)
    assert ids.n == 3
    c.close()


@pytest.mark.asyncio
async def test_crash_after_acquisition_never_reacquires(tmp_path, monkeypatch):
    req, ids = request(), IDs()
    c = coordinator(tmp_path, req, ids=ids)
    original = c._now
    calls = 0

    def crash():
        nonlocal calls
        calls += 1
        if calls == 3:  # acquisition committed; no seal yet
            raise KeyboardInterrupt
        return original()

    monkeypatch.setattr(c, "_now", crash)
    with pytest.raises(KeyboardInterrupt):
        await c.execute(req)
    assert ids.n == 3
    c.close()
    c = coordinator(tmp_path, req, ids=ids)
    with pytest.raises(CaptureUnresolved):
        await c.execute(req)
    assert c.status(req.cycle_id) == "UNRESOLVED"
    assert ids.n == 3
    assert c._store.get(uid(1)) is not None
    c.close()


@pytest.mark.asyncio
async def test_sealed_recovery_ignores_later_backdated_store_writes(tmp_path, monkeypatch):
    req, ids = request(), IDs()
    c = coordinator(tmp_path, req, ids=ids)

    def crash(seal):
        raise KeyboardInterrupt

    monkeypatch.setattr(c, "_research", crash)
    with pytest.raises(KeyboardInterrupt):
        await c.execute(req)
    assert c.status(req.cycle_id) == "INPUTS_SEALED"
    seal = c._seal(str(req.cycle_id), req)
    c._store.append(
        ObservedMarketData(
            observation_id=uid(999),
            payload=req.bars[-1].model_copy(update={"close": Decimal(104)}),
            observed_at=NOW,
            source=req.policy.source,
        )
    )
    c.close()
    c = coordinator(tmp_path, req, ids=ids)
    result = await c.execute(req)
    assert result.sealed_digest == digest(seal.model_dump_json())
    assert result.research.request.history.bars[-1].observation_id == uid(3)
    assert ids.n == 3
    c.close()


@pytest.mark.asyncio
async def test_crash_after_complete_returns_original_result(tmp_path, monkeypatch):
    req = request()
    c = coordinator(tmp_path, req)
    original = c._transition

    def crash(cid, stage, at, **kwargs):
        original(cid, stage, at, **kwargs)
        if stage == "COMPLETE":
            raise KeyboardInterrupt

    monkeypatch.setattr(c, "_transition", crash)
    with pytest.raises(KeyboardInterrupt):
        await c.execute(req)
    c.close()
    c = coordinator(tmp_path, req)
    assert (await c.execute(req)).cycle_id == req.cycle_id
    assert len(c.audit(req.cycle_id)) == 3
    c.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "updates", [{"max_bar_age_us": 1}, {"max_pages": 1}, {"max_artifact_bytes": 1024}]
)
async def test_caps_and_freshness_fail_closed(tmp_path, updates):
    req = request(policy_updates=updates)
    c = coordinator(tmp_path, req)
    with pytest.raises(CaptureBlocked):
        await c.execute(req)
    assert c._db.execute("SELECT count(*) FROM cycles WHERE state='COMPLETE'").fetchone()[0] == 0
    c.close()


@pytest.mark.asyncio
async def test_empty_and_missing_coverage_are_not_fabricated(tmp_path):
    req = request(bars=())
    c = coordinator(tmp_path / "required", req)
    with pytest.raises(CaptureBlocked, match="coverage"):
        await c.execute(req)
    assert c.status(req.cycle_id) == "BLOCKED"
    c.close()
    req = request(bars=(), policy_updates={"require_every_interval": False})
    c = coordinator(tmp_path / "observed", req)
    result = await c.execute(req)
    assert result.research.setup_snapshots == ()
    assert result.trend.snapshots == ()
    c.close()


@pytest.mark.asyncio
async def test_clock_regression_preserves_receipts_and_never_backdates(tmp_path):
    req = request()
    clock = Clock()
    c = coordinator(tmp_path, req, clock)
    await c.execute(req)
    clock.at -= timedelta(seconds=1)
    with pytest.raises(ValueError, match="backwards"):
        await c.execute(request(number=101))
    assert c._db.execute("SELECT count(*) FROM cycles").fetchone()[0] == 1
    c.close()


@pytest.mark.asyncio
async def test_two_connections_do_not_steal_writer_claim(tmp_path):
    req = request()
    a, b = coordinator(tmp_path, req), coordinator(tmp_path, req)
    a._guard.execute("BEGIN IMMEDIATE")
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        await b.execute(req)
    a._guard.execute("ROLLBACK")
    assert (await b.execute(req)).cycle_id == req.cycle_id
    a.close()
    b.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("column", ["sealed", "result", "request_digest", "state"])
async def test_corruption_rejected(tmp_path, column):
    req = request()
    c = coordinator(tmp_path, req)
    await c.execute(req)
    # Column is a test-owned parameter, never application input.
    c._db.execute(f"UPDATE cycles SET {column}='corrupt'")
    with pytest.raises(ValueError):
        await c.execute(req)
    c.close()


@pytest.mark.parametrize(
    "updates",
    [
        {"mode": "PAPER_RESEARCH"},
        {"schema_version": 1},
        {"bars": []},
        {"policy_digest": "0" * 64},
        {"scheduled_at": START},
        {"scheduled_at": 123},
    ],
)
def test_strict_request_revalidation(updates):
    with pytest.raises((ValueError, ValidationError)):
        CycleRequest.model_validate(request().model_copy(update=updates))


def test_no_sorting_no_scope_repair_and_no_partial_bar():
    req = request()
    for bars in (
        req.bars[::-1],
        (req.bars[0], req.bars[0]),
        (req.bars[0].model_copy(update={"timestamp": END}),),
    ):
        with pytest.raises(ValueError):
            request(bars=bars)
    with pytest.raises(ValueError, match="closed"):
        request(policy_updates={"finalization_delay_us": 60_000_000})
    with pytest.raises(ValueError):
        request(policy_updates={"max_pages": True})


@pytest.mark.asyncio
async def test_seal_rejects_future_observations_and_false_receipt_binding(tmp_path):
    req = request()
    c = coordinator(tmp_path, req)
    await c.execute(req)
    seal = c._seal(str(req.cycle_id), req)
    future = seal.observations[0].model_copy(update={"observed_at": NOW + timedelta(seconds=1)})
    with pytest.raises(ValueError):
        SealedInputs.model_validate(
            seal.model_copy(update={"observations": (future,) + seal.observations[1:]})
        )
    with pytest.raises(ValueError):
        SealedInputs.model_validate(
            seal.model_copy(update={"acquired_ids": (uid(999), uid(2), uid(3))})
        )
    c.close()
