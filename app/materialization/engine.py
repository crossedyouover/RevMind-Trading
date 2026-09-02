"""Pure deterministic point-in-time materialization of canonical market bars."""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from pydantic import ValidationError

from app.core.schemas import MarketBar
from app.data.observations import ObservedMarketData
from app.materialization.models import BarSeriesRequest, MaterializedBar, MaterializedBarHistory


class BarMaterializationError(Exception):
    """Base error for deterministic bar materialization."""


class BarMaterializationInvalidInputError(BarMaterializationError):
    """Raised when the request or observation history cannot be trusted."""


class BarMaterializationComputationError(BarMaterializationError):
    """Raised when validated input cannot produce canonical output."""


class BarMaterializationEngine(Protocol):
    """Narrow pure boundary for point-in-time bar materialization."""

    def materialize(
        self,
        observations: Sequence[ObservedMarketData],
        request: BarSeriesRequest,
    ) -> MaterializedBarHistory:
        """Return a deterministic analysis-ready bar history."""
        ...


class DeterministicBarMaterializationEngine:
    """Select the latest explicitly sourced bar version known at a fixed cutoff."""

    def materialize(
        self,
        observations: Sequence[ObservedMarketData],
        request: BarSeriesRequest,
    ) -> MaterializedBarHistory:
        """Materialize without I/O, input repair, source blending, or hidden state."""
        trusted_request = self._request(request)
        trusted_observations = self._observations(observations, trusted_request)
        candidates = tuple(
            observation
            for observation in trusted_observations
            if self._matches(observation, trusted_request)
        )

        selected: dict[datetime, ObservedMarketData] = {}
        for observation in candidates:
            payload = observation.payload
            if isinstance(payload, MarketBar):
                selected[payload.timestamp] = observation

        try:
            bars = tuple(
                MaterializedBar.from_observation(selected[timestamp])
                for timestamp in sorted(selected)
            )
            return MaterializedBarHistory(
                request=trusted_request,
                bars=bars,
                inspected_observation_count=len(trusted_observations),
                eligible_bar_candidate_count=len(candidates),
            )
        except (ValidationError, AttributeError, TypeError, ValueError) as exc:
            raise BarMaterializationComputationError(
                "failed to construct trustworthy materialized bar history"
            ) from exc

    @staticmethod
    def _request(request: BarSeriesRequest) -> BarSeriesRequest:
        if not isinstance(request, BarSeriesRequest):
            raise BarMaterializationInvalidInputError(
                "request must be a validated BarSeriesRequest"
            )
        try:
            return BarSeriesRequest.model_validate(
                request.model_dump(mode="python", round_trip=True, warnings="none")
            )
        except (ValidationError, AttributeError, TypeError, ValueError) as exc:
            raise BarMaterializationInvalidInputError(
                "request must be a validated BarSeriesRequest"
            ) from exc

    @staticmethod
    def _observations(
        observations: Sequence[ObservedMarketData], request: BarSeriesRequest
    ) -> tuple[ObservedMarketData, ...]:
        if not isinstance(observations, Sequence) or isinstance(observations, (str, bytes)):
            raise BarMaterializationInvalidInputError(
                "observations must be a sequence of ObservedMarketData"
            )
        trusted: list[ObservedMarketData] = []
        previous: tuple[datetime, UUID] | None = None
        identifiers: set[UUID] = set()
        for observation in observations:
            if not isinstance(observation, ObservedMarketData):
                raise BarMaterializationInvalidInputError(
                    "observations must contain only ObservedMarketData"
                )
            try:
                item = ObservedMarketData.model_validate(
                    observation.model_dump(mode="python", round_trip=True, warnings="none")
                )
            except (ValidationError, AttributeError, TypeError, ValueError) as exc:
                raise BarMaterializationInvalidInputError(
                    "observations must contain canonical ObservedMarketData"
                ) from exc
            key = (item.observed_at, item.observation_id)
            if previous is not None and key <= previous:
                raise BarMaterializationInvalidInputError(
                    "observations must be in strict canonical knowledge order"
                )
            if item.observation_id in identifiers:
                raise BarMaterializationInvalidInputError(
                    "observations contain a duplicate observation ID"
                )
            if item.observed_at > request.as_of:
                raise BarMaterializationInvalidInputError(
                    "observation is not known by request as_of"
                )
            trusted.append(item)
            identifiers.add(item.observation_id)
            previous = key
        return tuple(trusted)

    @staticmethod
    def _matches(observation: ObservedMarketData, request: BarSeriesRequest) -> bool:
        payload = observation.payload
        if not isinstance(payload, MarketBar):
            return False
        if observation.source != request.source:
            return False
        if payload.instrument != request.instrument or payload.timeframe is not request.timeframe:
            return False
        if request.start is not None and payload.timestamp < request.start:
            return False
        return request.end is None or payload.timestamp < request.end
