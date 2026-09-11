"""Provider-neutral read-only boundary for timestamped catalyst observations."""

from datetime import datetime
from typing import Protocol

from app.catalysts.models import ObservedCatalystFact
from app.core.schemas import Instrument


class CatalystProviderError(Exception):
    """A catalyst source could not provide a trustworthy bounded result."""


class CatalystProvider(Protocol):
    async def get_news(
        self,
        instruments: tuple[Instrument, ...],
        *,
        published_start: datetime,
        published_end: datetime,
        observed_at: datetime,
    ) -> tuple[ObservedCatalystFact, ...]: ...

    async def aclose(self) -> None: ...
