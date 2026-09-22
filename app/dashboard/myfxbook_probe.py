"""Explicit bounded Myfxbook account-discovery probe for the local dashboard."""

from collections.abc import Callable
from datetime import date
from typing import Literal, Protocol

from pydantic import SecretStr, model_validator

from app.accounts.models import (
    AccountFactBatch,
    ExternalPerformanceObservation,
    TradingAccountSnapshot,
)
from app.accounts.myfxbook import MyfxbookAdapter
from app.core.schemas import CanonicalModel
from app.dashboard.settings import SettingsStore
from app.data.ingestion import SystemUtcClock


class AccountProbe(Protocol):
    async def list_accounts(self) -> tuple[TradingAccountSnapshot, ...]: ...

    async def disconnect(self) -> None: ...


class AccountFactProbe(Protocol):
    async def sync_account(self, provider_account_id: str) -> AccountFactBatch: ...

    async def disconnect(self) -> None: ...


class PerformanceProbe(Protocol):
    async def daily_performance(
        self, provider_account_id: str, start: date, end: date
    ) -> tuple[ExternalPerformanceObservation, ...]: ...

    async def disconnect(self) -> None: ...


class MyfxbookPerformanceRequest(CanonicalModel):
    schema_version: Literal[1] = 1
    start: date
    end: date

    @model_validator(mode="after")
    def bounded_range(self) -> "MyfxbookPerformanceRequest":
        if self.start > self.end:
            raise ValueError("performance start date cannot be after end date")
        if (self.end - self.start).days >= 366:
            raise ValueError("performance range cannot exceed 366 inclusive dates")
        return self


ProbeFactory = Callable[[SecretStr, SecretStr, str], AccountProbe]
FactProbeFactory = Callable[[SecretStr, SecretStr, str], AccountFactProbe]
PerformanceProbeFactory = Callable[[SecretStr, SecretStr, str], PerformanceProbe]


def _default_factory(email: SecretStr, password: SecretStr, timezone: str) -> AccountProbe:
    return MyfxbookAdapter(
        email,
        password,
        SystemUtcClock(),
        broker_timezone=timezone,
    )


def _default_fact_factory(
    email: SecretStr, password: SecretStr, timezone: str
) -> AccountFactProbe:
    return MyfxbookAdapter(
        email,
        password,
        SystemUtcClock(),
        broker_timezone=timezone,
    )


def _default_performance_factory(
    email: SecretStr, password: SecretStr, timezone: str
) -> PerformanceProbe:
    return MyfxbookAdapter(
        email,
        password,
        SystemUtcClock(),
        broker_timezone=timezone,
    )


async def probe_myfxbook_accounts(
    settings: SettingsStore,
    *,
    factory: ProbeFactory = _default_factory,
) -> dict[str, object]:
    """Discover bounded account choices and terminally disconnect before returning."""
    profile = settings.load_myfxbook_profile()
    if profile is None:
        raise ValueError("Myfxbook profile is not configured")
    email, password = settings.myfxbook_credentials()
    adapter = factory(email, password, profile.broker_timezone)
    accounts: tuple[TradingAccountSnapshot, ...] | None = None
    try:
        accounts = await adapter.list_accounts()
    finally:
        await adapter.disconnect()
    return {
        "schema_version": 1,
        "status": "CONNECTED_READ_ONLY",
        "account_count": len(accounts),
        "accounts": [account.model_dump(mode="json") for account in accounts],
        "session": "DISCONNECTED",
        "execution": "NONE",
    }


async def probe_myfxbook_summary(
    settings: SettingsStore,
    *,
    factory: ProbeFactory = _default_factory,
) -> dict[str, object]:
    """Read only the exact saved account summary and terminally disconnect."""
    profile = settings.load_myfxbook_profile()
    if profile is None:
        raise ValueError("Myfxbook profile is not configured")
    email, password = settings.myfxbook_credentials()
    adapter = factory(email, password, profile.broker_timezone)
    account: TradingAccountSnapshot | None = None
    try:
        matches = tuple(
            item
            for item in await adapter.list_accounts()
            if item.provider_account_id == profile.provider_account_id
        )
        if len(matches) != 1:
            raise ValueError("saved Myfxbook account is unavailable")
        account = matches[0]
    finally:
        await adapter.disconnect()
    return {
        "schema_version": 1,
        "status": "CURRENT_READ_ONLY_SNAPSHOT",
        "account": account.model_dump(mode="json"),
        "session": "DISCONNECTED",
        "execution": "NONE",
    }


async def probe_myfxbook_facts(
    settings: SettingsStore,
    *,
    factory: FactProbeFactory = _default_fact_factory,
) -> dict[str, object]:
    """Materialize one exact saved-account fact batch and terminally disconnect."""
    profile = settings.load_myfxbook_profile()
    if profile is None:
        raise ValueError("Myfxbook profile is not configured")
    email, password = settings.myfxbook_credentials()
    adapter = factory(email, password, profile.broker_timezone)
    batch: AccountFactBatch | None = None
    try:
        batch = await adapter.sync_account(profile.provider_account_id)
        if batch.account.provider_account_id != profile.provider_account_id:
            raise ValueError("Myfxbook returned facts for an unexpected account")
    finally:
        await adapter.disconnect()
    return {
        "schema_version": 1,
        "status": "CURRENT_READ_ONLY_FACTS",
        "facts": batch.model_dump(mode="json"),
        "session": "DISCONNECTED",
        "execution": "NONE",
    }


async def probe_myfxbook_performance(
    settings: SettingsStore,
    request: MyfxbookPerformanceRequest,
    *,
    factory: PerformanceProbeFactory = _default_performance_factory,
) -> dict[str, object]:
    """Read one explicit daily-performance range and terminally disconnect."""
    profile = settings.load_myfxbook_profile()
    if profile is None:
        raise ValueError("Myfxbook profile is not configured")
    email, password = settings.myfxbook_credentials()
    adapter = factory(email, password, profile.broker_timezone)
    observations: tuple[ExternalPerformanceObservation, ...] | None = None
    try:
        observations = await adapter.daily_performance(
            profile.provider_account_id, request.start, request.end
        )
        if any(item.provider_account_id != profile.provider_account_id for item in observations):
            raise ValueError("Myfxbook returned performance for an unexpected account")
    finally:
        await adapter.disconnect()
    return {
        "schema_version": 1,
        "status": "CURRENT_READ_ONLY_PERFORMANCE",
        "provider_account_id": profile.provider_account_id,
        "start": request.start.isoformat(),
        "end": request.end.isoformat(),
        "observation_count": len(observations),
        "observations": [item.model_dump(mode="json") for item in observations],
        "session": "DISCONNECTED",
        "execution": "NONE",
    }
