"""Strictly read-only provider interface for external trading-account facts."""

from typing import Protocol

from app.accounts.models import AccountFactBatch, ExternalSentimentContext
from app.core.schemas import Instrument


class ReadOnlyTradingAccountProvider(Protocol):
    """Retrieve facts only; execution methods intentionally do not exist."""

    async def sync_account(self, provider_account_id: str) -> AccountFactBatch: ...

    async def sentiment_context(
        self, instruments: tuple[Instrument, ...]
    ) -> tuple[ExternalSentimentContext, ...]: ...

    async def aclose(self) -> None: ...
