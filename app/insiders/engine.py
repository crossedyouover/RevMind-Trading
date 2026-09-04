"""Pure latest-received insider transaction materialization with post-revision filters."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from pydantic import ValidationError

from app.insiders.models import (
    InsiderMaterializationRequest,
    MaterializedInsiderHistory,
    ObservedInsiderTransaction,
    _matches,
)


class InsiderMaterializationError(Exception):
    """Base failure for insider transaction materialization."""


class InsiderMaterializationInvalidInputError(InsiderMaterializationError):
    """The supplied request or observation tuple cannot be trusted."""


class InsiderMaterializationComputationError(InsiderMaterializationError):
    """A canonical result could not be constructed from validated input."""


class InsiderMaterializationEngine(Protocol):
    def materialize(
        self,
        facts: tuple[ObservedInsiderTransaction, ...],
        request: InsiderMaterializationRequest,
    ) -> MaterializedInsiderHistory:
        """Retain the latest explicitly keyed source receipts before applying filters."""
        ...


class DeterministicInsiderMaterializationEngine:
    """Stateless O(n) reference implementation; no input repair or field inference."""

    def materialize(
        self,
        facts: tuple[ObservedInsiderTransaction, ...],
        request: InsiderMaterializationRequest,
    ) -> MaterializedInsiderHistory:
        trusted_request = self._request(request)
        trusted = self._facts(facts, trusted_request)
        source_facts = tuple(fact for fact in trusted if fact.source == trusted_request.source)
        latest: dict[str, UUID] = {}
        for fact in source_facts:
            if fact.source_transaction_id is not None:
                latest[fact.source_transaction_id] = fact.observation_id
        # Revisit the original knowledge order: do not order by filing or transaction time.
        winners = tuple(
            fact
            for fact in source_facts
            if fact.source_transaction_id is None
            or latest[fact.source_transaction_id] == fact.observation_id
        )
        selected = tuple(fact for fact in winners if _matches(fact, trusted_request))
        try:
            return MaterializedInsiderHistory(
                request=trusted_request,
                facts=selected,
                inspected_receipt_count=len(trusted),
                source_receipt_count=len(source_facts),
                revision_winner_count=len(winners),
                matching_winner_count=len(selected),
            )
        except (ValidationError, AttributeError, TypeError, ValueError) as exc:
            raise InsiderMaterializationComputationError(
                "failed to construct a canonical insider history"
            ) from exc

    @staticmethod
    def _request(request: InsiderMaterializationRequest) -> InsiderMaterializationRequest:
        if not isinstance(request, InsiderMaterializationRequest):
            raise InsiderMaterializationInvalidInputError("request must be canonical")
        try:
            return InsiderMaterializationRequest.model_validate(
                request.model_dump(mode="python", round_trip=True, warnings="none")
            )
        except (ValidationError, AttributeError, TypeError, ValueError) as exc:
            raise InsiderMaterializationInvalidInputError("request must be canonical") from exc

    @staticmethod
    def _facts(
        facts: tuple[ObservedInsiderTransaction, ...], request: InsiderMaterializationRequest
    ) -> tuple[ObservedInsiderTransaction, ...]:
        if not isinstance(facts, tuple):
            raise InsiderMaterializationInvalidInputError("facts must be an immutable tuple")
        trusted: list[ObservedInsiderTransaction] = []
        previous: tuple[datetime, UUID] | None = None
        identifiers: set[UUID] = set()
        for fact in facts:
            if not isinstance(fact, ObservedInsiderTransaction):
                raise InsiderMaterializationInvalidInputError("fact must be an insider observation")
            try:
                item = ObservedInsiderTransaction.model_validate(
                    fact.model_dump(mode="python", round_trip=True, warnings="none")
                )
            except (ValidationError, AttributeError, TypeError, ValueError) as exc:
                raise InsiderMaterializationInvalidInputError("fact must be canonical") from exc
            key = (item.observed_at, item.observation_id)
            if item.observation_id in identifiers:
                raise InsiderMaterializationInvalidInputError("duplicate observation ID")
            if previous is not None and key <= previous:
                raise InsiderMaterializationInvalidInputError(
                    "facts violate strict knowledge order"
                )
            if item.observed_at > request.as_of:
                raise InsiderMaterializationInvalidInputError("fact is not known by as_of")
            trusted.append(item)
            identifiers.add(item.observation_id)
            previous = key
        return tuple(trusted)
