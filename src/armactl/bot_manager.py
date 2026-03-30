"""Helpers for installing and managing the optional Telegram bot service."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from armactl import paths
from armactl.bot_config import ensure_bot_config, load_bot_config, validate_bot_config
from armactl.i18n import _, tr
from armactl.redaction import redact_sensitive_text, safe_subprocess_error
from armactl.service_manager import (
    ServiceResult,
    daemon_reload,
    disable_service,
    enable_service,
    get_service_status,
    has_privileged_systemctl_channel,
    install_privileged_systemctl_channel,
    install_systemd_unit_file,
    restart_service,
    start_service,
    stop_service,
)


def bot_service_name() -> str:
    """Return the fixed systemd unit name for the Telegram bot."""
    return paths.BOT_SERVICE_NAME


def bot_python_path() -> Path:
    """Return the repo-local Python interpreter used by the bot service."""
    project_root = Path(__file__).resolve().parents[2]
    return project_root / ".venv" / "bin" / "python"


def validate_bot_service_config(instance: str) -> list[str]:
    """Return service-install validation errors for the instance bot config."""
    config = load_bot_config(instance)
    errors = validate_bot_config(config)
    if not config.enabled:
        errors.insert(0, _("Telegram bot must be enabled before installing the bot service."))
    return errors


def check_bot_runtime() -> ServiceResult:
    """Verify that the repo-local virtualenv can import python-telegram-bot."""
    python_bin = bot_python_path()
    if not python_bin.exists():
        return ServiceResult(
            False,
            tr(
                "Bot runtime Python not found at {path}. Re-run "
                "./scripts/bootstrap.sh --prod or --dev.",
                path=python_bin,
            ),
            1,
        )

    result = subprocess.run(
        [str(python_bin), "-c", "import telegram, telegram.ext"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return ServiceResult(True, _("Bot runtime is ready."))

    error_text = safe_subprocess_error(result.stderr, result.stdout)
    if "No module named 'telegram'" in error_text:
        return ServiceResult(
            False,
            _(
                "python-telegram-bot is not installed in the repo virtualenv. "
                "Re-run ./scripts/bootstrap.sh --prod or --dev."
            ),
            result.returncode or 1,
        )

    return ServiceResult(
        False,
        tr("Bot runtime check failed: {error}", error=error_text or _("Unknown")),
        result.returncode or 1,
    )


def render_bot_service_unit(instance: str) -> str:
    """Render the systemd unit text for the Telegram bot service."""
    project_root = Path(__file__).resolve().parents[2]
    templates_dir = project_root / "templates"
    env = Environment(loader=FileSystemLoader(str(templates_dir)))
    home_dir = Path.home()
    user = os.getenv("USER", "root")
    try:
        if user == "root" and os.getlogin():
            user = os.getlogin()
    except OSError:
        pass

    service_template = env.get_template("armactl-bot.service.j2")
    return service_template.render(
        instance=instance,
        user=user,
        home_dir=str(home_dir),
        bot_dir=str(paths.bot_dir(instance)),
        project_root=str(project_root),
        python_bin=str(bot_python_path()),
    )


def install_bot_service(instance: str) -> list[ServiceResult]:
    """Generate, install, reload and enable the Telegram bot systemd service."""
    results: list[ServiceResult] = []

    try:
        ensure_bot_config(instance)
        errors = validate_bot_service_config(instance)
        if errors:
            return [ServiceResult(False, errors[0], 1)]

        runtime_result = check_bot_runtime()
        if not runtime_result.success:
            return [runtime_result]

        privileged_results = install_privileged_systemctl_channel()
        privileged_failures = [result for result in privileged_results if not result.success]
        if privileged_failures:
            return privileged_results
        results.extend(privileged_results)

        service_path = paths.bot_service_file()
        service_render = render_bot_service_unit(instance)

        with tempfile.TemporaryDirectory() as tempd:
            temp_dir = Path(tempd)
            temp_service = temp_dir / bot_service_name()
            temp_service.write_text(service_render, encoding="utf-8")

            install_result = install_systemd_unit_file(temp_service, service_path)
            if not install_result.success:
                return [install_result]
            results.append(install_result)

        reload_result = daemon_reload()
        results.append(
            ServiceResult(
                reload_result.success,
                (
                    _("Systemd daemon reloaded")
                    if reload_result.success
                    else tr("Daemon reload failed: {message}", message=reload_result.message)
                ),
                reload_result.exit_code,
            )
        )

        enable_result = enable_service(bot_service_name())
        results.append(enable_result)
    except Exception as e:
        results.append(
            ServiceResult(
                False,
                tr("Bot service install failed: {error}", error=redact_sensitive_text(e)),
                1,
            )
        )

    return results


def get_bot_service_status() -> dict[str, Any]:
    """Return structured status for the Telegram bot service and runtime."""
    status = get_service_status(bot_service_name())
    status.update(
        service_file=str(paths.bot_service_file()),
        installed=paths.bot_service_file().exists(),
        runtime=check_bot_runtime().to_dict(),
        privileged_channel_installed=has_privileged_systemctl_channel(),
    )
    return status


def ensure_bot_service_runtime(instance: str) -> list[ServiceResult]:
    """Best-effort auto-heal for an already installed and enabled Telegram bot."""
    try:
        config = load_bot_config(instance)
    except Exception as e:
        return [
            ServiceResult(
                False,
                tr(
                    "Failed to load Telegram bot settings: {error}",
                    error=redact_sensitive_text(e),
                ),
                1,
            )
        ]

    if not config.enabled or not paths.bot_service_file().exists():
        return []

    runtime_result = check_bot_runtime()
    if not runtime_result.success:
        return [runtime_result]

    status = get_service_status(bot_service_name())
    results: list[ServiceResult] = []

    if not status.get("enabled"):
        enable_result = enable_service(bot_service_name())
        results.append(enable_result)
        if not enable_result.success:
            return results
        status["enabled"] = True

    active_state = str(status.get("active_state", "")).lower()
    is_running = bool(status.get("active")) or active_state in {
        "active",
        "activating",
        "reloading",
    }
    if not is_running:
        results.append(start_bot_service())

    return results


def start_bot_service() -> ServiceResult:
    """Start the Telegram bot service."""
    return start_service(bot_service_name())


def stop_bot_service() -> ServiceResult:
    """Stop the Telegram bot service."""
    return stop_service(bot_service_name())


def restart_bot_service() -> ServiceResult:
    """Restart the Telegram bot service."""
    return restart_service(bot_service_name())


def disable_bot_service() -> ServiceResult:
    """Disable Telegram bot auto-start."""
    return disable_service(bot_service_name())
