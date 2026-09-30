"""Canonical global-event observation contracts."""

from app.intelligence.events.engine import (
    DeterministicGlobalEventMaterializationEngine,
    GlobalEventMaterializationComputationError,
    GlobalEventMaterializationEngine,
    GlobalEventMaterializationError,
    GlobalEventMaterializationInvalidInputError,
)
from app.intelligence.events.models import (
    GlobalEventCategory,
    GlobalEventMaterializationRequest,
    GlobalEventReceiptBatch,
    MaterializedGlobalEventHistory,
    ObservedGlobalEvent,
)
from app.intelligence.events.provider import GlobalEventProvider, GlobalEventProviderError
from app.intelligence.events.store import (
    GlobalEventConflictError,
    GlobalEventCorruptionError,
    GlobalEventStore,
    GlobalEventStoreError,
    GlobalEventStoreUnavailableError,
    SQLiteGlobalEventStore,
)
from app.intelligence.events.usgs import UsgsEarthquakeProvider

__all__ = (
    "DeterministicGlobalEventMaterializationEngine",
    "GlobalEventCategory",
    "GlobalEventMaterializationComputationError",
    "GlobalEventMaterializationEngine",
    "GlobalEventMaterializationError",
    "GlobalEventMaterializationInvalidInputError",
    "GlobalEventMaterializationRequest",
    "GlobalEventProvider",
    "GlobalEventProviderError",
    "GlobalEventReceiptBatch",
    "GlobalEventConflictError",
    "GlobalEventCorruptionError",
    "GlobalEventStore",
    "GlobalEventStoreError",
    "GlobalEventStoreUnavailableError",
    "MaterializedGlobalEventHistory",
    "ObservedGlobalEvent",
    "SQLiteGlobalEventStore",
    "UsgsEarthquakeProvider",
)
