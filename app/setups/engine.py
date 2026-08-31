"""Stateless deterministic composition of current-snapshot Phase 8 evidence."""

from typing import Protocol

from pydantic import ValidationError

from app.evidence.models import EvidenceSnapshot, MarketEvidence, MarketEvidenceKey
from app.setups.models import (
    SETUP_KEY_ORDER,
    AlignedEvidenceHistory,
    SetupEvidenceReference,
    SetupHypothesis,
    SetupKey,
    SetupSnapshot,
    _derive_setup_status,
    _required_evidence_keys,
)


class SetupCompositionError(Exception):
    """Base exception for deterministic setup composition."""


class SetupCompositionInvalidInputError(SetupCompositionError):
    """Raised when supplied Phase 8 evidence history is invalid."""


class SetupCompositionComputationError(SetupCompositionError):
    """Raised when valid evidence cannot produce trustworthy setup output."""


class SetupCompositionEngine(Protocol):
    """Narrow batch boundary for deterministic setup composition."""

    def analyze(self, history: AlignedEvidenceHistory) -> tuple[SetupSnapshot, ...]:
        """Return one complete setup snapshot per evidence snapshot."""
        ...


class DeterministicSetupCompositionEngine:
    """Pure, current-snapshot-only Phase 9 reference implementation."""

    def analyze(self, history: AlignedEvidenceHistory) -> tuple[SetupSnapshot, ...]:
        """Compose the frozen catalogue without history scans or hidden state."""
        if not isinstance(history, AlignedEvidenceHistory):
            raise SetupCompositionInvalidInputError(
                "history must be a validated AlignedEvidenceHistory"
            )
        history = self._revalidate_history(history)
        try:
            return tuple(self._compose_snapshot(snapshot) for snapshot in history.snapshots)
        except ValidationError as exc:
            raise SetupCompositionComputationError(
                "failed to construct trustworthy canonical setup output"
            ) from exc

    @staticmethod
    def _revalidate_history(history: AlignedEvidenceHistory) -> AlignedEvidenceHistory:
        """Reject malformed low-level model copies at the public engine boundary."""
        try:
            snapshots = tuple(
                EvidenceSnapshot.model_validate(
                    snapshot.model_dump(
                        mode="python", round_trip=True, warnings="none"
                    )
                )
                for snapshot in history.snapshots
            )
            return AlignedEvidenceHistory(snapshots=snapshots)
        except (ValidationError, AttributeError, TypeError, ValueError) as exc:
            raise SetupCompositionInvalidInputError(
                "history must be a validated AlignedEvidenceHistory"
            ) from exc

    @classmethod
    def _compose_snapshot(cls, snapshot: EvidenceSnapshot) -> SetupSnapshot:
        evidence_index = cls._index_evidence(snapshot)
        setups = tuple(
            cls._compose_hypothesis(snapshot, evidence_index, key)
            for key in SETUP_KEY_ORDER
        )
        return SetupSnapshot(
            instrument=snapshot.instrument,
            timeframe=snapshot.timeframe,
            timestamp=snapshot.timestamp,
            setups=setups,
        )

    @staticmethod
    def _index_evidence(snapshot: EvidenceSnapshot) -> dict[MarketEvidenceKey, MarketEvidence]:
        """Build one exact current-snapshot evidence index without substitution."""
        index = {evidence.key: evidence for evidence in snapshot.evidence}
        if len(index) != len(snapshot.evidence):
            raise SetupCompositionInvalidInputError("evidence keys must be unique")
        return index

    @staticmethod
    def _compose_hypothesis(
        snapshot: EvidenceSnapshot,
        evidence_index: dict[MarketEvidenceKey, MarketEvidence],
        key: SetupKey,
    ) -> SetupHypothesis:
        references: list[SetupEvidenceReference] = []
        for evidence_key in _required_evidence_keys(key):
            try:
                evidence = evidence_index[evidence_key]
            except KeyError as exc:
                raise SetupCompositionInvalidInputError(
                    f"snapshot lacks required evidence {evidence_key.value}"
                ) from exc
            references.append(
                SetupEvidenceReference(
                    timestamp=snapshot.timestamp,
                    key=evidence.key,
                    status=evidence.status,
                )
            )
        reference_tuple = tuple(references)
        return SetupHypothesis(
            key=key,
            status=_derive_setup_status(
                tuple(reference.status for reference in reference_tuple)
            ),
            evidence_references=reference_tuple,
        )
