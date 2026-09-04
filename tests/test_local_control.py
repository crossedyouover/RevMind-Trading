"""Versioned local authority, command idempotency, and crash safety."""

import sqlite3
from uuid import UUID

import pytest
from pydantic import ValidationError
from test_shadow_runtime import NOW, plan

from app.control.local import (
    ControlAction as A,
)
from app.control.local import (
    ControlCommand,
    ControlGrant,
    LocalControlPlane,
)
from app.control.local import (
    ControlOutcome as O,
)
from app.runtime.shadow import RunState, ShadowRuntime


def grant(value, actions=tuple(A)):
    return ControlGrant(
        principal_id="host", run_id=value.run_id, plan_digest=value.digest(), actions=actions
    )


def command(value, action, index=1, **changes):
    return ControlCommand(
        **(
            dict(
                command_id=UUID(int=index, version=4),
                principal_id="host",
                run_id=value.run_id,
                plan_digest=value.digest(),
                action=action,
                issued_at=NOW,
                max_jobs=1,
                plan=value if action is A.REGISTER else None,
            )
            | changes
        )
    )


def test_register_start_tick_status_audit_health(tmp_path):
    value = plan()
    runtime = ShadowRuntime(tmp_path)
    control = LocalControlPlane(runtime, tmp_path / "control.db", (grant(value),))
    for index, action in enumerate((A.REGISTER, A.START, A.TICK, A.STATUS, A.AUDIT, A.HEALTH), 1):
        response = control.execute(command(value, action, index))
        assert response.outcome is O.COMPLETED
        if action is A.TICK:
            assert response.status.state is RunState.COMPLETE
        if action is A.AUDIT:
            assert any(row[1] == "STEP" for row in response.audit)
        if action is A.HEALTH:
            assert response.reason == "READABLE"
    assert len(runtime.sink.envelopes) == 1
    control.close()
    runtime.close()


def test_idempotency_conflict_reopen_and_revocation(tmp_path):
    value = plan()
    runtime = ShadowRuntime(tmp_path)
    path = tmp_path / "control.db"
    control = LocalControlPlane(runtime, path, (grant(value),))
    original = command(value, A.REGISTER)
    first = control.execute(original)
    assert control.execute(original) == first
    with pytest.raises(ValueError):
        control.execute(command(value, A.STATUS))
    control.close()
    control = LocalControlPlane(runtime, path, ())
    assert control.execute(original).reason == "AUTHORITY_DENIED"
    control.close()
    control = LocalControlPlane(runtime, path, (grant(value),))
    assert control.execute(original) == first
    control.close()
    runtime.close()


def test_scope_denial_before_runtime_access(tmp_path, monkeypatch):
    value = plan()
    runtime = ShadowRuntime(tmp_path)
    control = LocalControlPlane(runtime, tmp_path / "control.db", (grant(value, (A.STATUS,)),))

    def fail(*args):
        raise AssertionError("unauthorized runtime access")

    monkeypatch.setattr(runtime, "status", fail)
    for cmd in [
        command(value, A.START),
        command(value, A.STATUS, principal_id="other"),
        command(value, A.STATUS, plan_digest="0" * 64),
    ]:
        assert control.execute(cmd).reason == "AUTHORITY_DENIED"
    control.close()
    runtime.close()


def test_bound_manifest_cannot_change(tmp_path):
    value = plan()
    runtime = ShadowRuntime(tmp_path)
    runtime.register(value, NOW)
    changed = value.model_copy(update={"destination": "other"})
    control = LocalControlPlane(runtime, tmp_path / "control.db", (grant(changed),))
    assert control.execute(command(changed, A.START)).reason == "PLAN_MISMATCH"
    assert runtime.status(str(value.run_id)).state is RunState.PAUSED
    control.close()
    runtime.close()


def test_crash_claim_never_reexecutes(tmp_path, monkeypatch):
    value = plan()
    runtime = ShadowRuntime(tmp_path)
    path = tmp_path / "control.db"
    control = LocalControlPlane(runtime, path, (grant(value),))

    def crash(*args):
        raise KeyboardInterrupt()

    monkeypatch.setattr(control, "_dispatch", crash)
    cmd = command(value, A.REGISTER)
    with pytest.raises(KeyboardInterrupt):
        control.execute(cmd)
    control.close()
    control = LocalControlPlane(runtime, path, (grant(value),))
    response = control.execute(cmd)
    assert response.outcome is O.UNCERTAIN
    assert response.reason == "UNRESOLVED_COMMAND"
    with pytest.raises(KeyError):
        runtime.status(str(value.run_id))
    control.close()
    runtime.close()


@pytest.mark.parametrize("bad", [True, 1.0, "1", 2])
def test_version_is_strict(bad):
    value = plan(())
    with pytest.raises(ValidationError):
        command(value, A.REGISTER, schema_version=bad)


def test_unexpected_error_and_corrupt_response(tmp_path, monkeypatch):
    value = plan(())
    runtime = ShadowRuntime(tmp_path)
    path = tmp_path / "control.db"
    control = LocalControlPlane(runtime, path, (grant(value),))

    def fail(*args):
        raise OSError("unavailable")

    monkeypatch.setattr(control, "_dispatch", fail)
    cmd = command(value, A.REGISTER)
    assert control.execute(cmd).outcome is O.UNCERTAIN
    assert control.execute(cmd).outcome is O.UNCERTAIN
    db = sqlite3.connect(path)
    db.execute("UPDATE control_commands SET response='{}'")
    db.commit()
    db.close()
    with pytest.raises(ValueError):
        control.execute(cmd)
    control.close()
    runtime.close()


def test_denials_are_audited_without_target_access(tmp_path):
    value = plan(())
    runtime = ShadowRuntime(tmp_path)
    path = tmp_path / "control.db"
    control = LocalControlPlane(runtime, path, ())
    assert control.execute(command(value, A.STATUS)).reason == "AUTHORITY_DENIED"
    db = sqlite3.connect(path)
    assert db.execute("SELECT count(*) FROM control_denials").fetchone()[0] == 1
    db.close()
    control.close()
    runtime.close()


def test_failed_tick_is_not_reported_as_success(tmp_path, monkeypatch):
    value = plan()
    runtime = ShadowRuntime(tmp_path)
    control = LocalControlPlane(runtime, tmp_path / "control.db", (grant(value),))
    control.execute(command(value, A.REGISTER, 1))
    control.execute(command(value, A.START, 2))

    def fail(*args):
        raise OSError("journal failed")

    monkeypatch.setattr(runtime.journal, "record", fail)
    response = control.execute(command(value, A.TICK, 3))
    assert response.outcome is O.REJECTED
    assert response.reason == "RUN_FAILED"
    assert response.status.state is RunState.FAILED
    control.close()
    runtime.close()
