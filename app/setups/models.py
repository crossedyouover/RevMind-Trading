"""Immutable contracts for deterministic Phase 9 setup composition."""

from enum import StrEnum

from pydantic import ValidationError, ValidationInfo, field_validator, model_validator

from app.core.schemas import CanonicalModel, Instrument, Timeframe, UtcDatetime
from app.evidence.models import (
    MARKET_EVIDENCE_KEY_ORDER,
    EvidenceSnapshot,
    MarketEvidenceKey,
    MarketEvidenceStatus,
)


class SetupKey(StrEnum):
    """Closed, descriptive Phase 9 setup catalogue."""

    UPSIDE_BREAKOUT_ABOVE_SMA = "UPSIDE_BREAKOUT_ABOVE_SMA"
    DOWNSIDE_BREAKDOWN_BELOW_SMA = "DOWNSIDE_BREAKDOWN_BELOW_SMA"


SETUP_KEY_ORDER: tuple[SetupKey, ...] = tuple(SetupKey)


class SetupStatus(StrEnum):
    """Availability and satisfaction state of one setup hypothesis."""

    WARMING_UP = "WARMING_UP"
    UNDEFINED = "UNDEFINED"
    INACTIVE = "INACTIVE"
    ACTIVE = "ACTIVE"


_UPSIDE_EVIDENCE = (
    MarketEvidenceKey.PRICE_ABOVE_SMA,
    MarketEvidenceKey.CLOSE_BREAKOUT_ABOVE_PRIOR_HIGH,
)
_DOWNSIDE_EVIDENCE = (
    MarketEvidenceKey.PRICE_BELOW_SMA,
    MarketEvidenceKey.CLOSE_BREAKDOWN_BELOW_PRIOR_LOW,
)


def _required_evidence_keys(key: SetupKey) -> tuple[MarketEvidenceKey, ...]:
    """Return one setup's frozen evidence keys in Phase 8 canonical order."""
    required = (
        _UPSIDE_EVIDENCE
        if key is SetupKey.UPSIDE_BREAKOUT_ABOVE_SMA
        else _DOWNSIDE_EVIDENCE
    )
    return tuple(
        evidence_key
        for evidence_key in MARKET_EVIDENCE_KEY_ORDER
        if evidence_key in required
    )


def _derive_setup_status(statuses: tuple[MarketEvidenceStatus, ...]) -> SetupStatus:
    """Apply availability precedence before the frozen all-active conjunction."""
    if MarketEvidenceStatus.WARMING_UP in statuses:
        return SetupStatus.WARMING_UP
    if MarketEvidenceStatus.UNDEFINED in statuses:
        return SetupStatus.UNDEFINED
    if all(status is MarketEvidenceStatus.ACTIVE for status in statuses):
        return SetupStatus.ACTIVE
    return SetupStatus.INACTIVE


class AlignedEvidenceHistory(CanonicalModel):
    """Structurally valid, chronologically ordered Phase 8 evidence snapshots."""

    snapshots: tuple[EvidenceSnapshot, ...]

    @field_validator("snapshots", mode="before")
    @classmethod
    def require_canonical_tuple(cls, value: object, info: ValidationInfo) -> object:
        """Require immutable canonical objects in normal Python construction."""
        if not isinstance(value, tuple):
            raise ValueError("evidence history snapshots must be supplied as a tuple")
        if info.mode == "python" and not all(
            isinstance(snapshot, EvidenceSnapshot) for snapshot in value
        ):
            raise ValueError("evidence history must contain actual EvidenceSnapshot objects")
        return value

    @model_validator(mode="after")
    def validate_history(self) -> "AlignedEvidenceHistory":
        """Reject mixed identity, mixed timeframe, and non-increasing time."""
        if not self.snapshots:
            return self
        first = self.snapshots[0]
        previous = first.timestamp
        for snapshot in self.snapshots[1:]:
            if snapshot.instrument != first.instrument:
                raise ValueError("evidence history contains mixed instruments")
            if snapshot.timeframe is not first.timeframe:
                raise ValueError("evidence history contains mixed timeframes")
            if snapshot.timestamp <= previous:
                if snapshot.timestamp == previous:
                    raise ValueError("evidence history contains duplicate timestamps")
                raise ValueError("evidence history timestamps must be strictly increasing")
            previous = snapshot.timestamp
        return self


class SetupEvidenceReference(CanonicalModel):
    """Typed reference to one actual current-snapshot Phase 8 evidence operand."""

    timestamp: UtcDatetime
    key: MarketEvidenceKey
    status: MarketEvidenceStatus


class SetupHypothesis(CanonicalModel):
    """Complete deterministic state and provenance for one frozen setup."""

    key: SetupKey
    status: SetupStatus
    evidence_references: tuple[SetupEvidenceReference, ...]

    @model_validator(mode="after")
    def validate_hypothesis(self) -> "SetupHypothesis":
        """Reject incomplete, reordered, duplicate, or contradictory setup state."""
        try:
            for reference in self.evidence_references:
                SetupEvidenceReference(
                    timestamp=reference.timestamp,
                    key=reference.key,
                    status=reference.status,
                )
                if not isinstance(reference.key, MarketEvidenceKey) or not isinstance(
                    reference.status, MarketEvidenceStatus
                ):
                    raise ValueError("setup evidence references must use canonical enum values")
        except (ValidationError, AttributeError, TypeError) as exc:
            raise ValueError("setup evidence references must be canonical") from exc
        keys = tuple(reference.key for reference in self.evidence_references)
        expected = _required_evidence_keys(self.key)
        if keys != expected:
            raise ValueError("setup evidence references must match the exact canonical definition")
        if len(keys) != len(set(keys)):
            raise ValueError("setup evidence references must be unique")
        timestamps = tuple(reference.timestamp for reference in self.evidence_references)
        if len(set(timestamps)) != 1:
            raise ValueError("setup evidence reference timestamps must match")
        implied = _derive_setup_status(
            tuple(reference.status for reference in self.evidence_references)
        )
        if self.status is not implied:
            raise ValueError("setup status contradicts its evidence-reference statuses")
        return self


class SetupSnapshot(CanonicalModel):
    """Complete canonical Phase 9 setup state for one evidence snapshot."""

    instrument: Instrument
    timeframe: Timeframe
    timestamp: UtcDatetime
    setups: tuple[SetupHypothesis, ...]

    @model_validator(mode="after")
    def validate_complete_setups(self) -> "SetupSnapshot":
        """Require the exact frozen catalogue and timestamp-linked provenance."""
        try:
            for setup in self.setups:
                SetupHypothesis(
                    key=setup.key,
                    status=setup.status,
                    evidence_references=setup.evidence_references,
                )
                if not isinstance(setup.key, SetupKey) or not isinstance(
                    setup.status, SetupStatus
                ):
                    raise ValueError("setup snapshot entries must use canonical enum values")
        except (ValidationError, AttributeError, TypeError) as exc:
            raise ValueError("setup snapshot entries must be canonical") from exc
        keys = tuple(setup.key for setup in self.setups)
        if keys != SETUP_KEY_ORDER:
            raise ValueError("setup snapshot must contain the complete canonical setup catalogue")
        if any(
            reference.timestamp != self.timestamp
            for setup in self.setups
            for reference in setup.evidence_references
        ):
            raise ValueError("setup evidence references must match the setup snapshot timestamp")
        return self
