"""Immutable contracts for deterministic single-series research composition."""

from pydantic import ValidationError, model_validator

from app.core.schemas import CanonicalModel
from app.evidence.models import EvidenceSnapshot, MarketEvidenceConfig
from app.materialization.models import MaterializedBarHistory
from app.setups.models import SetupSnapshot
from app.technical.models import TechnicalAnalysisConfig, TechnicalSnapshot


class SingleSeriesResearchRequest(CanonicalModel):
    """One PIT materialized history and explicit deterministic analysis configuration."""

    history: MaterializedBarHistory
    technical_config: TechnicalAnalysisConfig
    evidence_config: MarketEvidenceConfig

    @model_validator(mode="after")
    def revalidate_nested_contracts(self) -> "SingleSeriesResearchRequest":
        """Never trust bypassed or shallow-copied nested state."""
        try:
            history = MaterializedBarHistory(
                request=self.history.request,
                bars=self.history.bars,
                inspected_observation_count=self.history.inspected_observation_count,
                eligible_bar_candidate_count=self.history.eligible_bar_candidate_count,
            )
            technical_config = TechnicalAnalysisConfig.model_validate(
                self.technical_config.model_dump(
                    mode="python", round_trip=True, warnings="none"
                )
            )
            evidence_config = MarketEvidenceConfig.model_validate(
                self.evidence_config.model_dump(
                    mode="python", round_trip=True, warnings="none"
                )
            )
        except (ValidationError, AttributeError, TypeError, ValueError) as exc:
            raise ValueError("research request contains noncanonical nested state") from exc
        object.__setattr__(self, "history", history)
        object.__setattr__(self, "technical_config", technical_config)
        object.__setattr__(self, "evidence_config", evidence_config)
        return self


class SingleSeriesResearchResult(CanonicalModel):
    """Complete aligned Phase 13, 7, 8, and 9 state for one requested series."""

    request: SingleSeriesResearchRequest
    technical_snapshots: tuple[TechnicalSnapshot, ...]
    evidence_snapshots: tuple[EvidenceSnapshot, ...]
    setup_snapshots: tuple[SetupSnapshot, ...]

    @model_validator(mode="after")
    def validate_complete_alignment(self) -> "SingleSeriesResearchResult":
        """Require exact count, identity, timeframe, and timestamp alignment."""
        try:
            request = SingleSeriesResearchRequest(
                history=self.request.history,
                technical_config=self.request.technical_config,
                evidence_config=self.request.evidence_config,
            )
        except (ValidationError, AttributeError, TypeError, ValueError) as exc:
            raise ValueError("research result request must be canonical") from exc
        object.__setattr__(self, "request", request)
        try:
            technical_snapshots = tuple(
                TechnicalSnapshot.model_validate(
                    item.model_dump(mode="python", round_trip=True, warnings="none")
                )
                for item in self.technical_snapshots
            )
            evidence_snapshots = tuple(
                EvidenceSnapshot.model_validate(
                    item.model_dump(mode="python", round_trip=True, warnings="none")
                )
                for item in self.evidence_snapshots
            )
            setup_snapshots = tuple(
                SetupSnapshot.model_validate(
                    item.model_dump(mode="python", round_trip=True, warnings="none")
                )
                for item in self.setup_snapshots
            )
        except (ValidationError, AttributeError, TypeError, ValueError) as exc:
            raise ValueError("research result contains noncanonical stage output") from exc
        object.__setattr__(self, "technical_snapshots", technical_snapshots)
        object.__setattr__(self, "evidence_snapshots", evidence_snapshots)
        object.__setattr__(self, "setup_snapshots", setup_snapshots)
        bars = tuple(item.bar for item in request.history.bars)
        expected_count = len(bars)
        collections = (
            technical_snapshots,
            evidence_snapshots,
            setup_snapshots,
        )
        if any(len(items) != expected_count for items in collections):
            raise ValueError("all research stages must align one-to-one with materialized bars")
        for index, bar in enumerate(bars):
            for snapshot in (
                technical_snapshots[index],
                evidence_snapshots[index],
                setup_snapshots[index],
            ):
                if snapshot.instrument != bar.instrument:
                    raise ValueError("research snapshot instrument must match materialized bar")
                if snapshot.timeframe is not bar.timeframe:
                    raise ValueError("research snapshot timeframe must match materialized bar")
                if snapshot.timestamp != bar.timestamp:
                    raise ValueError("research snapshot timestamp must match materialized bar")
        return self
