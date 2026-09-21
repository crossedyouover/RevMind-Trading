"""Strictly read-only provider interface for external trading-account facts."""

from datetime import date
from typing import Protocol

from app.accounts.models import (
    AccountFactBatch,
    ExternalPerformanceObservation,
    ExternalSentimentContext,
    TradingAccountSnapshot,
)
from app.core.schemas import Instrument


class ReadOnlyTradingAccountProvider(Protocol):
    """Retrieve facts only; execution methods intentionally do not exist."""

    async def list_accounts(self) -> tuple[TradingAccountSnapshot, ...]: ...

    async def sync_account(self, provider_account_id: str) -> AccountFactBatch: ...

    async def daily_performance(
        self, provider_account_id: str, start: date, end: date
    ) -> tuple[ExternalPerformanceObservation, ...]: ...

    async def sentiment_context(
        self, instruments: tuple[Instrument, ...]
    ) -> tuple[ExternalSentimentContext, ...]: ...

    async def disconnect(self) -> None: ...

    async def aclose(self) -> None: ...
