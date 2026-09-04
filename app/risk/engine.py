"""Pure paper risk checks; passing is never an executable approval."""

from decimal import DecimalException
from typing import Protocol

from app.risk.models import PaperRiskRequest, PaperRiskResult, _evaluate, _rebuild


class PaperRiskError(Exception):
    """Base non-passing risk evaluation failure."""


class PaperRiskInvalidInputError(PaperRiskError):
    """Malformed policy, proposal, or context."""


class PaperRiskComputationError(PaperRiskError):
    """Risk arithmetic could not complete safely."""


class PaperRiskEngine(Protocol):
    def evaluate(self, request: PaperRiskRequest) -> PaperRiskResult: ...


class DeterministicPaperRiskEngine:
    def evaluate(self, request: PaperRiskRequest) -> PaperRiskResult:
        try:
            try:
                if type(request) is not PaperRiskRequest:
                    raise ValueError("expected PaperRiskRequest")
                trusted = _rebuild(request)
                if not isinstance(trusted, PaperRiskRequest):
                    raise ValueError("invalid reconstructed request")
            except (AttributeError, TypeError, ValueError) as exc:
                raise PaperRiskInvalidInputError("invalid paper risk input") from exc
            status, reasons, projection = _evaluate(trusted)
            return PaperRiskResult(
                request=trusted, status=status, reasons=reasons, projection=projection
            )
        except DecimalException as exc:
            raise PaperRiskComputationError("paper risk arithmetic failed") from exc
