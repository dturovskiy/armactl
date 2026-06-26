"""Controlled Discord statistics settings actions for web routes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from armactl import paths
from armactl.discord_stats import (
    DiscordStatsConfig,
    DiscordStatsConfigError,
    load_discord_stats_config,
    save_discord_stats_config,
)
from armactl.redaction import redact_sensitive_text
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
