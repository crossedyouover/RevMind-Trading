"""Immutable evidence reports without decision or execution authority."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BeforeValidator, ValidationInfo, field_validator, model_validator

from app.catalysts.models import MaterializedCatalystHistory
from app.core.schemas import CanonicalModel, UtcDatetime
from app.insiders.models import MaterializedInsiderHistory
from app.regime.models import TrendRegimeResult
from app.research.models import SingleSeriesResearchResult


def _rebuild(value: object) -> object:
    """Reconstruct every nested model before frozen parent validators can trust it."""
    if isinstance(value, CanonicalModel):
        fields = type(value).model_fields
        if set(value.__dict__) - set(fields):
            raise ValueError("unexpected nested fields")
        return type(value).model_validate({name: _rebuild(getattr(value, name)) for name in fields})
    if isinstance(value, tuple):
        return tuple(_rebuild(item) for item in value)
    if isinstance(value, (list, dict, set)):
        raise ValueError("nested Python state must be canonical and immutable")
    return value


def _canonical(value: object, info: ValidationInfo) -> object:
    if info.mode == "json":
        return _json_collections(value)
    if not isinstance(value, CanonicalModel):
        raise ValueError("expected an actual canonical model")
    try:
        return _rebuild(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("incomplete or invalid nested evidence") from exc


def _json_collections(value: object) -> object:
    """Adapt only legacy period arrays; leave scalar JSON validation to frozen models."""
    if isinstance(value, dict):
        return {
            key: tuple(item)
            if key.endswith("_periods") and isinstance(item, list)
            else _json_collections(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_json_collections(item) for item in value]
    return value


def _time(value: object, info: ValidationInfo) -> object:
    if isinstance(value, datetime):
        return value
    if info.mode == "json" and isinstance(value, str):
        return datetime.fromisoformat(value)
    raise ValueError("evaluation_at requires an aware datetime")


type EvaluationTime = Annotated[UtcDatetime, BeforeValidator(_time)]
type Payload = (
    MaterializedCatalystHistory
    | MaterializedInsiderHistory
    | TrendRegimeResult
    | SingleSeriesResearchResult
)


class DeskKind(StrEnum):
    CATALYST_FACTS = "CATALYST_FACTS"
    INSIDER_FACTS = "INSIDER_FACTS"
    TREND_EVIDENCE = "TREND_EVIDENCE"
    SETUP_EVIDENCE = "SETUP_EVIDENCE"


class DeskCoverage(StrEnum):
    PRESENT = "PRESENT"
    EMPTY = "EMPTY"


def _coverage(payload: Payload) -> DeskCoverage:
    if isinstance(payload, (MaterializedCatalystHistory, MaterializedInsiderHistory)):
        present = bool(payload.facts)
    elif isinstance(payload, TrendRegimeResult):
        present = bool(payload.snapshots)
    else:
        present = bool(payload.setup_snapshots)
    return DeskCoverage.PRESENT if present else DeskCoverage.EMPTY


class _Request[P: Payload](CanonicalModel):
    evaluation_at: EvaluationTime
    payload: P

    @field_validator("payload", mode="before")
    @classmethod
    def canonical_payload(cls, value: object, info: ValidationInfo) -> object:
        return _canonical(value, info)

    @model_validator(mode="after")
    def validate_pit(self) -> Self:
        try:
            payload = self.payload
            if isinstance(payload, (MaterializedCatalystHistory, MaterializedInsiderHistory)):
                cutoff = payload.request.as_of
                receipts = tuple(fact.observation_id for fact in payload.facts)
            else:
                history = payload.request.history
                cutoff = history.request.as_of
                receipts = tuple(bar.observation_id for bar in history.bars)
                if any(bar.bar.timestamp > self.evaluation_at for bar in history.bars):
                    raise ValueError("future bar event in desk evidence")
                if isinstance(payload, TrendRegimeResult):
                    if payload.request.evaluation_at > self.evaluation_at:
                        raise ValueError("trend evaluation cannot be backdated")
            if cutoff > self.evaluation_at:
                raise ValueError("source cutoff exceeds desk evaluation")
            if len(set(receipts)) != len(receipts):
                raise ValueError("duplicate receipt identities")
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid desk PIT evidence: {exc}") from exc
        return self


class CatalystDeskRequest(_Request[MaterializedCatalystHistory]):
    kind: Literal[DeskKind.CATALYST_FACTS] = DeskKind.CATALYST_FACTS


class InsiderDeskRequest(_Request[MaterializedInsiderHistory]):
    kind: Literal[DeskKind.INSIDER_FACTS] = DeskKind.INSIDER_FACTS


class TrendDeskRequest(_Request[TrendRegimeResult]):
    kind: Literal[DeskKind.TREND_EVIDENCE] = DeskKind.TREND_EVIDENCE


class SetupDeskRequest(_Request[SingleSeriesResearchResult]):
    kind: Literal[DeskKind.SETUP_EVIDENCE] = DeskKind.SETUP_EVIDENCE


class _Report[R: (CatalystDeskRequest, InsiderDeskRequest, TrendDeskRequest, SetupDeskRequest)](
    CanonicalModel
):
    request: R
    schema_version: Literal[1] = 1
    coverage: DeskCoverage

    @field_validator("schema_version", mode="before")
    @classmethod
    def strict_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("schema_version must be integer 1")
        return value

    @field_validator("request", mode="before")
    @classmethod
    def canonical_request(cls, value: object, info: ValidationInfo) -> object:
        return _canonical(value, info)

    @model_validator(mode="after")
    def validate_coverage(self) -> Self:
        if self.coverage != _coverage(self.request.payload):
            raise ValueError("coverage contradicts retained evidence")
        return self


class CatalystDeskReport(_Report[CatalystDeskRequest]):
    kind: Literal[DeskKind.CATALYST_FACTS] = DeskKind.CATALYST_FACTS


class InsiderDeskReport(_Report[InsiderDeskRequest]):
    kind: Literal[DeskKind.INSIDER_FACTS] = DeskKind.INSIDER_FACTS


class TrendDeskReport(_Report[TrendDeskRequest]):
    kind: Literal[DeskKind.TREND_EVIDENCE] = DeskKind.TREND_EVIDENCE


class SetupDeskReport(_Report[SetupDeskRequest]):
    kind: Literal[DeskKind.SETUP_EVIDENCE] = DeskKind.SETUP_EVIDENCE
