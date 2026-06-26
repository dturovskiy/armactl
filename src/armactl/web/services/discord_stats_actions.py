"""Controlled Discord statistics settings actions for web routes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from armactl import paths
from armactl.discord_stats import (
    DiscordStatsConfig,
    DiscordStatsConfigError,
    DiscordStatsPublishError,
    disable_discord_stats_service,
    install_discord_stats_service,
    load_discord_stats_config,
    publish_discord_stats,
    restart_discord_stats_service,
    save_discord_stats_config,
    start_discord_stats_service,
    stop_discord_stats_service,
)
from armactl.redaction import redact_sensitive_text
from armactl.service_manager import ServiceResult
from armactl.web.services.audit import AuditLogError, append_audit_event


@dataclass(frozen=True)
class DiscordStatsSettingsResult:
    """Safe result for Discord statistics settings updates."""

    success: bool
    message: str
    enabled: bool
    interval_seconds: int
    webhook_configured: bool
    webhook_changed: bool
    audit_written: bool = True
    intent_audited: bool = True


@dataclass(frozen=True)
class DiscordStatsActionResult:
    """Safe result for Discord statistics service/publish actions."""

    success: bool
    message: str
    action: str
    audit_written: bool = True
    intent_audited: bool = True


def _safe_message(value: object) -> str:
    return redact_sensitive_text(value).strip() or "No details available."


def _result(
    *,
    success: bool,
    message: str,
    enabled: bool,
    interval_seconds: int,
    webhook_configured: bool,
    webhook_changed: bool,
    audit_written: bool = True,
    intent_audited: bool = True,
) -> DiscordStatsSettingsResult:
    return DiscordStatsSettingsResult(
        success=success,
        message=_safe_message(message),
        enabled=enabled,
        interval_seconds=interval_seconds,
        webhook_configured=webhook_configured,
        webhook_changed=webhook_changed,
        audit_written=audit_written,
        intent_audited=intent_audited,
    )


def save_discord_stats_settings_and_audit(
    *,
    enabled: bool,
    webhook_url: str,
    interval_seconds: int,
    audit_log_path: Path,
    username: str,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> DiscordStatsSettingsResult:
    """Save Discord stats settings with secret-safe audit events."""
    current = load_discord_stats_config(instance)
    normalized_webhook = webhook_url.strip()
    webhook_changed = bool(normalized_webhook)
    webhook_configured = bool(normalized_webhook or current.webhook_url.strip())

    try:
        append_audit_event(
            audit_log_path,
            username=username,
            action="discord_stats.configure",
            instance=instance,
            target="discord stats",
            success=True,
            message="Discord statistics settings change requested.",
            exit_code=0,
            details={
                "phase": "intent",
                "enabled": enabled,
                "webhook_changed": webhook_changed,
                "webhook_configured": webhook_configured,
                "interval_seconds": interval_seconds,
            },
        )
    except AuditLogError:
        return _result(
            success=False,
            message="Discord statistics settings were not saved because audit logging failed.",
            enabled=current.enabled,
            interval_seconds=current.interval_seconds,
            webhook_configured=current.webhook_configured(),
            webhook_changed=False,
            audit_written=False,
            intent_audited=False,
        )

    updated = DiscordStatsConfig(
        instance=current.instance,
        enabled=enabled,
        webhook_url=normalized_webhook or current.webhook_url,
        interval_seconds=interval_seconds,
        message_id=current.message_id,
        env_path=current.env_path,
    )

    try:
        save_discord_stats_config(updated)
    except DiscordStatsConfigError as error:
        message = str(error)
        try:
            append_audit_event(
                audit_log_path,
                username=username,
                action="discord_stats.configure",
                instance=instance,
                target="discord stats",
                success=False,
                message=message,
                exit_code=1,
                details={
                    "phase": "outcome",
                    "enabled": enabled,
                    "webhook_changed": webhook_changed,
                    "webhook_configured": webhook_configured,
                    "interval_seconds": interval_seconds,
                },
            )
        except AuditLogError:
            return _result(
                success=False,
                message="Discord statistics settings failed, and audit logging also failed.",
                enabled=current.enabled,
                interval_seconds=current.interval_seconds,
                webhook_configured=current.webhook_configured(),
                webhook_changed=False,
                audit_written=False,
            )
        return _result(
            success=False,
            message=message,
            enabled=current.enabled,
            interval_seconds=current.interval_seconds,
            webhook_configured=current.webhook_configured(),
            webhook_changed=False,
        )

    try:
        append_audit_event(
            audit_log_path,
            username=username,
            action="discord_stats.configure",
            instance=instance,
            target="discord stats",
            success=True,
            message="Discord statistics settings saved.",
            exit_code=0,
            details={
                "phase": "outcome",
                "enabled": updated.enabled,
                "webhook_changed": webhook_changed,
                "webhook_configured": updated.webhook_configured(),
                "interval_seconds": updated.interval_seconds,
            },
        )
    except AuditLogError:
        return _result(
            success=False,
            message="Discord statistics settings were saved, but audit logging failed.",
            enabled=updated.enabled,
            interval_seconds=updated.interval_seconds,
            webhook_configured=updated.webhook_configured(),
            webhook_changed=webhook_changed,
            audit_written=False,
        )

    return _result(
        success=True,
        message="Discord statistics settings saved.",
        enabled=updated.enabled,
        interval_seconds=updated.interval_seconds,
        webhook_configured=updated.webhook_configured(),
        webhook_changed=webhook_changed,
    )

_DISCORD_STATS_ACTION_LABELS = {
    "publish": "Discord statistics message published.",
    "install": "Discord statistics service installed.",
    "start": "Discord statistics service started.",
    "restart": "Discord statistics service restarted.",
    "stop": "Discord statistics service stopped.",
    "disable": "Discord statistics service disabled.",
}


def _service_results_success(results: list[ServiceResult]) -> bool:
    return bool(results) and all(result.success for result in results)


def _first_service_failure(results: list[ServiceResult]) -> str:
    for result in results:
        if not result.success:
            return _safe_message(result.message)
    return "Discord statistics service action failed."


def _run_discord_stats_action(action: str, instance: str) -> tuple[bool, str]:
    if action == "publish":
        try:
            result = publish_discord_stats(instance)
        except DiscordStatsPublishError as error:
            return False, str(error)
        return result.success, result.message

    if action == "install":
        results = install_discord_stats_service(instance)
        if _service_results_success(results):
            return True, _DISCORD_STATS_ACTION_LABELS[action]
        return False, _first_service_failure(results)

    service_actions = {
        "start": start_discord_stats_service,
        "restart": restart_discord_stats_service,
        "stop": stop_discord_stats_service,
        "disable": disable_discord_stats_service,
    }
    service_action = service_actions.get(action)
    if service_action is None:
        return False, "Unsupported Discord statistics action."
    result = service_action()
    if result.success:
        return True, _DISCORD_STATS_ACTION_LABELS[action]
    return False, result.message


def run_discord_stats_action_and_audit(
    *,
    action: str,
    audit_log_path: Path,
    username: str,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> DiscordStatsActionResult:
    """Run a Discord stats publish/service action with secret-safe audit events."""
    normalized_action = action.strip().lower()
    if normalized_action not in _DISCORD_STATS_ACTION_LABELS:
        return DiscordStatsActionResult(
            False,
            _safe_message("Unsupported Discord statistics action."),
            normalized_action or "unknown",
            audit_written=False,
            intent_audited=False,
        )

    try:
        append_audit_event(
            audit_log_path,
            username=username,
            action=f"discord_stats.{normalized_action}",
            instance=instance,
            target="discord stats",
            success=True,
            message="Discord statistics action requested.",
            exit_code=0,
            details={"phase": "intent", "action": normalized_action},
        )
    except AuditLogError:
        return DiscordStatsActionResult(
            False,
            _safe_message("Discord statistics action was not run because audit logging failed."),
            normalized_action,
            audit_written=False,
            intent_audited=False,
        )

    success, message = _run_discord_stats_action(normalized_action, instance)
    safe_message = _safe_message(message)

    try:
        append_audit_event(
            audit_log_path,
            username=username,
            action=f"discord_stats.{normalized_action}",
            instance=instance,
            target="discord stats",
            success=success,
            message=safe_message,
            exit_code=0 if success else 1,
            details={"phase": "outcome", "action": normalized_action},
        )
    except AuditLogError:
        return DiscordStatsActionResult(
            False,
            _safe_message("Discord statistics action completed, but audit logging failed."),
            normalized_action,
            audit_written=False,
        )

    return DiscordStatsActionResult(success, safe_message, normalized_action)
