"""Pure deterministic point-in-time global-event materialization."""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from pydantic import ValidationError

from app.intelligence.events.models import (
    GlobalEventMaterializationRequest,
    MaterializedGlobalEventHistory,
    ObservedGlobalEvent,
    _global_event_output_key,
    _matches_event_filters,
)


class GlobalEventMaterializationError(Exception):
    """Base global-event materialization error."""


class GlobalEventMaterializationInvalidInputError(GlobalEventMaterializationError):
    """Raised when supplied event receipts or request cannot be trusted."""


class GlobalEventMaterializationComputationError(GlobalEventMaterializationError):
    """Raised when canonical output construction fails."""


class GlobalEventMaterializationEngine(Protocol):
    def materialize(
        self,
        events: Sequence[ObservedGlobalEvent],
        request: GlobalEventMaterializationRequest,
    ) -> MaterializedGlobalEventHistory: ...


class DeterministicGlobalEventMaterializationEngine:
    """Select the latest eligible provider revisions known at a fixed cutoff."""

    def materialize(
        self,
        events: Sequence[ObservedGlobalEvent],
        request: GlobalEventMaterializationRequest,
    ) -> MaterializedGlobalEventHistory:
        trusted_request = self._request(request)
        trusted = self._events(events, trusted_request)
        eligible = tuple(
            event
            for event in trusted
            if event.source == trusted_request.source
            and _matches_event_filters(event, trusted_request)
        )
        keyed: dict[str, ObservedGlobalEvent] = {}
        unkeyed: list[ObservedGlobalEvent] = []
        for event in eligible:
            if event.source_event_id is None:
                unkeyed.append(event)
            else:
                keyed[event.source_event_id] = event
        selected = tuple(sorted((*unkeyed, *keyed.values()), key=_global_event_output_key))
        try:
            return MaterializedGlobalEventHistory(
                request=trusted_request,
                events=selected,
                inspected_event_count=len(trusted),
                eligible_event_count=len(eligible),
            )
        except (ValidationError, AttributeError, TypeError, ValueError) as exc:
            raise GlobalEventMaterializationComputationError(
                "failed to construct global-event materialization"
            ) from exc

    @staticmethod
    def _request(
        request: GlobalEventMaterializationRequest,
    ) -> GlobalEventMaterializationRequest:
        if not isinstance(request, GlobalEventMaterializationRequest):
            raise GlobalEventMaterializationInvalidInputError("request must be canonical")
        try:
            return GlobalEventMaterializationRequest.model_validate(
                request.model_dump(mode="python", round_trip=True, warnings="none")
            )
        except (ValidationError, AttributeError, TypeError, ValueError) as exc:
            raise GlobalEventMaterializationInvalidInputError("request must be canonical") from exc

    @staticmethod
    def _events(
        events: Sequence[ObservedGlobalEvent], request: GlobalEventMaterializationRequest
    ) -> tuple[ObservedGlobalEvent, ...]:
        if not isinstance(events, Sequence) or isinstance(events, (str, bytes)):
            raise GlobalEventMaterializationInvalidInputError("events must be a sequence")
        output: list[ObservedGlobalEvent] = []
        previous: tuple[datetime, UUID] | None = None
        identities: set[UUID] = set()
        for event in events:
            if not isinstance(event, ObservedGlobalEvent):
                raise GlobalEventMaterializationInvalidInputError("events must be canonical")
            try:
                item = ObservedGlobalEvent.model_validate(
                    event.model_dump(mode="python", round_trip=True, warnings="none")
                )
            except (ValidationError, AttributeError, TypeError, ValueError) as exc:
                raise GlobalEventMaterializationInvalidInputError(
                    "events must be canonical"
                ) from exc
            key = (item.observed_at, item.observation_id)
            if previous is not None and key <= previous:
                raise GlobalEventMaterializationInvalidInputError(
                    "events must be in strict canonical knowledge order"
                )
            if item.observation_id in identities:
                raise GlobalEventMaterializationInvalidInputError("duplicate observation ID")
            if item.observed_at > request.as_of:
                raise GlobalEventMaterializationInvalidInputError("event is not known by as_of")
            output.append(item)
            identities.add(item.observation_id)
            previous = key
        return tuple(output)
