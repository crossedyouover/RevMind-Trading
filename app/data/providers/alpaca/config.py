"""Secret-safe provider-local configuration for Alpaca market data."""

from enum import StrEnum

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AlpacaDataFeed(StrEnum):
    """Explicit supported Alpaca stock data feeds."""

    IEX = "iex"
    SIP = "sip"


class AlpacaMarketDataSettings(BaseSettings):
    """Optional Alpaca credentials that never make the provider mandatory."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_prefix="ALPACA_",
        case_sensitive=False, extra="ignore", frozen=True,
        hide_input_in_errors=True,
    )

    api_key_id: SecretStr | None = None
    api_secret_key: SecretStr | None = None
    data_feed: AlpacaDataFeed = AlpacaDataFeed.IEX

    @field_validator("api_key_id", "api_secret_key", mode="before")
    @classmethod
    def normalize_blank_credentials(cls, value: object) -> object:
        """Treat blank environment placeholders as absent without trimming secrets."""
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        if isinstance(raw, str):
            if not raw.strip():
                return None
            if raw != raw.strip():
                raise ValueError("Alpaca credentials must not contain surrounding whitespace")
        return value

    @model_validator(mode="after")
    def validate_credentials(self) -> "AlpacaMarketDataSettings":
        if (self.api_key_id is None) != (self.api_secret_key is None):
            raise ValueError("Alpaca credentials must be both present or both absent")
        return self

    @property
    def available(self) -> bool:
        return self.api_key_id is not None and self.api_secret_key is not None
