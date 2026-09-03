"""Deterministic coordination of Phase 14 results into the frozen scanner."""

from typing import Protocol

from pydantic import ValidationError

from app.scanner.engine import DeterministicScannerEngine, ScannerEngine, ScannerError
from app.scanner.models import SetupUniverseSnapshot
from app.universe.models import (
    UniverseCoordinationRequest,
    UniverseCoordinationResult,
    UniverseSeriesSelection,
    UniverseSeriesStatus,
)


class UniverseCoordinationError(Exception):
    """Base error for deterministic universe coordination."""


class UniverseCoordinationInvalidInputError(UniverseCoordinationError):
    """Raised when a universe request cannot be trusted."""


class UniverseCoordinationComputationError(UniverseCoordinationError):
    """Raised when canonical scanner output cannot be produced."""


class UniverseCoordinationEngine(Protocol):
    def coordinate(self, request: UniverseCoordinationRequest) -> UniverseCoordinationResult: ...


class DeterministicUniverseCoordinationEngine:
    """Retain every series and scan exactly the setup states eligible at scan_as_of."""

    def __init__(self, scanner: ScannerEngine | None = None) -> None:
        self._scanner = scanner if scanner is not None else DeterministicScannerEngine()

    def coordinate(self, request: UniverseCoordinationRequest) -> UniverseCoordinationResult:
        trusted = self._request(request)
        selections = []
        for result in trusted.series_results:
            eligible = tuple(
                snapshot
                for snapshot in result.setup_snapshots
                if snapshot.timestamp <= trusted.scan_as_of
            )
            selected = eligible[-1] if eligible else None
            selections.append(
                UniverseSeriesSelection(
                    series_result=result,
                    status=(
                        UniverseSeriesStatus.AVAILABLE
                        if selected is not None
                        else UniverseSeriesStatus.NO_ELIGIBLE_HISTORY
                    ),
                    selected_setup=selected,
                )
            )
        snapshots = tuple(
            item.selected_setup for item in selections if item.selected_setup is not None
        )
        try:
            scanner_snapshot = self._scanner.scan(
                SetupUniverseSnapshot(
                    scan_as_of=trusted.scan_as_of,
                    timeframe=trusted.timeframe,
                    setup_snapshots=snapshots,
                )
            )
            return UniverseCoordinationResult(
                request=trusted,
                selections=tuple(selections),
                scanner_snapshot=scanner_snapshot,
            )
        except (ScannerError, ValidationError, AttributeError, TypeError, ValueError) as exc:
            raise UniverseCoordinationComputationError(
                "deterministic universe coordination failed"
            ) from exc

    @staticmethod
    def _request(request: UniverseCoordinationRequest) -> UniverseCoordinationRequest:
        if not isinstance(request, UniverseCoordinationRequest):
            raise UniverseCoordinationInvalidInputError(
                "request must be a validated UniverseCoordinationRequest"
            )
        try:
            return UniverseCoordinationRequest(
                knowledge_as_of=request.knowledge_as_of,
                scan_as_of=request.scan_as_of,
                timeframe=request.timeframe,
                series_results=request.series_results,
            )
        except (ValidationError, AttributeError, TypeError, ValueError) as exc:
            raise UniverseCoordinationInvalidInputError(
                "request must be a validated UniverseCoordinationRequest"
            ) from exc
