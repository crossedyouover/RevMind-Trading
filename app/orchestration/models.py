"""Pure evidence composition; risk veto supremacy and QUIET by default."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from app.core.schemas import CanonicalModel, NonBlankStr, UtcDatetime
from app.desks.models import (
    CatalystDeskReport,
    InsiderDeskReport,
    SetupDeskReport,
    TrendDeskReport,
)
from app.regime.models import RegimeEvidenceStatus, TrendRegime, TrendRegimeSnapshot
from app.risk.models import PaperRiskProposal, PaperRiskResult, PaperRiskStatus
from app.setups.models import SetupKey, SetupSnapshot, SetupStatus


def _time(value: object, info: ValidationInfo) -> object:
    if isinstance(value, datetime):
        return value
    if info.mode == "json" and isinstance(value, str):
        return datetime.fromisoformat(value)
    raise ValueError("expected aware datetime")


type EvaluationTime = Annotated[UtcDatetime, BeforeValidator(_time)]


def _rebuild(value: object) -> object:
    if isinstance(value, _Contract):
        # These models always revalidate instances and reconstruct children in field validators.
        return type(value).model_validate(value)
    if isinstance(value, CanonicalModel):
        fields = type(value).model_fields
        if set(value.__dict__) - set(fields):
            raise ValueError("unknown nested fields")
        return type(value).model_validate({name: _rebuild(getattr(value, name)) for name in fields})
    if isinstance(value, tuple):
        return tuple(_rebuild(item) for item in value)
    if isinstance(value, (list, dict, set)):
        raise ValueError("Python evidence must use immutable canonical objects")
    return value


class _Contract(CanonicalModel):
    model_config = ConfigDict(revalidate_instances="always")

    @field_validator("*", mode="before")
    @classmethod
    def canonical_fields(cls, value: object, info: ValidationInfo) -> object:
        if info.mode == "json":
            return value
        try:
            return _rebuild(value)
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("invalid nested composition input") from exc


class HeadOfDeskPolicy(_Contract):
    policy_id: NonBlankStr
    policy_version: NonBlankStr
    account_id: NonBlankStr
    expected_risk_policy_id: NonBlankStr
    expected_risk_policy_version: NonBlankStr
    setup_key: SetupKey
    enable_watchlist: Annotated[bool, Field(strict=True)]
    enable_alert: Annotated[bool, Field(strict=True)]
    max_bar_age_us: Annotated[int, Field(strict=True, ge=0)]

    @model_validator(mode="after")
    def validate_permissions(self) -> Self:
        if self.enable_alert and not self.enable_watchlist:
            raise ValueError("alert requires watchlist opt-in")
        return self


class HeadOfDeskRequest(_Contract):
    proposal: PaperRiskProposal
    risk: PaperRiskResult | None
    setup: SetupDeskReport | None
    trend: TrendDeskReport | None
    catalyst: CatalystDeskReport | None
    insider: InsiderDeskReport | None
    policy: HeadOfDeskPolicy
    as_of: EvaluationTime
    evaluation_at: EvaluationTime

    @model_validator(mode="after")
    def validate_cutoff(self) -> Self:
        if self.as_of > self.evaluation_at:
            raise ValueError("knowledge cutoff exceeds evaluation")
        return self


class HeadOfDeskDisposition(StrEnum):
    QUIET = "QUIET"
    WATCHLIST = "WATCHLIST"
    ALERT = "ALERT"


class HeadOfDeskReason(StrEnum):
    RISK_UNAVAILABLE = "RISK_UNAVAILABLE"
    RISK_VETO = "RISK_VETO"
    PROPOSAL_MISMATCH = "PROPOSAL_MISMATCH"
    ACCOUNT_MISMATCH = "ACCOUNT_MISMATCH"
    RISK_POLICY_MISMATCH = "RISK_POLICY_MISMATCH"
    RISK_BOUNDARY_MISMATCH = "RISK_BOUNDARY_MISMATCH"
    FUTURE_KNOWLEDGE = "FUTURE_KNOWLEDGE"
    FUTURE_EVALUATION = "FUTURE_EVALUATION"
    INSTRUMENT_MISMATCH = "INSTRUMENT_MISMATCH"
    EVIDENCE_SCOPE_MISMATCH = "EVIDENCE_SCOPE_MISMATCH"
    BAR_HISTORY_MISMATCH = "BAR_HISTORY_MISMATCH"
    STALE_SETUP = "STALE_SETUP"
    STALE_TREND = "STALE_TREND"
    SNAPSHOT_MISMATCH = "SNAPSHOT_MISMATCH"
    PROPOSAL_DIRECTION_MISMATCH = "PROPOSAL_DIRECTION_MISMATCH"
    WATCHLIST_DISABLED = "WATCHLIST_DISABLED"
    SETUP_UNAVAILABLE = "SETUP_UNAVAILABLE"
    SETUP_NOT_ACTIVE = "SETUP_NOT_ACTIVE"
    TREND_NOT_SUPPORTING = "TREND_NOT_SUPPORTING"
    ALERT_DISABLED = "ALERT_DISABLED"
    SETUP_AND_TREND_SUPPORTED = "SETUP_AND_TREND_SUPPORTED"


def _age_us(now: datetime, event: datetime) -> int:
    delta = now - event
    return (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds


def _blockers(
    request: HeadOfDeskRequest,
    setup: SetupSnapshot | None,
    trend: TrendRegimeSnapshot | None,
) -> tuple[HeadOfDeskReason, ...]:
    reasons: set[HeadOfDeskReason] = set()
    risk, proposal, policy = request.risk, request.proposal, request.policy
    cutoffs = [proposal.observed_at, proposal.reference_mark.observed_at]
    evaluations = [proposal.effective_at, proposal.reference_mark.valued_at]
    accounts = [proposal.account_id, policy.account_id]
    if risk is None:
        reasons.add(HeadOfDeskReason.RISK_UNAVAILABLE)
    else:
        if risk.status is PaperRiskStatus.VETO:
            reasons.add(HeadOfDeskReason.RISK_VETO)
        rr = risk.request
        if rr.proposal.model_dump_json() != proposal.model_dump_json():
            reasons.add(HeadOfDeskReason.PROPOSAL_MISMATCH)
        accounts.extend(
            (rr.proposal.account_id, rr.policy.account_id, rr.context.request.account.account_id)
        )
        if (rr.policy.policy_id, rr.policy.policy_version) != (
            policy.expected_risk_policy_id,
            policy.expected_risk_policy_version,
        ):
            reasons.add(HeadOfDeskReason.RISK_POLICY_MISMATCH)
        if rr.as_of != request.as_of or rr.evaluation_at != request.evaluation_at:
            reasons.add(HeadOfDeskReason.RISK_BOUNDARY_MISMATCH)
        cutoffs.extend(
            (
                rr.as_of,
                rr.context.request.as_of,
                rr.proposal.observed_at,
                rr.proposal.reference_mark.observed_at,
            )
        )
        evaluations.extend(
            (
                rr.evaluation_at,
                rr.context.request.evaluation_at,
                rr.proposal.effective_at,
                rr.proposal.reference_mark.valued_at,
            )
        )
    if any(account != policy.account_id for account in accounts):
        reasons.add(HeadOfDeskReason.ACCOUNT_MISMATCH)
    for report in (request.setup, request.trend):
        if report is None:
            continue
        history = report.request.payload.request.history
        cutoffs.append(history.request.as_of)
        evaluations.append(report.request.evaluation_at)
        if history.request.instrument != proposal.instrument:
            reasons.add(HeadOfDeskReason.INSTRUMENT_MISMATCH)
        if history.request.as_of != request.as_of:
            reasons.add(HeadOfDeskReason.EVIDENCE_SCOPE_MISMATCH)
        evaluations.extend(bar.bar.timestamp for bar in history.bars)
    if request.trend is not None:
        evaluations.append(request.trend.request.payload.request.evaluation_at)
    for fact_report in (request.catalyst, request.insider):
        if fact_report is None:
            continue
        scope = fact_report.request.payload.request
        cutoffs.append(scope.as_of)
        evaluations.append(fact_report.request.evaluation_at)
        if scope.instrument is not None and scope.instrument != proposal.instrument:
            reasons.add(HeadOfDeskReason.INSTRUMENT_MISMATCH)
        if scope.instrument != proposal.instrument or scope.as_of != request.as_of:
            reasons.add(HeadOfDeskReason.EVIDENCE_SCOPE_MISMATCH)
    if any(cutoff > request.as_of for cutoff in cutoffs):
        reasons.add(HeadOfDeskReason.FUTURE_KNOWLEDGE)
    if any(evaluation > request.evaluation_at for evaluation in evaluations):
        reasons.add(HeadOfDeskReason.FUTURE_EVALUATION)
    if request.setup is not None and request.trend is not None:
        left = request.setup.request.payload.request.history
        right = request.trend.request.payload.request.history
        if left.model_dump_json() != right.model_dump_json():
            reasons.add(HeadOfDeskReason.BAR_HISTORY_MISMATCH)
    if (
        setup is not None
        and _age_us(request.evaluation_at, setup.timestamp) > policy.max_bar_age_us
    ):
        reasons.add(HeadOfDeskReason.STALE_SETUP)
    if trend is not None:
        bar = trend.observation.bar
        if _age_us(request.evaluation_at, bar.timestamp) > policy.max_bar_age_us:
            reasons.add(HeadOfDeskReason.STALE_TREND)
        if setup is not None and (
            setup.timestamp != bar.timestamp or setup.timeframe is not bar.timeframe
        ):
            reasons.add(HeadOfDeskReason.SNAPSHOT_MISMATCH)
    positive = policy.setup_key is SetupKey.UPSIDE_BREAKOUT_ABOVE_SMA
    if (proposal.quantity_change > 0) != positive:
        reasons.add(HeadOfDeskReason.PROPOSAL_DIRECTION_MISMATCH)
    return tuple(reason for reason in HeadOfDeskReason if reason in reasons)


type Composition = tuple[
    HeadOfDeskDisposition,
    tuple[HeadOfDeskReason, ...],
    SetupSnapshot | None,
    TrendRegimeSnapshot | None,
]


def _compose(request: HeadOfDeskRequest) -> Composition:
    setup = None
    trend = None
    if request.setup is not None and request.setup.request.payload.setup_snapshots:
        setup = request.setup.request.payload.setup_snapshots[-1]
    if request.trend is not None and request.trend.request.payload.snapshots:
        trend = request.trend.request.payload.snapshots[-1]
    blockers = _blockers(request, setup, trend)
    if blockers:
        return HeadOfDeskDisposition.QUIET, blockers, None, None
    disposition = HeadOfDeskDisposition.QUIET
    if not request.policy.enable_watchlist:
        reason = HeadOfDeskReason.WATCHLIST_DISABLED
    elif setup is None:
        reason = HeadOfDeskReason.SETUP_UNAVAILABLE
    elif (
        next(s for s in setup.setups if s.key is request.policy.setup_key).status
        is not SetupStatus.ACTIVE
    ):
        reason = HeadOfDeskReason.SETUP_NOT_ACTIVE
    else:
        disposition = HeadOfDeskDisposition.WATCHLIST
        expected = (
            TrendRegime.UPWARD
            if request.policy.setup_key is SetupKey.UPSIDE_BREAKOUT_ABOVE_SMA
            else TrendRegime.DOWNWARD
        )
        if (
            trend is None
            or trend.status is not RegimeEvidenceStatus.AVAILABLE
            or trend.regime is not expected
        ):
            reason = HeadOfDeskReason.TREND_NOT_SUPPORTING
        elif not request.policy.enable_alert:
            reason = HeadOfDeskReason.ALERT_DISABLED
        else:
            disposition = HeadOfDeskDisposition.ALERT
            reason = HeadOfDeskReason.SETUP_AND_TREND_SUPPORTED
    return disposition, (reason,), setup, trend


def _serialized(value: CanonicalModel | None) -> str | None:
    return None if value is None else value.model_dump_json()


class HeadOfDeskResult(_Contract):
    request: HeadOfDeskRequest
    schema_version: Literal[1] = 1
    disposition: HeadOfDeskDisposition
    reasons: tuple[HeadOfDeskReason, ...]
    selected_setup: SetupSnapshot | None
    selected_trend: TrendRegimeSnapshot | None

    @field_validator("schema_version", mode="before")
    @classmethod
    def strict_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("schema version must be integer 1")
        return value

    @model_validator(mode="after")
    def validate_derivation(self) -> Self:
        disposition, reasons, setup, trend = _compose(self.request)
        if (
            self.disposition != disposition
            or self.reasons != reasons
            or _serialized(self.selected_setup) != _serialized(setup)
            or _serialized(self.selected_trend) != _serialized(trend)
        ):
            raise ValueError("composition contradicts exact risk, evidence, and policy")
        return self
