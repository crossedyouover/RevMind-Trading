"""Pure Head-of-Desk composition; no delivery or execution."""

from decimal import DecimalException
from typing import Protocol

from app.orchestration.models import HeadOfDeskRequest, HeadOfDeskResult, _compose, _rebuild


class HeadOfDeskError(Exception):
    """Base non-promoting composition error."""


class HeadOfDeskInvalidInputError(HeadOfDeskError):
    """Malformed or forged input."""


class HeadOfDeskComputationError(HeadOfDeskError):
    """Frozen input arithmetic validation failed."""


class HeadOfDeskEngine(Protocol):
    def compose(self, request: HeadOfDeskRequest) -> HeadOfDeskResult: ...


class DeterministicHeadOfDeskEngine:
    def compose(self, request: HeadOfDeskRequest) -> HeadOfDeskResult:
        try:
            try:
                if type(request) is not HeadOfDeskRequest:
                    raise ValueError("expected HeadOfDeskRequest")
                trusted = _rebuild(request)
                if not isinstance(trusted, HeadOfDeskRequest):
                    raise ValueError("invalid reconstructed request")
            except (AttributeError, TypeError, ValueError) as exc:
                raise HeadOfDeskInvalidInputError("invalid composition input") from exc
            disposition, reasons, setup, trend = _compose(trusted)
            return HeadOfDeskResult(
                request=trusted,
                disposition=disposition,
                reasons=reasons,
                selected_setup=setup,
                selected_trend=trend,
            )
        except DecimalException as exc:
            raise HeadOfDeskComputationError(
                "frozen evidence validation arithmetic failed"
            ) from exc
