"""Pure advisory packaging; no upstream computation or I/O."""

from typing import Protocol

from app.desks.models import (
    CatalystDeskReport,
    CatalystDeskRequest,
    InsiderDeskReport,
    InsiderDeskRequest,
    SetupDeskReport,
    SetupDeskRequest,
    TrendDeskReport,
    TrendDeskRequest,
    _coverage,
    _rebuild,
)


class AdvisoryDeskError(Exception):
    """Base advisory boundary error."""


class AdvisoryDeskInvalidInputError(AdvisoryDeskError):
    """Invalid evidence never becomes an empty report."""


def _trusted[T](request: object, expected: type[T]) -> T:
    try:
        if type(request) is not expected:
            raise ValueError("wrong desk request type")
        rebuilt = _rebuild(request)
        if not isinstance(rebuilt, expected):
            raise ValueError("invalid reconstructed request")
        return rebuilt
    except (AttributeError, TypeError, ValueError) as exc:
        raise AdvisoryDeskInvalidInputError("invalid advisory desk request") from exc


class AdvisoryDeskEngine(Protocol):
    def catalyst(self, request: CatalystDeskRequest) -> CatalystDeskReport: ...
    def insider(self, request: InsiderDeskRequest) -> InsiderDeskReport: ...
    def trend(self, request: TrendDeskRequest) -> TrendDeskReport: ...
    def setup(self, request: SetupDeskRequest) -> SetupDeskReport: ...


class DeterministicAdvisoryDeskEngine:
    """Retain complete evidence; presence never implies actionability."""

    def catalyst(self, request: CatalystDeskRequest) -> CatalystDeskReport:
        trusted = _trusted(request, CatalystDeskRequest)
        return CatalystDeskReport(request=trusted, coverage=_coverage(trusted.payload))

    def insider(self, request: InsiderDeskRequest) -> InsiderDeskReport:
        trusted = _trusted(request, InsiderDeskRequest)
        return InsiderDeskReport(request=trusted, coverage=_coverage(trusted.payload))

    def trend(self, request: TrendDeskRequest) -> TrendDeskReport:
        trusted = _trusted(request, TrendDeskRequest)
        return TrendDeskReport(request=trusted, coverage=_coverage(trusted.payload))

    def setup(self, request: SetupDeskRequest) -> SetupDeskReport:
        trusted = _trusted(request, SetupDeskRequest)
        return SetupDeskReport(request=trusted, coverage=_coverage(trusted.payload))
