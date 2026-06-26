"""Telegram bot page DTO loader."""

from __future__ import annotations

from typing import Any

from armactl import bot_config, discord_stats, paths
from armactl.redaction import redact_sensitive_text
from armactl.web.page_models.common import (
    BOT_CONFIG_DISPLAY,
    DashboardError,
    _label_display,
    _unavailable,
)


def load_bot_summary(instance: str, errors: list[DashboardError]) -> dict[str, Any]:
    try:
        config = bot_config.load_bot_config(instance)
    except Exception as error:
        message = str(error) or error.__class__.__name__
        errors.append(DashboardError(section="bot", message=message))
        return _unavailable("Telegram bot status is not available")

    summary: dict[str, Any] = {
        "available": True,
        "enabled": bool(config.enabled),
        "token_configured": bool(config.token.strip()),
        "admin_chat_count": len(config.admin_chat_ids),
        "language": config.language,
        "env_path": _label_display(config.env_path, BOT_CONFIG_DISPLAY),
        "env_path_display": _label_display(config.env_path, BOT_CONFIG_DISPLAY),
        "service": _unavailable("Telegram bot service is not installed"),
    }

    try:
        service_file = paths.bot_service_file()
    except Exception as error:
        message = str(error) or error.__class__.__name__
        errors.append(DashboardError(section="bot_service", message=message))
        return summary

    if not service_file.exists():
        return summary

    try:
        from armactl import bot_manager

        service_status = bot_manager.get_bot_service_status()
    except Exception as error:
        message = str(error) or error.__class__.__name__
        errors.append(DashboardError(section="bot_service", message=message))
        summary["service"] = {
            **_unavailable("Telegram bot service status is not available"),
            "error": message,
        }
        return summary

    runtime = service_status.get("runtime")
    if not isinstance(runtime, dict):
        runtime = {}
    summary["service"] = {
        "available": True,
        "service_name": service_status.get("service_name", ""),
        "service_file": service_status.get("service_file", ""),
        "installed": bool(service_status.get("installed")),
        "active": bool(service_status.get("active")),
        "enabled": bool(service_status.get("enabled")),
        "active_state": service_status.get("active_state", "unknown"),
        "main_pid": service_status.get("main_pid", 0),
        "runtime_ready": bool(runtime.get("success")) if runtime else None,
    }
    return summary


def load_discord_stats_summary(instance: str, errors: list[DashboardError]) -> dict[str, Any]:
    """Return safe read-only Discord statistics publisher status."""
    try:
        config = discord_stats.load_discord_stats_config(instance)
    except Exception as error:
        message = str(error) or error.__class__.__name__
        errors.append(DashboardError(section="discord_stats", message=message))
        return _unavailable("Discord statistics status is not available")

    summary: dict[str, Any] = {
        "available": True,
        "enabled": bool(config.enabled),
        "webhook_configured": config.webhook_configured(),
        "webhook_display": config.masked_webhook_url(),
        "interval_seconds": config.interval_seconds,
        "message_id_configured": bool(config.message_id.strip()),
        "env_path": _label_display(config.env_path, "discord stats config"),
        "env_path_display": _label_display(config.env_path, "discord stats config"),
        "service": _unavailable("Discord statistics service is not installed"),
    }

    try:
        service_file = paths.discord_stats_service_file()
    except Exception as error:
        message = str(error) or error.__class__.__name__
        errors.append(DashboardError(section="discord_stats_service", message=message))
        return summary

    if not service_file.exists():
        return summary

    try:
        service_status = discord_stats.get_discord_stats_service_status()
    except Exception as error:
        message = str(error) or error.__class__.__name__
        errors.append(DashboardError(section="discord_stats_service", message=message))
        summary["service"] = {
            **_unavailable("Discord statistics service status is not available"),
            "error": message,
        }
        return summary

    runtime = service_status.get("runtime")
    if not isinstance(runtime, dict):
        runtime = {}
    summary["service"] = {
        "available": True,
        "service_name": service_status.get("service_name", ""),
        "service_file": service_status.get("service_file", ""),
        "installed": bool(service_status.get("installed")),
        "active": bool(service_status.get("active")),
        "enabled": bool(service_status.get("enabled")),
        "active_state": service_status.get("active_state", "unknown"),
        "main_pid": service_status.get("main_pid", 0),
        "runtime_ready": bool(runtime.get("success")) if runtime else None,
    }
    return summary

def load_bot_page(instance: str) -> dict[str, Any]:
    """Return safe read-only Telegram bot status without exposing token values."""
    errors: list[DashboardError] = []
    summary = load_bot_summary(instance, errors)
    discord_summary = load_discord_stats_summary(instance, errors)
    return {
        "instance": instance,
        "available": bool(summary.get("available")),
        "error": summary.get("error", ""),
        "bot": summary,
        "discord_stats": discord_summary,
        "errors": [
            {"section": error.section, "message": redact_sensitive_text(error.message)}
            for error in errors
        ],
    }
