"""Pure deterministic point-in-time catalyst materialization."""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from pydantic import ValidationError

from app.catalysts.models import (
    CatalystMaterializationRequest,
    MaterializedCatalystHistory,
    ObservedCatalystFact,
)


class CatalystMaterializationError(Exception):
    """Base catalyst materialization error."""


class CatalystMaterializationInvalidInputError(CatalystMaterializationError):
    """Raised when supplied facts or request cannot be trusted."""


class CatalystMaterializationComputationError(CatalystMaterializationError):
    """Raised when canonical output construction fails."""


class CatalystMaterializationEngine(Protocol):
    def materialize(
        self,
        facts: Sequence[ObservedCatalystFact],
        request: CatalystMaterializationRequest,
    ) -> MaterializedCatalystHistory: ...


class DeterministicCatalystMaterializationEngine:
    """Select explicitly sourced catalyst revisions known at a fixed cutoff."""

    def materialize(
        self,
        facts: Sequence[ObservedCatalystFact],
        request: CatalystMaterializationRequest,
    ) -> MaterializedCatalystHistory:
        trusted_request = self._request(request)
        trusted = self._facts(facts, trusted_request)
        eligible = tuple(item for item in trusted if self._matches(item, trusted_request))
        keyed: dict[str, ObservedCatalystFact] = {}
        unkeyed: list[ObservedCatalystFact] = []
        for fact in eligible:
            if fact.source_record_id is None:
                unkeyed.append(fact)
            else:
                keyed[fact.source_record_id] = fact
        selected = tuple(
            sorted(
                (*unkeyed, *keyed.values()),
                key=lambda item: (
                    item.published_at is None,
                    item.published_at or item.observed_at,
                    item.observed_at,
                    item.observation_id,
                ),
            )
        )
        try:
            return MaterializedCatalystHistory(
                request=trusted_request,
                facts=selected,
                inspected_fact_count=len(trusted),
                eligible_fact_count=len(eligible),
            )
        except (ValidationError, AttributeError, TypeError, ValueError) as exc:
            raise CatalystMaterializationComputationError(
                "failed to construct catalyst materialization"
            ) from exc

    @staticmethod
    def _request(request: CatalystMaterializationRequest) -> CatalystMaterializationRequest:
        if not isinstance(request, CatalystMaterializationRequest):
            raise CatalystMaterializationInvalidInputError("request must be canonical")
        try:
            return CatalystMaterializationRequest(
                as_of=request.as_of,
                source=request.source,
                instrument=request.instrument,
                source_type=request.source_type,
                published_start=request.published_start,
                published_end=request.published_end,
            )
        except (ValidationError, AttributeError, TypeError, ValueError) as exc:
            raise CatalystMaterializationInvalidInputError("request must be canonical") from exc

    @staticmethod
    def _facts(
        facts: Sequence[ObservedCatalystFact], request: CatalystMaterializationRequest
    ) -> tuple[ObservedCatalystFact, ...]:
        if not isinstance(facts, Sequence) or isinstance(facts, (str, bytes)):
            raise CatalystMaterializationInvalidInputError("facts must be a sequence")
        output = []
        previous: tuple[datetime, UUID] | None = None
        identifiers: set[UUID] = set()
        for fact in facts:
            if not isinstance(fact, ObservedCatalystFact):
                raise CatalystMaterializationInvalidInputError("facts must be canonical")
            try:
                item = ObservedCatalystFact(
                    observation_id=fact.observation_id,
                    headline=fact.headline,
                    source=fact.source,
                    source_type=fact.source_type,
                    observed_at=fact.observed_at,
                    published_at=fact.published_at,
                    source_record_id=fact.source_record_id,
                    url=fact.url,
                    source_summary=fact.source_summary,
                    instruments=fact.instruments,
                )
            except (ValidationError, AttributeError, TypeError, ValueError) as exc:
                raise CatalystMaterializationInvalidInputError("facts must be canonical") from exc
            key = (item.observed_at, item.observation_id)
            if previous is not None and key <= previous:
                raise CatalystMaterializationInvalidInputError(
                    "facts must be in strict canonical knowledge order"
                )
            if item.observation_id in identifiers:
                raise CatalystMaterializationInvalidInputError("duplicate observation ID")
            if item.observed_at > request.as_of:
                raise CatalystMaterializationInvalidInputError("fact is not known by as_of")
            output.append(item)
            identifiers.add(item.observation_id)
            previous = key
        return tuple(output)

    @staticmethod
    def _matches(
        fact: ObservedCatalystFact, request: CatalystMaterializationRequest
    ) -> bool:
        if fact.source != request.source:
            return False
        if request.instrument is not None and request.instrument not in fact.instruments:
            return False
        if request.source_type is not None and fact.source_type is not request.source_type:
            return False
        if request.published_start is not None:
            if fact.published_at is None or fact.published_at < request.published_start:
                return False
        if request.published_end is not None:
            if fact.published_at is None or fact.published_at >= request.published_end:
                return False
        return True
