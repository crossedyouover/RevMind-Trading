"""Pure orchestration of the frozen deterministic single-series research stages."""

from typing import Protocol

from pydantic import ValidationError

from app.evidence.engine import (
    DeterministicMarketEvidenceEngine,
    MarketEvidenceEngine,
    MarketEvidenceError,
)
from app.evidence.models import AlignedTechnicalHistory
from app.research.models import SingleSeriesResearchRequest, SingleSeriesResearchResult
from app.setups.engine import (
    DeterministicSetupCompositionEngine,
    SetupCompositionEngine,
    SetupCompositionError,
)
from app.setups.models import AlignedEvidenceHistory
from app.technical.engine import (
    DeterministicTechnicalAnalysisEngine,
    TechnicalAnalysisEngine,
    TechnicalAnalysisError,
)


class SingleSeriesResearchError(Exception):
    """Base error for deterministic single-series research composition."""


class SingleSeriesResearchInvalidInputError(SingleSeriesResearchError):
    """Raised when the supplied Phase 14 request cannot be trusted."""


class SingleSeriesResearchComputationError(SingleSeriesResearchError):
    """Raised when a downstream stage cannot produce a trustworthy result."""


class SingleSeriesResearchEngine(Protocol):
    """Narrow boundary for deterministic single-series research composition."""

    def analyze(self, request: SingleSeriesResearchRequest) -> SingleSeriesResearchResult:
        """Run the frozen deterministic analytical stages exactly once."""
        ...


class DeterministicSingleSeriesResearchEngine:
    """Compose Phase 7, 8, and 9 while retaining the complete Phase 13 input."""

    def __init__(
        self,
        technical_engine: TechnicalAnalysisEngine | None = None,
        evidence_engine: MarketEvidenceEngine | None = None,
        setup_engine: SetupCompositionEngine | None = None,
    ) -> None:
        self._technical_engine = (
            technical_engine
            if technical_engine is not None
            else DeterministicTechnicalAnalysisEngine()
        )
        self._evidence_engine = (
            evidence_engine
            if evidence_engine is not None
            else DeterministicMarketEvidenceEngine()
        )
        self._setup_engine = (
            setup_engine if setup_engine is not None else DeterministicSetupCompositionEngine()
        )

    def analyze(self, request: SingleSeriesResearchRequest) -> SingleSeriesResearchResult:
        """Compose without storage, providers, clocks, scanning, risk, or execution."""
        trusted = self._request(request)
        bars = tuple(item.bar for item in trusted.history.bars)
        try:
            technical = self._technical_engine.analyze(bars, trusted.technical_config)
            evidence = self._evidence_engine.analyze(
                AlignedTechnicalHistory(bars=bars, technical_snapshots=technical),
                trusted.evidence_config,
            )
            setups = self._setup_engine.analyze(AlignedEvidenceHistory(snapshots=evidence))
            return SingleSeriesResearchResult(
                request=trusted,
                technical_snapshots=technical,
                evidence_snapshots=evidence,
                setup_snapshots=setups,
            )
        except (
            TechnicalAnalysisError,
            MarketEvidenceError,
            SetupCompositionError,
            ValidationError,
            AttributeError,
            TypeError,
            ValueError,
        ) as exc:
            raise SingleSeriesResearchComputationError(
                "single-series deterministic research stage failed"
            ) from exc

    @staticmethod
    def _request(request: SingleSeriesResearchRequest) -> SingleSeriesResearchRequest:
        if not isinstance(request, SingleSeriesResearchRequest):
            raise SingleSeriesResearchInvalidInputError(
                "request must be a validated SingleSeriesResearchRequest"
            )
        try:
            return SingleSeriesResearchRequest(
                history=request.history,
                technical_config=request.technical_config,
                evidence_config=request.evidence_config,
            )
        except (ValidationError, AttributeError, TypeError, ValueError) as exc:
            raise SingleSeriesResearchInvalidInputError(
                "request must be a validated SingleSeriesResearchRequest"
            ) from exc
