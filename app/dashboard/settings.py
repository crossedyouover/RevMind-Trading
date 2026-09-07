"""Dashboard-owned local configuration; no provider calls or execution authority."""

import os
import re
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self
from uuid import uuid4

from pydantic import ConfigDict, Field, SecretStr, field_validator, model_validator

from app.core.schemas import AssetClass, CanonicalModel, Instrument, Timeframe
from app.data.providers.alpaca.config import AlpacaDataFeed, AlpacaMarketDataSettings


class DataMode(StrEnum):
    OFFLINE = "OFFLINE"
    ALPACA = "ALPACA"


class AlpacaFeed(StrEnum):
    IEX = "IEX"
    SIP = "SIP"


class SessionRule(StrEnum):
    REGULAR = "REGULAR"
    EXTENDED = "EXTENDED"


class WatchInstrument(CanonicalModel):
    symbol: Annotated[str, Field(strict=True, pattern=r"^[A-Z][A-Z0-9.\-]{0,14}$")]
    exchange: Annotated[str, Field(strict=True, pattern=r"^[A-Z]{4}$")]
    asset_class: Literal["EQUITY", "ETF"]
    currency: Literal["USD"]

    def to_instrument(self) -> Instrument:
        return Instrument(
            symbol=self.symbol,
            exchange=self.exchange,
            asset_class=AssetClass(self.asset_class),
            currency=self.currency,
        )


class DashboardSettings(CanonicalModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, validate_assignment=True, revalidate_instances="always"
    )
    schema_version: Literal[1] = 1
    data_mode: DataMode
    alpaca_feed: AlpacaFeed
    watchlist: tuple[WatchInstrument, ...]
    timeframe: Timeframe
    session_rule: SessionRule

    @field_validator("schema_version", mode="before")
    @classmethod
    def strict_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("schema version must be integer 1")
        return value

    @model_validator(mode="after")
    def canonical_watchlist(self) -> Self:
        if not self.watchlist or len(self.watchlist) > 25:
            raise ValueError("watchlist must contain 1 to 25 exact instruments")
        keys = tuple((v.asset_class, v.exchange, v.symbol, v.currency) for v in self.watchlist)
        if len(set(keys)) != len(keys):
            raise ValueError("watchlist instruments must be unique")
        return self


DEFAULT_SETTINGS = DashboardSettings(
    data_mode=DataMode.OFFLINE,
    alpaca_feed=AlpacaFeed.IEX,
    watchlist=(
        WatchInstrument(symbol="AAPL", exchange="XNAS", asset_class="EQUITY", currency="USD"),
        WatchInstrument(symbol="MSFT", exchange="XNAS", asset_class="EQUITY", currency="USD"),
        WatchInstrument(symbol="SPY", exchange="ARCX", asset_class="ETF", currency="USD"),
    ),
    timeframe=Timeframe.ONE_MINUTE,
    session_rule=SessionRule.REGULAR,
)


class SettingsStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.settings_path = directory / "settings.json"
        self.secret_path = directory / "alpaca.env"

    def load(self) -> DashboardSettings:
        if not self.settings_path.exists():
            return DEFAULT_SETTINGS
        payload = self.settings_path.read_bytes()
        if len(payload) > 32_768:
            raise ValueError("settings file is too large")
        return DashboardSettings.model_validate_json(payload)

    def credentials_configured(self) -> bool:
        if not self.secret_path.exists():
            return False
        values = self._read_secrets()
        return bool(values.get("ALPACA_API_KEY_ID") and values.get("ALPACA_API_SECRET_KEY"))

    def alpaca_provider_settings(self) -> AlpacaMarketDataSettings:
        settings = self.load()
        values = self._read_secrets() if self.secret_path.exists() else {}
        return AlpacaMarketDataSettings.model_validate(
            {
                "api_key_id": values.get("ALPACA_API_KEY_ID"),
                "api_secret_key": values.get("ALPACA_API_SECRET_KEY"),
                "data_feed": AlpacaDataFeed(settings.alpaca_feed.value.lower()),
            }
        )

    def alpaca_credentials(self) -> tuple[SecretStr, SecretStr]:
        values = self._read_secrets() if self.secret_path.exists() else {}
        key = values.get("ALPACA_API_KEY_ID")
        secret = values.get("ALPACA_API_SECRET_KEY")
        if not key or not secret:
            raise ValueError("Alpaca credentials are not configured")
        return SecretStr(key), SecretStr(secret)

    def public(self) -> dict[str, object]:
        settings = self.load()
        value = settings.model_dump(mode="json")
        value["credentials_configured"] = self.credentials_configured()
        value["integration_status"] = (
            "OFFLINE_DEMO"
            if settings.data_mode is DataMode.OFFLINE
            else "CONFIGURED_NOT_ACTIVE"
            if self.credentials_configured()
            else "CREDENTIALS_REQUIRED"
        )
        return value

    def save(
        self,
        settings: DashboardSettings,
        key_id: str | None,
        secret_key: str | None,
        clear_credentials: bool,
    ) -> dict[str, object]:
        settings = DashboardSettings.model_validate(settings)
        if type(clear_credentials) is not bool:
            raise ValueError("clear_credentials must be a strict boolean")
        if (key_id is None) != (secret_key is None):
            raise ValueError("both Alpaca credential fields are required together")
        if key_id is not None:
            for value in (key_id, secret_key):
                if (
                    not isinstance(value, str)
                    or not value
                    or value != value.strip()
                    or len(value) > 512
                    or "\r" in value
                    or "\n" in value
                ):
                    raise ValueError("invalid credential value")
        if clear_credentials and key_id is not None:
            raise ValueError("cannot set and clear credentials together")
        self.directory.mkdir(parents=True, exist_ok=True)
        self._replace(self.settings_path, settings.model_dump_json(indent=2).encode())
        if clear_credentials:
            if self.secret_path.exists():
                self.secret_path.unlink()
        elif key_id is not None and secret_key is not None:
            body = f"ALPACA_API_KEY_ID={key_id}\nALPACA_API_SECRET_KEY={secret_key}\n".encode()
            self._replace(self.secret_path, body)
        return self.public()

    @staticmethod
    def _replace(path: Path, payload: bytes) -> None:
        temporary = path.with_name(f".{path.name}.{uuid4()}.tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _read_secrets(self) -> dict[str, str]:
        payload = self.secret_path.read_bytes()
        if len(payload) > 2_048:
            raise ValueError("credential file is too large")
        values: dict[str, str] = {}
        for line in payload.decode("utf-8").splitlines():
            if not re.fullmatch(r"ALPACA_API_(?:KEY_ID|SECRET_KEY)=[^\r\n]{1,512}", line):
                raise ValueError("credential file is malformed")
            key, value = line.split("=", 1)
            if key in values:
                raise ValueError("duplicate credential field")
            values[key] = value
        return values
