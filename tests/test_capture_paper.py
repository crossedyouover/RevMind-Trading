"""Paper risk cannot be bypassed by captured evidence or recovery."""

from datetime import timedelta
from decimal import Decimal

import pytest
from test_head_of_desk import request as desk_request
from test_offline_capture import NOW, coordinator, request, uid

from app.capture.models import CapturePolicy, digest
from app.capture.paper import (
    PaperEvaluationCoordinator,
    PaperEvaluationRequest,
    PaperPolicy,
    compose_paper,
)


async def paper_request(path, veto=False, alert=True):
    base = desk_request()
    capture = request()
    policy = CapturePolicy.model_validate(
        capture.policy.model_copy(
            update={
                "instrument": base.proposal.instrument,
                "technical_config": base.setup.request.payload.request.technical_config,
                "evidence_config": base.setup.request.payload.request.evidence_config,
            }
        )
    )
    bars = tuple(
        b.model_copy(
            update={
                "instrument": base.proposal.instrument,
                "high": b.close,
                "open": b.close,
                "low": b.close,
            }
        )
        for b in capture.bars
    )
    capture = capture.model_copy(
        update={"policy": policy, "policy_digest": policy.digest(), "bars": bars}
    )
    c = coordinator(path, capture)
    result = await c.execute(capture)
    seal = c._seal(str(capture.cycle_id), capture)
    c.close()
    risk = base.risk.request.policy.model_copy(
        update={
            "max_abs_quantity_change": Decimal("0.5") if veto else Decimal(100),
        }
    )
    desk = base.policy.model_copy(update={"max_bar_age_us": 120_000_000, "enable_alert": alert})
    return PaperEvaluationRequest(
        schema_version="PAPER_RESEARCH_V1",
        evaluation_id=uid(200),
        sealed=seal,
        capture=result,
        policy=PaperPolicy(capture_policy_digest=policy.digest(), risk=risk, desk=desk),
        account=base.risk.request.context.request.account.model_copy(
            update={
                "effective_at": NOW,
                "observed_at": NOW,
            }
        ),
        proposal=base.proposal.model_copy(
            update={
                "effective_at": NOW,
                "observed_at": NOW,
                "reference_mark": base.proposal.reference_mark.model_copy(
                    update={
                        "valued_at": NOW,
                        "observed_at": NOW,
                    }
                ),
            }
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "veto,alert,expected",
    [(False, True, "ALERT"), (False, False, "WATCHLIST"), (True, True, "QUIET")],
)
async def test_actual_frozen_risk_and_all_dispositions(tmp_path, veto, alert, expected):
    r = await paper_request(tmp_path / "capture", veto=veto, alert=alert)
    c = PaperEvaluationCoordinator(tmp_path / "paper", allowed_policy_digests=(r.policy.digest(),))
    result = c.evaluate(r, NOW)
    assert result.decision.disposition == expected
    assert result.decision == compose_paper(r)
    assert c.journal.decision(result.journal_key, NOW) == result.decision
    assert result.decision.request.risk.request.context.request.account == r.account
    if veto:
        assert result.decision.request.risk.status == "VETO"
        assert "RISK_VETO" in result.decision.reasons
    assert c.evaluate(r, NOW + timedelta(seconds=1)) == result
    assert len(c.journal.keys(NOW + timedelta(seconds=1))) == 1
    c.close()
    c = PaperEvaluationCoordinator(tmp_path / "paper", allowed_policy_digests=(r.policy.digest(),))
    assert c.evaluate(r, NOW + timedelta(seconds=2)) == result
    c.close()


@pytest.mark.asyncio
async def test_journal_before_checkpoint_crash_replays_without_duplicate(tmp_path, monkeypatch):
    r = await paper_request(tmp_path / "capture")
    c = PaperEvaluationCoordinator(tmp_path / "paper", allowed_policy_digests=(r.policy.digest(),))
    original = c.journal.record

    def crash(decision, at):
        original(decision, at)
        raise KeyboardInterrupt

    monkeypatch.setattr(c.journal, "record", crash)
    with pytest.raises(KeyboardInterrupt):
        c.evaluate(r, NOW)
    assert len(c.journal.keys(NOW)) == 1
    assert c._db.execute("SELECT result FROM paper_evaluations").fetchone()[0] is None
    c.close()
    c = PaperEvaluationCoordinator(tmp_path / "paper", allowed_policy_digests=(r.policy.digest(),))
    result = c.evaluate(r, NOW + timedelta(seconds=1))
    assert c.journal.decision(result.journal_key, NOW) == result.decision
    assert len(c.journal.keys(NOW + timedelta(seconds=1))) == 1
    c.close()


@pytest.mark.asyncio
async def test_missing_future_and_mismatched_inputs_rejected(tmp_path):
    r = await paper_request(tmp_path / "capture")
    for changes in (
        {"account": None},
        {"proposal": None},
        {"account": r.account.model_copy(update={"observed_at": NOW + timedelta(seconds=1)})},
        {"proposal": r.proposal.model_copy(update={"account_id": "other"})},
        {"proposal": r.proposal.model_copy(update={"observed_at": NOW + timedelta(seconds=1)})},
        {"policy": r.policy.model_copy(update={"capture_policy_digest": "0" * 64})},
    ):
        with pytest.raises(ValueError):
            PaperEvaluationRequest.model_validate(r.model_copy(update=changes))


@pytest.mark.asyncio
async def test_authority_conflict_time_and_journal_failure(tmp_path, monkeypatch):
    r = await paper_request(tmp_path / "capture")
    c = PaperEvaluationCoordinator(tmp_path / "paper", allowed_policy_digests=("0" * 64,))
    with pytest.raises(PermissionError):
        c.evaluate(r, NOW)
    assert c._db.execute("SELECT count(*) FROM paper_evaluations").fetchone()[0] == 0
    c._allowed = (r.policy.digest(),)
    with pytest.raises(ValueError):
        c.evaluate(r, NOW - timedelta(seconds=1))

    def fail(*args):
        raise OSError("disk failure")

    monkeypatch.setattr(c.journal, "record", fail)
    with pytest.raises(OSError):
        c.evaluate(r, NOW)
    assert c._db.execute("SELECT result FROM paper_evaluations").fetchone()[0] is None
    changed = r.model_copy(
        update={"proposal": r.proposal.model_copy(update={"quantity_change": Decimal(2)})}
    )
    with pytest.raises(ValueError, match="conflict"):
        c.evaluate(changed, NOW)
    c.close()


@pytest.mark.asyncio
async def test_tampered_capture_rejected_before_journaling(tmp_path):
    r = await paper_request(tmp_path / "capture")
    bad_history = r.capture.research.request.history.model_copy(
        update={
            "inspected_observation_count": 99,
        }
    )
    bad_research = r.capture.research.model_copy(
        update={
            "request": r.capture.research.request.model_copy(update={"history": bad_history}),
        }
    )
    bad_trend = r.capture.trend.model_copy(
        update={
            "request": r.capture.trend.request.model_copy(update={"history": bad_history}),
        }
    )
    bad = r.model_copy(
        update={
            "capture": r.capture.model_copy(update={"research": bad_research, "trend": bad_trend})
        }
    )
    with pytest.raises(ValueError, match="sealed"):
        compose_paper(bad)


@pytest.mark.asyncio
async def test_corrupt_result_and_revoked_replay(tmp_path):
    r = await paper_request(tmp_path / "capture")
    c = PaperEvaluationCoordinator(tmp_path / "paper", allowed_policy_digests=(r.policy.digest(),))
    c.evaluate(r, NOW)
    c._allowed = ("0" * 64,)
    with pytest.raises(PermissionError):
        c.evaluate(r, NOW)
    c._allowed = (r.policy.digest(),)
    c._db.execute("UPDATE paper_evaluations SET result_digest='corrupt'")
    with pytest.raises(ValueError, match="corrupt"):
        c.evaluate(r, NOW)
    c.close()


@pytest.mark.asyncio
async def test_claimed_capture_cannot_bypass_its_freshness_policy(tmp_path):
    r = await paper_request(tmp_path / "capture")
    policy = CapturePolicy.model_validate(
        r.sealed.request.policy.model_copy(update={"max_bar_age_us": 1})
    )
    capture_request = r.sealed.request.model_copy(
        update={"policy": policy, "policy_digest": policy.digest()}
    )
    seal = r.sealed.model_copy(update={"request": capture_request})
    altered = r.model_copy(
        update={
            "sealed": seal,
            "capture": r.capture.model_copy(
                update={"sealed_digest": digest(seal.model_dump_json())}
            ),
            "policy": r.policy.model_copy(update={"capture_policy_digest": policy.digest()}),
        }
    )
    with pytest.raises(ValueError, match="stale"):
        compose_paper(altered)


@pytest.mark.asyncio
async def test_stale_account_is_a_journaled_veto_not_a_refreshed_account(tmp_path):
    r = await paper_request(tmp_path / "capture")
    old = NOW - timedelta(seconds=10)
    r = r.model_copy(
        update={"account": r.account.model_copy(update={"effective_at": old, "observed_at": old})}
    )
    c = PaperEvaluationCoordinator(tmp_path / "paper", allowed_policy_digests=(r.policy.digest(),))
    result = c.evaluate(r, NOW)
    assert result.decision.disposition == "QUIET"
    assert result.decision.request.risk.status == "VETO"
    assert result.decision.request.risk.request.context.request.account.observed_at == old
    c.close()
