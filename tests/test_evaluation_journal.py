"""Persistent decision-time and separately observed outcome tests."""

import sqlite3
from datetime import timedelta
from decimal import Decimal, Inexact, localcontext
from uuid import UUID

import pytest
from pydantic import ValidationError
from test_head_of_desk import NOW, request, run

from app.evaluation.journal import SQLiteEvaluationJournal
from app.evaluation.outcomes import OutcomeMeasurement


@pytest.fixture
def decision():
    return run(request())


def outcome(decision, **changes):
    mark = decision.request.proposal.reference_mark.model_copy(
        update={
            "observation_id": UUID(int=4000, version=4),
            "price": Decimal("12"),
            "valued_at": NOW + timedelta(days=1),
            "observed_at": NOW + timedelta(days=1),
        }
    )
    return OutcomeMeasurement(
        **(dict(decision=decision, mark=mark, as_of=NOW + timedelta(days=1)) | changes)
    )


def test_append_duplicate_reopen_and_outcomes(tmp_path, decision):
    path = tmp_path / "journal.db"
    journal = SQLiteEvaluationJournal(path)
    key = journal.record(decision, NOW)
    assert journal.record(decision, NOW + timedelta(seconds=1)) == key
    measurement = outcome(decision)
    assert measurement.reference_return() == Decimal("0.2")
    oid = journal.record_outcome(measurement, measurement.as_of)
    assert journal.record_outcome(measurement, measurement.as_of) == oid
    assert journal.outcomes(key, NOW) == ()
    assert journal.outcomes(key, measurement.as_of) == (measurement,)
    journal.close()
    journal = SQLiteEvaluationJournal(path)
    assert journal.keys(NOW) == (key,)
    assert journal.decision(key, NOW).model_dump_json() == decision.model_dump_json()
    assert journal.outcomes(key, measurement.as_of) == (measurement,)
    journal.close()


@pytest.mark.parametrize("kind", ["quiet", "watchlist", "alert"])
def test_all_dispositions_are_journaled(tmp_path, kind):
    req = request()
    if kind == "quiet":
        req = req.model_copy(update={"risk": None})
    if kind == "watchlist":
        req = req.model_copy(update={"trend": None})
    value = run(req)
    journal = SQLiteEvaluationJournal(tmp_path / "journal.db")
    key = journal.record(value, NOW)
    assert journal.decision(key, NOW) == value
    journal.close()


def test_knowledge_and_time_rejections(tmp_path, decision):
    journal = SQLiteEvaluationJournal(tmp_path / "journal.db")
    with pytest.raises(ValueError):
        journal.record(decision, NOW - timedelta(microseconds=1))
    key = journal.record(decision, NOW + timedelta(seconds=1))
    with pytest.raises(KeyError):
        journal.decision(key, NOW)
    with pytest.raises(ValueError):
        journal.record(decision, NOW)
    measurement = outcome(decision)
    with pytest.raises(ValueError):
        journal.record_outcome(measurement, NOW)
    with pytest.raises(ValidationError):
        outcome(decision, as_of=NOW)
    with pytest.raises(ValidationError):
        outcome(
            decision,
            mark=measurement.mark.model_copy(
                update={
                    "instrument": measurement.mark.instrument.model_copy(
                        update={"exchange": "XNYS"}
                    )
                }
            ),
        )
    with pytest.raises(ValidationError):
        outcome(
            decision,
            mark=measurement.mark.model_copy(update={"valued_at": NOW - timedelta(microseconds=1)}),
        )
    journal.close()


def test_unknown_decision_and_corrupt_storage(tmp_path, decision):
    path = tmp_path / "journal.db"
    journal = SQLiteEvaluationJournal(path)
    with pytest.raises(KeyError):
        journal.record_outcome(outcome(decision), NOW + timedelta(days=1))
    key = journal.record(decision, NOW)
    db = sqlite3.connect(path)
    db.execute("UPDATE evaluation_decisions SET payload='{}' WHERE key=?", (key,))
    db.commit()
    db.close()
    with pytest.raises(ValueError):
        journal.decision(key, NOW)
    journal.close()


def test_return_context_and_zero_reference(decision):
    measurement = outcome(decision)
    with localcontext() as ctx:
        ctx.prec = 2
        ctx.traps[Inexact] = True
        assert measurement.reference_return() == Decimal("0.2")
    req = request().model_copy(update={"risk": None})
    proposal = req.proposal.model_copy(
        update={
            "reference_mark": req.proposal.reference_mark.model_copy(update={"price": Decimal(0)})
        }
    )
    quiet = run(req.model_copy(update={"proposal": proposal}))
    assert outcome(quiet).measurement_status() == "UNDEFINED"
