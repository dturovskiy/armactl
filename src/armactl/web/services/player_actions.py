"""Audited player registry actions for web routes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from armactl import paths
from armactl.redaction import redact_sensitive_text
from armactl.web.services import player_registry, player_sources
from armactl.web.services.audit import AuditLogError, append_audit_event

ACTION_REFRESH = "players.refresh"


@dataclass(frozen=True)
class PlayerRefreshResult:
    stored_count: int = 0
    ignored_count: int = 0
    success: bool = True
    message: str = "Player registry refreshed."
    audit_error: str = ""
    intent_audited: bool = True
    backend_success: bool = True
    audit_written: bool = True

    @property
    def status_label(self) -> str:
        return "success" if self.success else "failure"


def _safe_error(error: object) -> str:
    return redact_sensitive_text(error) or "Audit logging failed."


def _append_refresh_audit(
    audit_log_path, *, username, instance, target, success, message, phase, result=None
):
    details = {"phase": phase}
    if result is not None:
        details["stored_count"] = str(result.stored_count)
        details["ignored_count"] = str(result.ignored_count)
    append_audit_event(
        Path(audit_log_path),
        username=username,
        action=ACTION_REFRESH,
        instance=instance,
        target=target,
        success=success,
        message=message,
        exit_code=0 if success else 1,
        details=details,
    )


def _intent_failure(error):
    return PlayerRefreshResult(
        stored_count=0,
        ignored_count=0,
        success=False,
        message="Player registry refresh was not run because audit logging failed.",
        audit_error=_safe_error(error),
        intent_audited=False,
        backend_success=False,
        audit_written=False,
    )


def _registry_observations(
    players: tuple[player_sources.CurrentPlayer, ...],
) -> tuple[player_registry.PlayerObservation, ...]:
    return tuple(
        player_registry.PlayerObservation(
            reliable_id=player.admin_reference or player.reliable_id,
            display_name=player.display_name,
            source=player.source,
        )
        for player in players
    )


def refresh_registry_and_audit(
    instance=paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root=paths.DEFAULT_DATA_ROOT,
    audit_log_path,
    username,
):
    normalized_instance = instance or paths.DEFAULT_INSTANCE_NAME
    db_path = player_registry.player_registry_db_path(
        normalized_instance,
        data_root=data_root,
    )
    target = str(db_path)
    try:
        _append_refresh_audit(
            audit_log_path,
            username=username,
            instance=normalized_instance,
            target=target,
            success=True,
            message="Player registry refresh requested.",
            phase="intent",
        )
    except AuditLogError as error:
        return _intent_failure(error)

    roster = player_sources.load_current_player_roster(normalized_instance)
    snapshot = player_registry.record_current_players_snapshot(
        db_path,
        _registry_observations(roster.players),
    )
    result = PlayerRefreshResult(
        stored_count=snapshot.stored_count,
        ignored_count=snapshot.ignored_count,
    )
    try:
        _append_refresh_audit(
            audit_log_path,
            username=username,
            instance=normalized_instance,
            target=target,
            success=True,
            message="Player registry refreshed.",
            phase="outcome",
            result=result,
        )
    except AuditLogError as error:
        return PlayerRefreshResult(
            stored_count=result.stored_count,
            ignored_count=result.ignored_count,
            success=False,
            message="Player registry refreshed, but audit logging failed.",
            audit_error=_safe_error(error),
            backend_success=True,
            audit_written=False,
        )
    return result
