"""Tests for Telegram bot `.env` configuration helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from armactl.bot_config import (
    BotConfig,
    BotConfigError,
    ensure_bot_config,
    load_bot_config,
    save_bot_config,
    validate_bot_config,
)


def test_load_bot_config_returns_defaults_when_file_is_missing(tmp_path: Path):
    env_path = tmp_path / "bot" / ".env"

    with patch("armactl.bot_config.paths.bot_env_file", return_value=env_path):
        config = load_bot_config("default")

    assert config.instance == "default"
    assert config.enabled is False
    assert config.token == ""
    assert config.admin_chat_ids == []
    assert config.language == "uk"
    assert config.env_path == env_path


def test_save_and_load_bot_config_roundtrip(tmp_path: Path):
    env_path = tmp_path / "bot" / ".env"
    config = BotConfig(
        instance="default",
        enabled=True,
        token="123456:ABCDEF",
        admin_chat_ids=["123456789", "-100987654321"],
        language="uk",
        env_path=env_path,
    )

    save_bot_config(config)

    with patch("armactl.bot_config.paths.bot_env_file", return_value=env_path):
        reloaded = load_bot_config("default")

    assert reloaded.enabled is True
    assert reloaded.token == "123456:ABCDEF"
    assert reloaded.admin_chat_ids == ["123456789", "-100987654321"]
    assert reloaded.language == "uk"
    assert reloaded.env_path == env_path


def test_validate_bot_config_requires_token_and_chat_id_when_enabled():
    config = BotConfig(instance="default", enabled=True, token="", admin_chat_ids=[], language="uk")

    errors = validate_bot_config(config)

    assert "Bot token is required when Telegram bot is enabled." in errors
    assert "At least one admin Chat ID is required when Telegram bot is enabled." in errors


def test_save_bot_config_rejects_invalid_chat_ids(tmp_path: Path):
    env_path = tmp_path / "bot" / ".env"
    config = BotConfig(
        instance="default",
        enabled=False,
        token="",
        admin_chat_ids=["123456", "bad-id"],
        language="uk",
        env_path=env_path,
    )

    with pytest.raises(BotConfigError, match="Admin Chat IDs"):
        save_bot_config(config)


def test_ensure_bot_config_creates_default_file(tmp_path: Path):
    env_path = tmp_path / "bot" / ".env"

    with patch("armactl.bot_config.paths.bot_env_file", return_value=env_path):
        created_path = ensure_bot_config("default")

    assert created_path == env_path
    assert env_path.exists()
    text = env_path.read_text(encoding="utf-8")
    assert "ARMACTL_BOT_ENABLED=false" in text
    assert "ARMACTL_BOT_LANGUAGE=uk" in text
