"""Tests for Telegram bot service management helpers."""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from armactl.bot_config import BotConfig
from armactl.bot_manager import (
    check_bot_runtime,
    ensure_bot_service_runtime,
    get_bot_service_status,
    render_bot_service_unit,
    validate_bot_service_config,
)
from armactl.i18n import _
from armactl.service_manager import ServiceResult


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

    with (
        patch("armactl.bot_manager.bot_python_path", return_value=python_bin),
        patch("armactl.bot_manager.resolve_linux_user", return_value="defenders88"),
    ):
        text = render_bot_service_unit("default")

    assert "Description=armactl Telegram Bot (default)" in text
    assert "User=defenders88" in text
    assert f"ExecStart={python_bin} -m armactl.telegram_bot --instance default" in text
    assert "Restart=always" in text
    assert "StartLimitIntervalSec=0" in text
    assert "Environment=HOME=" in text


def test_ensure_bot_service_runtime_enables_and_starts_installed_bot(
    tmp_path: Path,
) -> None:
    config = BotConfig(
        instance="default",
        enabled=True,
        token="123456:ABCDEF",
        admin_chat_ids=["123456789"],
        language="uk",
    )
    service_path = tmp_path / "armactl-bot.service"
    service_path.write_text("[Unit]\n", encoding="utf-8")

    with (
        patch("armactl.bot_manager.load_bot_config", return_value=config),
        patch("armactl.bot_manager.paths.bot_service_file", return_value=service_path),
        patch(
            "armactl.bot_manager.check_bot_runtime",
            return_value=ServiceResult(True, _("Bot runtime is ready.")),
        ),
        patch(
            "armactl.bot_manager.get_service_status",
            return_value={"active": False, "enabled": False, "active_state": "inactive"},
        ),
        patch(
            "armactl.bot_manager.enable_service",
            return_value=ServiceResult(
                True,
                _("Systemctl action: enable armactl-bot.service: ok"),
            ),
        ) as enable_mock,
        patch(
            "armactl.bot_manager.start_bot_service",
            return_value=ServiceResult(
                True,
                _("Systemctl action: start armactl-bot.service: ok"),
            ),
        ) as start_mock,
    ):
        results = ensure_bot_service_runtime("default")

    assert [result.success for result in results] == [True, True]
    enable_mock.assert_called_once()
    start_mock.assert_called_once()


def test_ensure_bot_service_runtime_ignores_disabled_bot(tmp_path: Path) -> None:
    config = BotConfig(
        instance="default",
        enabled=False,
        token="",
        admin_chat_ids=[],
        language="uk",
    )
    service_path = tmp_path / "armactl-bot.service"
    service_path.write_text("[Unit]\n", encoding="utf-8")

    with (
        patch("armactl.bot_manager.load_bot_config", return_value=config),
        patch("armactl.bot_manager.paths.bot_service_file", return_value=service_path),
        patch("armactl.bot_manager.start_bot_service") as start_mock,
    ):
        results = ensure_bot_service_runtime("default")

    assert results == []
    start_mock.assert_not_called()


def test_get_bot_service_status_reports_privileged_channel(tmp_path: Path):
    service_path = tmp_path / "armactl-bot.service"
    service_path.write_text("[Unit]\n", encoding="utf-8")

    with (
        patch(
            "armactl.bot_manager.get_service_status",
            return_value={"service_name": "armactl-bot.service", "enabled": True},
        ),
        patch("armactl.bot_manager.paths.bot_service_file", return_value=service_path),
        patch(
            "armactl.bot_manager.check_bot_runtime",
            return_value=ServiceResult(True, _("Bot runtime is ready.")),
        ),
        patch("armactl.bot_manager.has_privileged_systemctl_channel", return_value=True),
    ):
        status = get_bot_service_status()

    assert status["installed"] is True
    assert status["privileged_channel_installed"] is True


def test_check_bot_runtime_redacts_secrets_in_errors(tmp_path: Path) -> None:
    python_bin = tmp_path / ".venv" / "bin" / "python"
    python_bin.parent.mkdir(parents=True, exist_ok=True)
    python_bin.write_text("", encoding="utf-8")
    result = CompletedProcess(
        args=[str(python_bin), "-c", "import telegram"],
        returncode=1,
        stdout="",
        stderr="ARMACTL_BOT_TOKEN=123456789:ABCDEF_secret_token",
    )

    with (
        patch("armactl.bot_manager.bot_python_path", return_value=python_bin),
        patch("armactl.bot_manager.subprocess.run", return_value=result),
    ):
        runtime = check_bot_runtime()

    assert runtime.success is False
    assert "123456789:ABCDEF_secret_token" not in runtime.message
    assert "ARMACTL_BOT_TOKEN=***" in runtime.message
