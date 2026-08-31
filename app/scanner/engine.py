"""Stateless deterministic projection of supplied Phase 9 setup universes."""

from typing import Protocol

from pydantic import ValidationError

from app.scanner.models import (
    InstrumentScanResult,
    ScannerSnapshot,
    SetupUniverseSnapshot,
    _revalidate_setup_snapshot,
)
from app.setups.models import SETUP_KEY_ORDER, SetupStatus


class ScannerError(Exception):
    """Base exception for deterministic scanning."""


class ScannerInvalidInputError(ScannerError):
    """Raised when a supplied setup universe is malformed."""


class ScannerComputationError(ScannerError):
    """Raised when valid input cannot produce trustworthy scanner output."""


class ScannerEngine(Protocol):
    """Narrow deterministic multi-instrument scan boundary."""

    def scan(self, universe: SetupUniverseSnapshot) -> ScannerSnapshot:
        """Return complete states with exact ACTIVE projections."""
        ...


class DeterministicScannerEngine:
    """Pure Phase 10 scanner reference implementation."""

    def scan(self, universe: SetupUniverseSnapshot) -> ScannerSnapshot:
        """Defensively validate and project the explicitly supplied universe once."""
        trusted = self._revalidate_universe(universe)
        try:
            results = tuple(
                InstrumentScanResult(
                    setup_snapshot=snapshot,
                    active_setup_keys=tuple(
                        key
                        for key in SETUP_KEY_ORDER
                        if next(item for item in snapshot.setups if item.key is key).status
                        is SetupStatus.ACTIVE
                    ),
                )
                for snapshot in trusted.setup_snapshots
            )
            return ScannerSnapshot(
                scan_as_of=trusted.scan_as_of,
                timeframe=trusted.timeframe,
                results=results,
            )
        except (ValidationError, AttributeError, TypeError, ValueError) as exc:
            raise ScannerComputationError(
                "failed to construct trustworthy canonical scanner output"
            ) from exc

    @staticmethod
    def _revalidate_universe(universe: SetupUniverseSnapshot) -> SetupUniverseSnapshot:
        if not isinstance(universe, SetupUniverseSnapshot):
            raise ScannerInvalidInputError(
                "universe must be a validated SetupUniverseSnapshot"
            )
        try:
            return SetupUniverseSnapshot(
                scan_as_of=universe.scan_as_of,
                timeframe=universe.timeframe,
                setup_snapshots=tuple(
                    _revalidate_setup_snapshot(snapshot)
                    for snapshot in universe.setup_snapshots
                ),
            )
        except (ValidationError, AttributeError, TypeError, ValueError) as exc:
            raise ScannerInvalidInputError(
                "universe must be a validated SetupUniverseSnapshot"
            ) from exc
