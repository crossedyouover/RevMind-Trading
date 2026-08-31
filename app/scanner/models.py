"""Immutable contracts for deterministic Phase 10 universe scanning."""

from datetime import datetime

from pydantic import ValidationError, ValidationInfo, field_validator, model_validator

from app.core.schemas import CanonicalModel, Instrument, Timeframe, UtcDatetime
from app.setups.models import SETUP_KEY_ORDER, SetupKey, SetupSnapshot, SetupStatus

type InstrumentIdentity = tuple[str, str, str, str]


def canonical_instrument_key(instrument: Instrument) -> InstrumentIdentity:
    """Return the frozen complete identity key used only for canonical ordering."""
    return (
        instrument.asset_class.value,
        instrument.exchange or "",
        instrument.symbol,
        instrument.currency or "",
    )


def _revalidate_setup_snapshot(snapshot: object) -> SetupSnapshot:
    """Reconstruct a Phase 9 snapshot so copied nested state is never trusted."""
    if not isinstance(snapshot, SetupSnapshot):
        raise ValueError("setup snapshots must be actual SetupSnapshot objects")
    try:
        return SetupSnapshot.model_validate(
            snapshot.model_dump(mode="python", round_trip=True, warnings="none")
        )
    except (ValidationError, AttributeError, TypeError, ValueError) as exc:
        raise ValueError("setup snapshot must be canonical") from exc


def _validate_ordered_snapshots(
    snapshots: tuple[SetupSnapshot, ...], timeframe: Timeframe, scan_as_of: datetime
) -> None:
    previous: InstrumentIdentity | None = None
    for snapshot in snapshots:
        if snapshot.timeframe is not timeframe:
            raise ValueError("setup snapshot timeframe must match the universe timeframe")
        if snapshot.timestamp > scan_as_of:
            raise ValueError("setup snapshot timestamp must not exceed scan_as_of")
        identity = canonical_instrument_key(snapshot.instrument)
        if previous is not None and identity <= previous:
            if identity == previous:
                raise ValueError("setup universe contains a duplicate instrument")
            raise ValueError("setup snapshots must be in strict canonical instrument order")
        previous = identity


class SetupUniverseSnapshot(CanonicalModel):
    """Explicit, single-timeframe collection of point-in-time-prepared setup state."""

    scan_as_of: UtcDatetime
    timeframe: Timeframe
    setup_snapshots: tuple[SetupSnapshot, ...]

    @field_validator("setup_snapshots", mode="before")
    @classmethod
    def require_tuple(cls, value: object, info: ValidationInfo) -> object:
        """Require immutable canonical objects during ordinary Python construction."""
        if info.mode == "python" and not isinstance(value, tuple):
            raise ValueError("setup snapshots must be supplied as a tuple")
        if info.mode == "python" and isinstance(value, tuple) and not all(
            isinstance(snapshot, SetupSnapshot) for snapshot in value
        ):
            raise ValueError("setup universe must contain actual SetupSnapshot objects")
        return value

    @model_validator(mode="after")
    def validate_universe(self) -> "SetupUniverseSnapshot":
        """Reject malformed, mixed, duplicate, future, or noncanonical state."""
        snapshots = tuple(_revalidate_setup_snapshot(item) for item in self.setup_snapshots)
        _validate_ordered_snapshots(snapshots, self.timeframe, self.scan_as_of)
        object.__setattr__(self, "setup_snapshots", snapshots)
        return self


class InstrumentScanResult(CanonicalModel):
    """Complete Phase 9 state plus its exact deterministic ACTIVE projection."""

    setup_snapshot: SetupSnapshot
    active_setup_keys: tuple[SetupKey, ...]

    @field_validator("active_setup_keys", mode="before")
    @classmethod
    def require_active_tuple(cls, value: object, info: ValidationInfo) -> object:
        if info.mode == "python" and not isinstance(value, tuple):
            raise ValueError("active setup keys must be supplied as a tuple")
        return value

    @model_validator(mode="after")
    def validate_projection(self) -> "InstrumentScanResult":
        """Make direct construction as strict as engine-produced output."""
        snapshot = _revalidate_setup_snapshot(self.setup_snapshot)
        expected = tuple(
            key
            for key in SETUP_KEY_ORDER
            if next(item for item in snapshot.setups if item.key is key).status
            is SetupStatus.ACTIVE
        )
        if self.active_setup_keys != expected:
            raise ValueError("active setup keys must exactly match the canonical ACTIVE projection")
        object.__setattr__(self, "setup_snapshot", snapshot)
        return self


class ScannerSnapshot(CanonicalModel):
    """Complete deterministic scan result for one explicit evaluation boundary."""

    scan_as_of: UtcDatetime
    timeframe: Timeframe
    results: tuple[InstrumentScanResult, ...]

    @field_validator("results", mode="before")
    @classmethod
    def require_result_tuple(cls, value: object, info: ValidationInfo) -> object:
        if info.mode == "python" and not isinstance(value, tuple):
            raise ValueError("scanner results must be supplied as a tuple")
        if info.mode == "python" and isinstance(value, tuple) and not all(
            isinstance(result, InstrumentScanResult) for result in value
        ):
            raise ValueError("scanner snapshot must contain actual InstrumentScanResult objects")
        return value

    @model_validator(mode="after")
    def validate_results(self) -> "ScannerSnapshot":
        """Defensively reconstruct results and enforce universe-level invariants."""
        try:
            results = tuple(
                InstrumentScanResult(
                    setup_snapshot=_revalidate_setup_snapshot(result.setup_snapshot),
                    active_setup_keys=result.active_setup_keys,
                )
                for result in self.results
            )
        except (ValidationError, AttributeError, TypeError, ValueError) as exc:
            raise ValueError("scanner results must be canonical") from exc
        _validate_ordered_snapshots(
            tuple(result.setup_snapshot for result in results),
            self.timeframe,
            self.scan_as_of,
        )
        object.__setattr__(self, "results", results)
        return self
