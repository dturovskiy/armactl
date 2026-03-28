"""Tests for Telegram bot service management helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from armactl.bot_config import BotConfig
from armactl.bot_manager import (
    check_bot_runtime,
    render_bot_service_unit,
    validate_bot_service_config,
)
from armactl.i18n import _


def test_validate_bot_service_config_requires_enabled_flag():
    config = BotConfig(
        instance="default",
        enabled=False,
        token="123456:ABCDEF",
        admin_chat_ids=["123456789"],
        language="uk",
    )

    with patch("armactl.bot_manager.load_bot_config", return_value=config):
        errors = validate_bot_service_config("default")

    assert _("Telegram bot must be enabled before installing the bot service.") in errors


def test_check_bot_runtime_reports_missing_python(tmp_path: Path):
    missing_python = tmp_path / ".venv" / "bin" / "python"

    with patch("armactl.bot_manager.bot_python_path", return_value=missing_python):
        result = check_bot_runtime()

    assert result.success is False
    expected = _(
        "Bot runtime Python not found at {path}. Re-run ./scripts/bootstrap.sh --prod or --dev."
    ).format(path=missing_python)
    assert expected == result.message


def test_render_bot_service_unit_contains_instance_and_execstart(tmp_path: Path):
    python_bin = tmp_path / ".venv" / "bin" / "python"

    with patch("armactl.bot_manager.bot_python_path", return_value=python_bin):
        text = render_bot_service_unit("default")

    assert "Description=armactl Telegram Bot (default)" in text
    assert f"ExecStart={python_bin} -m armactl.telegram_bot --instance default" in text
