"""Offline lifecycle, recovery, equivalence, and synthetic endurance."""

from datetime import timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError
from test_head_of_desk import NOW, request, run

from app.runtime.shadow import RunState, ShadowRunPlan, ShadowRuntime


def plan(requests=None, deliver=True):
    return ShadowRunPlan(
        run_id=UUID(int=7000, version=4),
        account_id="paper",
        requests=(request(),) if requests is None else requests,
        destination="local-only",
        deliver_alerts=deliver,
        alert_ttl_us=60000000,
    )


def test_lifecycle_due_boundary_and_replay_equivalence(tmp_path):
    value = plan()
    runtime = ShadowRuntime(tmp_path)
    rid = str(value.run_id)
    before = NOW - timedelta(microseconds=1)
    assert runtime.register(value, before).state == RunState.PAUSED
    assert runtime.tick(rid, before, 1).next_index == 0
    runtime.set_running(rid, True, before)
    assert runtime.tick(rid, before, 1).next_index == 0
    result = runtime.tick(rid, NOW, 1)
    assert result.state == RunState.COMPLETE
    assert len(runtime.sink.envelopes) == 1
    keys = runtime.journal.keys(NOW)
    assert runtime.journal.decision(keys[0], NOW) == run(value.requests[0])
    assert runtime.tick(rid, NOW, 1).state == RunState.COMPLETE
    assert len(runtime.sink.envelopes) == 1
    with pytest.raises(ValueError):
        runtime.set_running(rid, True, NOW)
    runtime.close()
    runtime = ShadowRuntime(tmp_path)
    assert runtime.status(rid).state == RunState.COMPLETE
    assert len(runtime.journal.keys(NOW)) == 1
    runtime.close()


def test_crash_after_effect_before_checkpoint_recovers(tmp_path, monkeypatch):
    value = plan()
    runtime = ShadowRuntime(tmp_path)
    rid = str(value.run_id)
    runtime.register(value, NOW)
    runtime.set_running(rid, True, NOW)
    original = runtime._event

    def crash(run_id, event, *args):
        if event == "STEP":
            raise KeyboardInterrupt()
        return original(run_id, event, *args)

    monkeypatch.setattr(runtime, "_event", crash)
    with pytest.raises(KeyboardInterrupt):
        runtime.tick(rid, NOW, 1)
    assert runtime.status(rid).next_index == 0
    assert len(runtime.sink.envelopes) == 1
    runtime.close()
    runtime = ShadowRuntime(tmp_path)
    assert runtime.tick(rid, NOW, 1).state == RunState.COMPLETE
    assert not runtime.sink.envelopes
    assert len(runtime.journal.keys(NOW)) == 1
    runtime.close()


def test_failure_is_durable_and_nonresumable(tmp_path, monkeypatch):
    runtime = ShadowRuntime(tmp_path)
    value = plan()
    rid = str(value.run_id)
    runtime.register(value, NOW)
    runtime.set_running(rid, True, NOW)

    def fail(*args):
        raise OSError("journal unavailable")

    monkeypatch.setattr(runtime.journal, "record", fail)
    assert runtime.tick(rid, NOW, 1).state == RunState.FAILED
    assert not runtime.sink.envelopes
    with pytest.raises(ValueError):
        runtime.set_running(rid, True, NOW)
    assert any(row[1] == "FAILED" for row in runtime.audit(rid))
    runtime.close()


def test_plan_scope_conflict_pause_and_clock(tmp_path):
    value = plan()
    runtime = ShadowRuntime(tmp_path)
    rid = str(value.run_id)
    runtime.register(value, NOW)
    with pytest.raises(ValueError):
        runtime.register(value.model_copy(update={"destination": "different"}), NOW)
    with pytest.raises(ValidationError):
        plan((value.requests[0], value.requests[0]))
    runtime.set_running(rid, True, NOW)
    runtime.set_running(rid, False, NOW)
    assert runtime.tick(rid, NOW, 1).state == RunState.PAUSED
    with pytest.raises(ValueError):
        runtime.tick(rid, NOW - timedelta(microseconds=1), 1)
    for bad in [True, 0, 10001, 1.0]:
        with pytest.raises(ValueError):
            runtime.tick(rid, NOW, bad)
    runtime.close()


def test_empty_plan_and_no_delivery(tmp_path):
    runtime = ShadowRuntime(tmp_path)
    value = plan((), deliver=False)
    rid = str(value.run_id)
    runtime.register(value, NOW)
    runtime.set_running(rid, True, NOW)
    assert runtime.tick(rid, NOW, 1).state == RunState.COMPLETE
    assert not runtime.sink.envelopes
    runtime.close()


def test_1440_step_synthetic_replay_with_restart(tmp_path):
    base = request().model_copy(update={"risk": None, "setup": None, "trend": None})
    entries = tuple(
        base.model_copy(
            update={
                "as_of": NOW + timedelta(minutes=i),
                "evaluation_at": NOW + timedelta(minutes=i),
            }
        )
        for i in range(1440)
    )
    value = plan(entries, deliver=False)
    rid = str(value.run_id)
    runtime = ShadowRuntime(tmp_path)
    runtime.register(value, NOW)
    runtime.set_running(rid, True, NOW)
    assert runtime.tick(rid, NOW + timedelta(minutes=719), 720).next_index == 720
    runtime.close()
    runtime = ShadowRuntime(tmp_path)
    end = NOW + timedelta(minutes=1439)
    assert runtime.tick(rid, end, 720).state == RunState.COMPLETE
    assert len(runtime.journal.keys(end)) == 1440
    assert sum(row[1] == "STEP" for row in runtime.audit(rid)) == 1440
    assert not runtime.sink.envelopes
    runtime.close()
