"""Explicit bounded Myfxbook account-discovery probe for the local dashboard."""

from collections.abc import Callable
from typing import Protocol

from pydantic import SecretStr

from app.accounts.models import AccountFactBatch, TradingAccountSnapshot
from app.accounts.myfxbook import MyfxbookAdapter
from app.dashboard.settings import SettingsStore
from app.data.ingestion import SystemUtcClock


class AccountProbe(Protocol):
    async def list_accounts(self) -> tuple[TradingAccountSnapshot, ...]: ...

    async def disconnect(self) -> None: ...


class AccountFactProbe(Protocol):
    async def sync_account(self, provider_account_id: str) -> AccountFactBatch: ...

    async def disconnect(self) -> None: ...


ProbeFactory = Callable[[SecretStr, SecretStr, str], AccountProbe]
FactProbeFactory = Callable[[SecretStr, SecretStr, str], AccountFactProbe]


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
