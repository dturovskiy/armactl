"""Audited player registry actions for web routes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from armactl import paths
from armactl.redaction import redact_sensitive_text
from armactl.web.services import player_registry, player_sources
from armactl.web.services.audit import AuditLogError, append_audit_event
from armactl.web.services.player_identity import (
    normalize_reliable_player_id,
    safe_player_text,
)

ACTION_REFRESH = "players.refresh"
_SOURCE_PENDING = "pending"
_SOURCE_UNAVAILABLE = "unavailable"


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
    source: str = ""
    reliable_count: int = 0

    @property
    def status_label(self) -> str:
        return "success" if self.success else "failure"


def _safe_text(value: object, *, max_length: int = 500) -> str:
    text = redact_sensitive_text(value).replace("\r", " ").replace("\n", " ").strip()
    if len(text) > max_length:
        return f"{text[:max_length]}..."
    return text


def _safe_error(error: object) -> str:
    return _safe_text(error) or "Audit logging failed."


def _safe_source(source: object) -> str:
    return safe_player_text(source) or _SOURCE_UNAVAILABLE


def _append_refresh_audit(
    audit_log_path,
    *,
    username,
    instance,
    target,
    success,
    message,
    phase,
    source=_SOURCE_PENDING,
    reliable_count=0,
    recorded_count=0,
    ignored_count=0,
    reason_class="",
    reason_message="",
):
    details = {
        "phase": phase,
        "action": ACTION_REFRESH,
        "instance": safe_player_text(instance),
        "source": _safe_source(source),
        "reliable_count": str(reliable_count),
        "recorded_count": str(recorded_count),
        "ignored_count": str(ignored_count),
    }
    if reason_class:
        details["reason_class"] = safe_player_text(reason_class)
    if reason_message:
        details["reason_message"] = _safe_text(reason_message)
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


def _reliable_observation_count(
    observations: tuple[player_registry.PlayerObservation, ...],
) -> int:
    return sum(
        1
        for observation in observations
        if normalize_reliable_player_id(observation.reliable_id)
    )


def _failure_result(
    *,
    audit_log_path,
    username,
    instance,
    target,
    message,
    source,
    reliable_count,
    error,
):
    safe_source = _safe_source(source)
    try:
        _append_refresh_audit(
            audit_log_path,
            username=username,
            instance=instance,
            target=target,
            success=False,
            message=message,
            phase="outcome",
            source=safe_source,
            reliable_count=reliable_count,
            recorded_count=0,
            ignored_count=0,
            reason_class=type(error).__name__,
            reason_message=message,
        )
    except AuditLogError as audit_error:
        return PlayerRefreshResult(
            stored_count=0,
            ignored_count=0,
            success=False,
            message="Player registry refresh failed, and audit logging also failed.",
            audit_error=_safe_error(audit_error),
            backend_success=False,
            audit_written=False,
            source=safe_source,
            reliable_count=reliable_count,
        )
    return PlayerRefreshResult(
        stored_count=0,
        ignored_count=0,
        success=False,
        message=message,
        backend_success=False,
        audit_written=True,
        source=safe_source,
        reliable_count=reliable_count,
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

    try:
        roster = player_sources.load_current_player_roster(normalized_instance)
    except Exception as error:  # noqa: BLE001 - route must render a controlled failure.
        return _failure_result(
            audit_log_path=audit_log_path,
            username=username,
            instance=normalized_instance,
            target=target,
            message="Player registry refresh failed while loading current players.",
            source=_SOURCE_UNAVAILABLE,
            reliable_count=0,
            error=error,
        )

    observations: tuple[player_registry.PlayerObservation, ...] = ()
    reliable_count = 0
    try:
        observations = _registry_observations(roster.players)
        reliable_count = _reliable_observation_count(observations)
        snapshot = player_registry.record_current_players_snapshot(db_path, observations)
    except Exception as error:  # noqa: BLE001 - route must render a controlled failure.
        return _failure_result(
            audit_log_path=audit_log_path,
            username=username,
            instance=normalized_instance,
            target=target,
            message="Player registry refresh failed while saving current players.",
            source=roster.source,
            reliable_count=reliable_count,
            error=error,
        )

    result = PlayerRefreshResult(
        stored_count=snapshot.stored_count,
        ignored_count=snapshot.ignored_count,
        source=_safe_source(roster.source),
        reliable_count=reliable_count,
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
            source=result.source,
            reliable_count=result.reliable_count,
            recorded_count=result.stored_count,
            ignored_count=result.ignored_count,
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
            source=result.source,
            reliable_count=result.reliable_count,
        )
    return result
