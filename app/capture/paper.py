"""Explicit paper inputs over sealed capture evidence; no fills or delivery."""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal, Self

from pydantic import UUID4, model_validator

from app.capture.models import Contract, CycleResult, Digest, SealedInputs, Time, digest
from app.desks.engine import DeterministicAdvisoryDeskEngine
from app.desks.models import SetupDeskRequest, TrendDeskRequest
from app.evaluation.journal import SQLiteEvaluationJournal
from app.materialization.engine import DeterministicBarMaterializationEngine
from app.materialization.models import BarSeriesRequest
from app.orchestration.desk import DeterministicHeadOfDeskEngine
from app.orchestration.models import HeadOfDeskPolicy, HeadOfDeskRequest, HeadOfDeskResult
from app.portfolio.engine import DeterministicPortfolioContextEngine
from app.portfolio.models import ObservedPaperAccountState, PortfolioContextRequest
from app.regime.engine import DeterministicTrendRegimeEngine
from app.regime.models import TrendRegimeRequest
from app.research.engine import DeterministicSingleSeriesResearchEngine
from app.research.models import SingleSeriesResearchRequest
from app.risk.engine import DeterministicPaperRiskEngine
from app.risk.models import PaperRiskPolicy, PaperRiskProposal, PaperRiskRequest


class PaperPolicy(Contract):
    capture_policy_digest: Digest
    risk: PaperRiskPolicy
    desk: HeadOfDeskPolicy

    @model_validator(mode="after")
    def binding(self) -> Self:
        if (
            self.risk.account_id != self.desk.account_id
            or self.risk.policy_id != self.desk.expected_risk_policy_id
            or self.risk.policy_version != self.desk.expected_risk_policy_version
        ):
            raise ValueError("paper policy binding mismatch")
        return self

    def digest(self) -> str:
        return digest(self.model_dump_json())


class PaperEvaluationRequest(Contract):
    schema_version: Literal["PAPER_RESEARCH_V1"]
    evaluation_id: UUID4
    sealed: SealedInputs
    capture: CycleResult
    policy: PaperPolicy
    account: ObservedPaperAccountState
    proposal: PaperRiskProposal

    @model_validator(mode="after")
    def binding(self) -> Self:
        s, c, p = self.sealed, self.capture, self.policy
        if (
            c.cycle_id != s.request.cycle_id
            or c.sealed_digest != digest(s.model_dump_json())
            or c.completed_at < s.sealed_at
            or p.capture_policy_digest != s.request.policy_digest
        ):
            raise ValueError("paper capture binding mismatch")
        if (
            self.account.account_id != p.risk.account_id
            or self.proposal.account_id != self.account.account_id
            or self.account.currency != p.risk.currency
            or self.proposal.instrument != s.request.policy.instrument
            or self.proposal.instrument.currency != self.account.currency
        ):
            raise ValueError("paper account/instrument scope mismatch")
        PortfolioContextRequest(account=self.account, as_of=s.as_of, evaluation_at=s.as_of)
        if (
            self.proposal.observed_at > s.as_of
            or self.proposal.reference_mark.observed_at > s.as_of
        ):
            raise ValueError("future-known paper proposal or reference mark")
        return self


class PaperEvaluationResult(Contract):
    request: PaperEvaluationRequest
    decision: HeadOfDeskResult
    journal_key: Digest
    completed_at: Time

    @model_validator(mode="after")
    def provenance(self) -> Self:
        r, d = self.request, self.decision.request
        if (
            self.journal_key != digest(self.decision.model_dump_json())
            or d.proposal != r.proposal
            or d.policy != r.policy.desk
            or d.as_of != r.sealed.as_of
            or d.evaluation_at != r.sealed.as_of
            or self.completed_at < r.capture.completed_at
            or d.risk is None
        ):
            raise ValueError("paper result provenance mismatch")
        if (
            d.risk.request.context.request.account != r.account
            or d.risk.request.policy != r.policy.risk
            or d.setup is None
            or d.setup.request.payload != r.capture.research
            or d.trend is None
            or d.trend.request.payload != r.capture.trend
            or d.catalyst is not None
            or d.insider is not None
        ):
            raise ValueError("paper result inputs mismatch")
        return self


def compose_paper(request: PaperEvaluationRequest) -> HeadOfDeskResult:
    """Recompute sealed research lineage, then invoke unmodified paper engines."""
    r = PaperEvaluationRequest.model_validate(request)
    s, p = r.sealed, r.sealed.request.policy
    history = DeterministicBarMaterializationEngine().materialize(
        s.observations,
        BarSeriesRequest(
            instrument=p.instrument,
            timeframe=p.timeframe,
            source=p.source,
            as_of=s.as_of,
            start=s.request.start,
            end=s.request.end,
        ),
    )
    actual = tuple(item.bar.timestamp for item in history.bars)
    expected = s.request.expected_times()
    if any(t not in expected for t in actual) or (p.require_every_interval and actual != expected):
        raise ValueError("sealed capture does not satisfy session coverage")
    if actual and s.as_of - actual[-1] > timedelta(microseconds=p.max_bar_age_us):
        raise ValueError("sealed capture is stale under its own policy")
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
            evaluation_at=s.as_of,
        )
    )
    if research != r.capture.research or trend != r.capture.trend:
        raise ValueError("capture result is not derived from sealed inputs")
    context = DeterministicPortfolioContextEngine().evaluate(
        PortfolioContextRequest(
            account=r.account,
            as_of=s.as_of,
            evaluation_at=s.as_of,
        )
    )
    risk = DeterministicPaperRiskEngine().evaluate(
        PaperRiskRequest(
            context=context,
            proposal=r.proposal,
            policy=r.policy.risk,
            as_of=s.as_of,
            evaluation_at=s.as_of,
        )
    )
    desks = DeterministicAdvisoryDeskEngine()
    return DeterministicHeadOfDeskEngine().compose(
        HeadOfDeskRequest(
            proposal=r.proposal,
            risk=risk,
            policy=r.policy.desk,
            as_of=s.as_of,
            evaluation_at=s.as_of,
            setup=desks.setup(SetupDeskRequest(payload=research, evaluation_at=s.as_of)),
            trend=desks.trend(TrendDeskRequest(payload=trend, evaluation_at=s.as_of)),
            catalyst=None,
            insider=None,
        )
    )


