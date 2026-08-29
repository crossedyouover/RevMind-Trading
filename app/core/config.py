"""Application configuration loaded from environment variables.

Settings are immutable after construction. Credentials use ``SecretStr`` so their values are
redacted from normal string and repr output. No credentials are required during this phase.
"""

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated, secret-safe application configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    trading_mode: Literal["paper", "shadow"] = Field(
        default="paper", validation_alias="TRADING_MODE"
    )
    database_url: str | None = Field(default=None, validation_alias="DATABASE_URL")
    openai_api_key: SecretStr | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    anthropic_api_key: SecretStr | None = Field(
        default=None, validation_alias="ANTHROPIC_API_KEY"
    )
    xai_api_key: SecretStr | None = Field(default=None, validation_alias="XAI_API_KEY")
    telegram_bot_token: SecretStr | None = Field(
        default=None, validation_alias="TELEGRAM_BOT_TOKEN"
    )
    telegram_chat_id: str | None = Field(default=None, validation_alias="TELEGRAM_CHAT_ID")
