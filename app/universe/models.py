"""Immutable contracts for deterministic multi-instrument universe coordination."""

from enum import StrEnum

from pydantic import ValidationError, ValidationInfo, field_validator, model_validator

from app.core.schemas import CanonicalModel, Timeframe, UtcDatetime
from app.research.models import SingleSeriesResearchResult
from app.scanner.models import ScannerSnapshot, canonical_instrument_key
from app.setups.models import SetupSnapshot


def revalidate_series(result: SingleSeriesResearchResult) -> SingleSeriesResearchResult:
    """Reconstruct one Phase 14 result through its frozen public contract."""
    return SingleSeriesResearchResult(
        request=result.request,
        technical_snapshots=result.technical_snapshots,
        evidence_snapshots=result.evidence_snapshots,
        setup_snapshots=result.setup_snapshots,
    )


class UniverseCoordinationRequest(CanonicalModel):
    """Explicit PIT and scan boundaries over a canonical ordered series collection."""

    knowledge_as_of: UtcDatetime
    scan_as_of: UtcDatetime
    timeframe: Timeframe
    series_results: tuple[SingleSeriesResearchResult, ...]

    @field_validator("series_results", mode="before")
    @classmethod
    def require_tuple(cls, value: object, info: ValidationInfo) -> object:
        if info.mode == "python" and not isinstance(value, tuple):
            raise ValueError("series results must be supplied as a tuple")
        return value

    @model_validator(mode="after")
    def validate_universe(self) -> "UniverseCoordinationRequest":
        try:
            results = tuple(revalidate_series(item) for item in self.series_results)
        except (ValidationError, AttributeError, TypeError, ValueError) as exc:
            raise ValueError("series results must be canonical") from exc
        previous = None
        for result in results:
            source = result.request.history.request
            if source.as_of != self.knowledge_as_of:
                raise ValueError("every series must share knowledge_as_of")
            if source.timeframe is not self.timeframe:
                raise ValueError("every series must share the universe timeframe")
            identity = canonical_instrument_key(source.instrument)
            if previous is not None and identity <= previous:
                raise ValueError("series must be unique and in strict canonical instrument order")
            previous = identity
        object.__setattr__(self, "series_results", results)
        return self


class UniverseSeriesStatus(StrEnum):
    """Whether a series has a setup snapshot eligible at the scan boundary."""

    AVAILABLE = "AVAILABLE"
    NO_ELIGIBLE_HISTORY = "NO_ELIGIBLE_HISTORY"


class UniverseSeriesSelection(CanonicalModel):
    """Complete retained Phase 14 series plus its optional selected setup state."""

    series_result: SingleSeriesResearchResult
    status: UniverseSeriesStatus
    selected_setup: SetupSnapshot | None

    @model_validator(mode="after")
    def validate_status(self) -> "UniverseSeriesSelection":
        if (self.selected_setup is None) is (self.status is UniverseSeriesStatus.AVAILABLE):
            raise ValueError("series status must match selected setup availability")
        return self


class UniverseCoordinationResult(CanonicalModel):
    """Complete retained universe state and the frozen Phase 10 scan projection."""

    request: UniverseCoordinationRequest
    selections: tuple[UniverseSeriesSelection, ...]
    scanner_snapshot: ScannerSnapshot

    @model_validator(mode="after")
    def validate_projection(self) -> "UniverseCoordinationResult":
        if len(self.selections) != len(self.request.series_results):
            raise ValueError("every requested series must have one retained selection")
        if self.scanner_snapshot.scan_as_of != self.request.scan_as_of:
            raise ValueError("scanner boundary must match coordination request")
        if self.scanner_snapshot.timeframe is not self.request.timeframe:
            raise ValueError("scanner timeframe must match coordination request")
        selected = tuple(
            item.selected_setup
            for item in self.selections
            if item.selected_setup is not None
        )
        projected = tuple(item.setup_snapshot for item in self.scanner_snapshot.results)
        if selected != projected:
            raise ValueError("scanner projection must exactly match available selections")
        return self
