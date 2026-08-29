"""Tests for secret-safe application settings."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings

CONFIG_NAMES = (
    "TRADING_MODE",
    "DATABASE_URL",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "XAI_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
)


@pytest.fixture(autouse=True)
def clear_config_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep settings tests independent of developer environment variables."""
    for name in CONFIG_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_settings_load_without_credentials() -> None:
    settings = Settings()

    assert settings.openai_api_key is None
    assert settings.anthropic_api_key is None
    assert settings.xai_api_key is None
    assert settings.telegram_bot_token is None


def test_trading_mode_defaults_to_paper() -> None:
    assert Settings().trading_mode == "paper"


def test_shadow_trading_mode_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADING_MODE", "shadow")

    assert Settings().trading_mode == "shadow"


@pytest.mark.parametrize("invalid_mode", ["live", "LIVE", "production", "real"])
def test_unsafe_trading_modes_are_rejected(
    monkeypatch: pytest.MonkeyPatch, invalid_mode: str
) -> None:
    monkeypatch.setenv("TRADING_MODE", invalid_mode)

    with pytest.raises(ValidationError):
        Settings()


def test_secrets_are_not_exposed_in_settings_repr_or_str(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_secrets = {
        "OPENAI_API_KEY": "sk-THIS-MUST-NEVER-APPEAR",
        "ANTHROPIC_API_KEY": "anthropic-THIS-MUST-NEVER-APPEAR",
        "XAI_API_KEY": "xai-THIS-MUST-NEVER-APPEAR",
        "TELEGRAM_BOT_TOKEN": "telegram-THIS-MUST-NEVER-APPEAR",
    }
    for name, value in fake_secrets.items():
        monkeypatch.setenv(name, value)

    settings = Settings()
    rendered_values = (repr(settings), str(settings))

    for rendered in rendered_values:
        assert all(secret not in rendered for secret in fake_secrets.values())
        assert "**********" in rendered
