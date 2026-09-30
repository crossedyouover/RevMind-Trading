"""Provider boundary for canonical global-event receipts."""

from datetime import datetime
from typing import Protocol

from app.intelligence.events.models import ObservedGlobalEvent


class GlobalEventProviderError(Exception):
    """Raised when an external global-event provider cannot return trusted facts."""


class GlobalEventProvider(Protocol):
    async def get_events(self, *, observed_at: datetime) -> tuple[ObservedGlobalEvent, ...]: ...