class PaperEvaluationCoordinator:
    def __init__(self, directory: Path, *, allowed_policy_digests: tuple[str, ...]) -> None:
        if type(allowed_policy_digests) is not tuple or not allowed_policy_digests:
            raise ValueError("explicit paper policy authority required")
        if any(
            type(v) is not str or len(v) != 64 or any(c not in "0123456789abcdef" for c in v)
            for v in allowed_policy_digests
        ):
            raise ValueError("invalid authorized paper policy digest")
        self._allowed = allowed_policy_digests
        directory.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(directory / "paper-evaluations.db"), isolation_level=None)
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("""CREATE TABLE IF NOT EXISTS paper_evaluations (
            id TEXT PRIMARY KEY, payload TEXT NOT NULL, digest TEXT NOT NULL,
            claimed_at TEXT NOT NULL, result TEXT, result_digest TEXT)""")
        self._db.execute("""CREATE TABLE IF NOT EXISTS paper_attempts (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL, at TEXT NOT NULL)""")
        self.journal = SQLiteEvaluationJournal(directory / "journal.db")

    def close(self) -> None:
        self.journal.close()
        self._db.close()

    def evaluate(self, request: PaperEvaluationRequest, at: datetime) -> PaperEvaluationResult:
        r = PaperEvaluationRequest.model_validate(request)
        if r.policy.digest() not in self._allowed:
            raise PermissionError("paper policy not authorized")
        if (
            not isinstance(at, datetime)
            or at.tzinfo is None
            or at.utcoffset() is None
            or at < r.capture.completed_at
        ):
            raise ValueError("explicit processing time must follow capture completion")
        payload, eid = r.model_dump_json(), str(r.evaluation_id)
        if len(payload.encode("utf-8")) > r.sealed.request.policy.max_artifact_bytes:
            raise ValueError("paper request byte cap exceeded")
        self._db.execute("BEGIN IMMEDIATE")
        try:
            row = self._db.execute(
                "SELECT payload,digest,claimed_at FROM paper_evaluations WHERE id=?", (eid,)
            ).fetchone()
            if row is None:
                self._db.execute(
                    "INSERT INTO paper_evaluations VALUES (?,?,?,?,NULL,NULL)",
                    (eid, payload, digest(payload), at.isoformat()),
                )
            elif row[0] != payload or row[1] != digest(payload):
                raise ValueError("paper evaluation ID conflict or corruption")
            elif at < datetime.fromisoformat(row[2]):
                raise ValueError("paper evaluation time moved backwards")
            last = self._db.execute(
                "SELECT at FROM paper_attempts WHERE id=? ORDER BY sequence DESC LIMIT 1", (eid,)
            ).fetchone()
            if last is not None and at < datetime.fromisoformat(last[0]):
                raise ValueError("paper attempt time moved backwards")
            self._db.execute(
                "INSERT INTO paper_attempts(id,at) VALUES (?,?)", (eid, at.isoformat())
            )
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        # The request/attempt is already durable. Serialize pure replay + idempotent journaling;
        # a crash can leave a journal row but cannot lose the inputs needed to reproduce it.
        self._db.execute("BEGIN IMMEDIATE")
        try:
            last_attempt = self._db.execute(
                "SELECT at FROM paper_attempts WHERE id=? ORDER BY sequence DESC LIMIT 1", (eid,)
            ).fetchone()
            if last_attempt is not None and at < datetime.fromisoformat(last_attempt[0]):
                raise ValueError("a later paper attempt already claimed processing")
            row = self._db.execute(
                "SELECT result,result_digest FROM paper_evaluations WHERE id=?", (eid,)
            ).fetchone()
            if row is not None and row[0] is not None:
                if digest(row[0]) != row[1]:
                    raise ValueError("corrupt paper result")
                result = PaperEvaluationResult.model_validate_json(row[0])
                if (
                    at < result.completed_at
                    or result.request != r
                    or self.journal.decision(result.journal_key, at) != result.decision
                ):
                    raise ValueError("paper journal/result mismatch")
            else:
                decision = compose_paper(r)
                key = digest(decision.model_dump_json())
                result = PaperEvaluationResult(
                    request=r, decision=decision, journal_key=key, completed_at=at
                )
                encoded = result.model_dump_json()
                if len(encoded.encode("utf-8")) > r.sealed.request.policy.max_artifact_bytes:
                    raise ValueError("paper result byte cap exceeded")
                if self.journal.record(decision, at) != key:
                    raise ValueError("unexpected journal identity")
                self._db.execute(
                    "UPDATE paper_evaluations SET result=?,result_digest=? WHERE id=?",
                    (encoded, digest(encoded), eid),
                )
            self._db.execute("COMMIT")
            return result
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
