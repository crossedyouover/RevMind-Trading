"""Immutable single-series trend evidence; not a market-wide risk classification."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import (
    BeforeValidator,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from app.core.schemas import CanonicalModel, UtcDatetime
from app.materialization.models import MaterializedBar, MaterializedBarHistory
from app.technical.models import (
    MAX_TECHNICAL_PERIOD,
    TechnicalFeature,
    TechnicalFeatureKey,
    TechnicalFeatureStatus,
)


def _timestamp(value: object, info: ValidationInfo) -> object:
    if isinstance(value, datetime):
        return value
    if info.mode == "json" and isinstance(value, str):
        return datetime.fromisoformat(value)
    raise ValueError("evaluation time must be an aware datetime")


type RegimePeriod = Annotated[int, Field(strict=True, ge=1, le=MAX_TECHNICAL_PERIOD)]
type EvaluationTime = Annotated[UtcDatetime, BeforeValidator(_timestamp)]


class TrendRegimeConfig(CanonicalModel):
    """Explicit periods for the two frozen Phase 7 operands, without implicit defaults."""

    sma_period: RegimePeriod
    return_period: RegimePeriod


def _bar(bar: MaterializedBar) -> MaterializedBar:
    if not isinstance(bar, MaterializedBar):
        raise ValueError("observation must be an actual MaterializedBar")
    return MaterializedBar.model_validate(
        bar.model_dump(mode="python", round_trip=True, warnings="none")
    )


def _history(history: MaterializedBarHistory) -> MaterializedBarHistory:
    try:
        if not isinstance(history.bars, tuple):
            raise ValueError("materialized history bars must remain a tuple")
        return MaterializedBarHistory(
            request=history.request,
            bars=tuple(_bar(item) for item in history.bars),
            inspected_observation_count=history.inspected_observation_count,
            eligible_bar_candidate_count=history.eligible_bar_candidate_count,
        )
    except (ValidationError, AttributeError, TypeError, ValueError) as exc:
        raise ValueError("materialized history must be complete and canonical") from exc


class TrendRegimeRequest(CanonicalModel):
    """PIT-prepared inputs; evaluation time is distinct from source knowledge time."""

    history: MaterializedBarHistory
    config: TrendRegimeConfig
    evaluation_at: EvaluationTime

    @model_validator(mode="after")
    def validate_inputs(self) -> "TrendRegimeRequest":
        try:
            history = _history(self.history)
            config = TrendRegimeConfig.model_validate(
                self.config.model_dump(mode="python", round_trip=True, warnings="none")
            )
            evaluation_at = self.evaluation_at
        except (ValidationError, AttributeError, TypeError, ValueError) as exc:
            raise ValueError("regime request must be complete and canonical") from exc
        if history.request.as_of > evaluation_at:
            raise ValueError("source knowledge cutoff must not exceed evaluation time")
        if any(item.bar.timestamp > evaluation_at for item in history.bars):
            raise ValueError("bar event time must not exceed evaluation time")
        identifiers = tuple(item.observation_id for item in history.bars)
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("materialized observation identities must be unique")
        object.__setattr__(self, "history", history)
        object.__setattr__(self, "config", config)
        return self


class RegimeEvidenceStatus(StrEnum):
    WARMING_UP = "WARMING_UP"
    UNDEFINED = "UNDEFINED"
    AVAILABLE = "AVAILABLE"


class TrendRegime(StrEnum):
    UPWARD = "UPWARD"
    DOWNWARD = "DOWNWARD"
    FLAT = "FLAT"
    MIXED = "MIXED"


def _derive(
    observation: MaterializedBar, sma: TechnicalFeature, arithmetic_return: TechnicalFeature
) -> tuple[RegimeEvidenceStatus, TrendRegime | None]:
    statuses = (sma.status, arithmetic_return.status)
    if TechnicalFeatureStatus.WARMING_UP in statuses:
        return RegimeEvidenceStatus.WARMING_UP, None
    if TechnicalFeatureStatus.UNDEFINED in statuses:
        return RegimeEvidenceStatus.UNDEFINED, None
    if sma.value is None or arithmetic_return.value is None:
        raise ValueError("available technical operands require values")
    close = observation.bar.close
    if close > sma.value and arithmetic_return.value > 0:
        regime = TrendRegime.UPWARD
    elif close < sma.value and arithmetic_return.value < 0:
        regime = TrendRegime.DOWNWARD
    elif close == sma.value and arithmetic_return.value == 0:
        regime = TrendRegime.FLAT
    else:
        regime = TrendRegime.MIXED
    return RegimeEvidenceStatus.AVAILABLE, regime


class TrendRegimeSnapshot(CanonicalModel):
    """One observation with exact technical operands and their descriptive interpretation."""

    observation: MaterializedBar
    sma: TechnicalFeature
    arithmetic_return: TechnicalFeature
    status: RegimeEvidenceStatus
    regime: TrendRegime | None

    @model_validator(mode="after")
    def validate_derivation(self) -> "TrendRegimeSnapshot":
        try:
            observation = _bar(self.observation)
            sma = TechnicalFeature.model_validate(
                self.sma.model_dump(mode="python", round_trip=True, warnings="none")
            )
            arithmetic_return = TechnicalFeature.model_validate(
                self.arithmetic_return.model_dump(mode="python", round_trip=True, warnings="none")
            )
            supplied_state = (self.status, self.regime)
        except (ValidationError, AttributeError, TypeError, ValueError) as exc:
            raise ValueError("regime snapshot must be complete and canonical") from exc
        if sma.key is not TechnicalFeatureKey.SMA_CLOSE:
            raise ValueError("SMA operand must use the SMA_CLOSE feature key")
        if arithmetic_return.key is not TechnicalFeatureKey.ARITHMETIC_RETURN:
            raise ValueError("return operand must use the ARITHMETIC_RETURN feature key")
        if supplied_state != _derive(observation, sma, arithmetic_return):
            raise ValueError("regime status or label contradicts the technical operands")
        object.__setattr__(self, "observation", observation)
        object.__setattr__(self, "sma", sma)
        object.__setattr__(self, "arithmetic_return", arithmetic_return)
        return self


class TrendRegimeResult(CanonicalModel):
    """A complete as-of recomputation, not backdated evidence available at each bar time."""

    request: TrendRegimeRequest
    snapshots: tuple[TrendRegimeSnapshot, ...]

    @field_validator("snapshots", mode="before")
    @classmethod
    def require_tuple(cls, value: object, info: ValidationInfo) -> object:
        if info.mode == "python":
            if not isinstance(value, tuple):
                raise ValueError("regime snapshots must be an immutable tuple")
            if not all(isinstance(item, TrendRegimeSnapshot) for item in value):
                raise ValueError("regime snapshots must be actual snapshot objects")
        return value

    @model_validator(mode="after")
    def validate_alignment(self) -> "TrendRegimeResult":
        try:
            request = TrendRegimeRequest(
                history=self.request.history,
                config=self.request.config,
                evaluation_at=self.request.evaluation_at,
            )
        except (ValidationError, AttributeError, TypeError, ValueError) as exc:
            raise ValueError("result request must be complete and canonical") from exc
        snapshots = tuple(
            TrendRegimeSnapshot.model_validate(
                item.model_dump(mode="python", round_trip=True, warnings="none")
            )
            for item in self.snapshots
        )
        if len(snapshots) != len(request.history.bars):
            raise ValueError("regime results must retain every supplied bar")
        for snapshot, observation in zip(snapshots, request.history.bars, strict=True):
            if snapshot.observation.model_dump_json() != observation.model_dump_json():
                raise ValueError("regime observation provenance must exactly match the input")
            if (
                snapshot.sma.period != request.config.sma_period
                or snapshot.arithmetic_return.period != request.config.return_period
            ):
                raise ValueError("technical operand periods must match configuration")
        object.__setattr__(self, "request", request)
        object.__setattr__(self, "snapshots", snapshots)
        return self
