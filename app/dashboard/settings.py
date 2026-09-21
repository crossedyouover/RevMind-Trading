"""Dashboard-owned local configuration; no provider calls or execution authority."""

import os
import re
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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


class ValidationDepth(StrEnum):
    STANDARD = "STANDARD"
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
    validation_depth: ValidationDepth = ValidationDepth.EXTENDED

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


class MyfxbookConnectionProfile(CanonicalModel):
    """Non-secret local selection required to construct a read-only adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")
    schema_version: Literal[1] = 1
    provider_account_id: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    broker_timezone: Annotated[str, Field(strict=True, min_length=1, max_length=128)]

    @field_validator("schema_version", mode="before")
    @classmethod
    def strict_profile_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("schema version must be integer 1")
        return value

    @field_validator("provider_account_id")
    @classmethod
    def valid_account_id(cls, value: str) -> str:
        if value != value.strip() or any(ord(character) < 32 for character in value):
            raise ValueError("invalid Myfxbook account id")
        return value

    @field_validator("broker_timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        if value != value.strip() or any(ord(character) < 32 for character in value):
            raise ValueError("invalid IANA broker timezone")
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
            raise ValueError("invalid IANA broker timezone") from exc
        return value


class MyfxbookSettingsRequest(CanonicalModel):
    """Strict browser request; secrets are accepted only for local persistence."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")
    schema_version: Literal[1] = 1
    provider_account_id: Annotated[str | None, Field(strict=True)] = None
    broker_timezone: Annotated[str | None, Field(strict=True)] = None
    email: Annotated[str | None, Field(strict=True)] = None
    password: Annotated[str | None, Field(strict=True)] = None
    clear_connection: Annotated[bool, Field(strict=True)] = False

    @field_validator("schema_version", mode="before")
    @classmethod
    def strict_request_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("schema version must be integer 1")
        return value

    @model_validator(mode="after")
    def coherent_operation(self) -> Self:
        supplied_profile = self.provider_account_id is not None or self.broker_timezone is not None
        supplied_secrets = self.email is not None or self.password is not None
        if self.clear_connection:
            if supplied_profile or supplied_secrets:
                raise ValueError("clear request cannot contain profile or credentials")
        elif self.provider_account_id is None or self.broker_timezone is None:
            raise ValueError("profile fields are required")
        if (self.email is None) != (self.password is None):
            raise ValueError("credential fields are required together")
        return self

    def profile(self) -> MyfxbookConnectionProfile | None:
        if self.clear_connection:
            return None
        if self.provider_account_id is None or self.broker_timezone is None:
            raise ValueError("profile fields are required")
        return MyfxbookConnectionProfile(
            provider_account_id=self.provider_account_id,
            broker_timezone=self.broker_timezone,
        )

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
    validation_depth=ValidationDepth.EXTENDED,
)


class SettingsStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.settings_path = directory / "settings.json"
        self.secret_path = directory / "alpaca.env"
        self.myfxbook_profile_path = directory / "myfxbook.json"
        self.myfxbook_secret_path = directory / "myfxbook.env"

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

    def load_myfxbook_profile(self) -> MyfxbookConnectionProfile | None:
        if not self.myfxbook_profile_path.exists():
            return None
        payload = self.myfxbook_profile_path.read_bytes()
        if len(payload) > 4_096:
            raise ValueError("Myfxbook profile file is too large")
        return MyfxbookConnectionProfile.model_validate_json(payload)

    def myfxbook_credentials(self) -> tuple[SecretStr, SecretStr]:
        values = self._read_myfxbook_secrets() if self.myfxbook_secret_path.exists() else {}
        email = values.get("MYFXBOOK_EMAIL")
        password = values.get("MYFXBOOK_PASSWORD")
        if not email or not password:
            raise ValueError("Myfxbook credentials are not configured")
        return SecretStr(email), SecretStr(password)

    def myfxbook_public(self) -> dict[str, object]:
        profile = self.load_myfxbook_profile()
        credentials_configured = False
        if self.myfxbook_secret_path.exists():
            values = self._read_myfxbook_secrets()
            credentials_configured = bool(
                values.get("MYFXBOOK_EMAIL") and values.get("MYFXBOOK_PASSWORD")
            )
        profile_configured = profile is not None
        return {
            "profile_configured": profile_configured,
            "credentials_configured": credentials_configured,
            "provider_account_id": profile.provider_account_id if profile is not None else None,
            "broker_timezone": profile.broker_timezone if profile is not None else None,
            "integration_status": (
                "CONFIGURED_NOT_ACTIVE"
                if profile_configured and credentials_configured
                else "INCOMPLETE_CONFIGURATION"
                if profile_configured or credentials_configured
                else "NOT_CONFIGURED"
            ),
        }

    def save_myfxbook(
        self,
        profile: MyfxbookConnectionProfile | None,
        email: str | None,
        password: str | None,
        clear_connection: bool,
    ) -> dict[str, object]:
        if type(clear_connection) is not bool:
            raise ValueError("clear_connection must be a strict boolean")
        if (email is None) != (password is None):
            raise ValueError("both Myfxbook credential fields are required together")
        validated_profile = (
            MyfxbookConnectionProfile.model_validate(profile) if profile is not None else None
        )
        if email is not None:
            for value in (email, password):
                if (
                    not isinstance(value, str)
                    or not value
                    or value != value.strip()
                    or len(value) > 512
                    or any(ord(character) < 32 for character in value)
                ):
                    raise ValueError("invalid Myfxbook credential value")
        if clear_connection and (validated_profile is not None or email is not None):
            raise ValueError("cannot set and clear Myfxbook connection together")
        self.directory.mkdir(parents=True, exist_ok=True)
        if clear_connection:
            self.myfxbook_profile_path.unlink(missing_ok=True)
            self.myfxbook_secret_path.unlink(missing_ok=True)
        else:
            if validated_profile is not None:
                self._replace(
                    self.myfxbook_profile_path,
                    validated_profile.model_dump_json(indent=2).encode(),
                )
            if email is not None and password is not None:
                body = f"MYFXBOOK_EMAIL={email}\nMYFXBOOK_PASSWORD={password}\n".encode()
                self._replace(self.myfxbook_secret_path, body)
        return self.myfxbook_public()

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

    def _read_myfxbook_secrets(self) -> dict[str, str]:
        payload = self.myfxbook_secret_path.read_bytes()
        if len(payload) > 2_048:
            raise ValueError("Myfxbook credential file is too large")
        try:
            lines = payload.decode("utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise ValueError("Myfxbook credential file is malformed") from exc
        values: dict[str, str] = {}
        for line in lines:
            if not re.fullmatch(r"MYFXBOOK_(?:EMAIL|PASSWORD)=[^\r\n]{1,512}", line):
                raise ValueError("Myfxbook credential file is malformed")
            key, value = line.split("=", 1)
            if key in values or value != value.strip() or any(ord(char) < 32 for char in value):
                raise ValueError("Myfxbook credential file is malformed")
            values[key] = value
        return values
