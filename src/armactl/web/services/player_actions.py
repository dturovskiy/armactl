"""Audited player registry actions for web routes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from armactl import paths
from armactl.redaction import redact_sensitive_text
from armactl.web.services import (
    player_current_cache,
    player_registry,
    player_sources,
)
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
    observed_count: int = 0
    stored_count: int = 0
    ignored_count: int = 0
    success: bool = True
    message: str = "Player registry refreshed."
    audit_error: str = ""
    intent_audited: bool = True
    backend_success: bool = True
    audit_written: bool = True
    source: str = ""
    status: str = ""
    reliable_count: int = 0
    reason_class: str = ""
    dry_run: bool = False

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


def _safe_status(status: object, *, available: bool | None = None) -> str:
    safe_status = safe_player_text(status)
    if safe_status:
        return safe_status
    if available is True:
        return "available"
    if available is False:
        return "unavailable"
    return "unknown"


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
    status="pending",
    observed_count=0,
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
        "status": _safe_status(status),
        "observed_count": str(observed_count),
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
        observed_count=0,
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


def _ignored_observation_count(
    observations: tuple[player_registry.PlayerObservation, ...],
) -> int:
    return sum(
        1
        for observation in observations
        if not normalize_reliable_player_id(observation.reliable_id)
    )


def refresh_current_players(
    instance=paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root=paths.DEFAULT_DATA_ROOT,
    dry_run: bool = False,
    observed_at: str | None = None,
):
    """Refresh the known-player registry from the current roster.

    This safe service-layer entry point is callable from web, job, and CLI glue.
    It stores only reliable identity rows in players.db and never stores IPs,
    raw log lines, or raw filesystem paths.
    """
    normalized_instance = instance or paths.DEFAULT_INSTANCE_NAME
    db_path = player_registry.player_registry_db_path(
        normalized_instance,
        data_root=data_root,
    )
    try:
        roster = player_sources.load_current_player_roster(normalized_instance)
    except Exception as error:  # noqa: BLE001 - callers need controlled service results.
        return PlayerRefreshResult(
            observed_count=0,
            stored_count=0,
            ignored_count=0,
            success=False,
            message="Player registry refresh failed while loading current players.",
            backend_success=False,
            source=_SOURCE_UNAVAILABLE,
            status="unavailable",
            reliable_count=0,
            reason_class=type(error).__name__,
            dry_run=dry_run,
        )

    player_current_cache.store_current_roster_snapshot_from_roster(
        roster,
        instance=normalized_instance,
        data_root=data_root,
    )

    safe_source = _safe_source(roster.source)
    safe_status = _safe_status(roster.status, available=roster.available)
    observations = _registry_observations(roster.players)
    observed_count = len(roster.players)
    reliable_count = _reliable_observation_count(observations)
    ignored_count = _ignored_observation_count(observations)

    if not roster.available:
        return PlayerRefreshResult(
            observed_count=observed_count,
            stored_count=0,
            ignored_count=ignored_count,
            success=False,
            message="Current player roster unavailable.",
            backend_success=False,
            source=safe_source,
            status=safe_status,
            reliable_count=reliable_count,
            reason_class="PlayerRosterUnavailable",
            dry_run=dry_run,
        )

    if dry_run:
        return PlayerRefreshResult(
            observed_count=observed_count,
            stored_count=0,
            ignored_count=ignored_count,
            message="Player registry refresh dry run completed.",
            source=safe_source,
            status=safe_status,
            reliable_count=reliable_count,
            dry_run=True,
        )

    if reliable_count == 0:
        message = (
            "No current players online."
            if observed_count == 0
            else "No reliable current players found."
        )
        return PlayerRefreshResult(
            observed_count=observed_count,
            stored_count=0,
            ignored_count=ignored_count,
            message=message,
            source=safe_source,
            status=safe_status,
            reliable_count=0,
        )

    try:
        snapshot = player_registry.record_current_players_snapshot(
            db_path,
            observations,
            observed_at=observed_at,
        )
    except Exception as error:  # noqa: BLE001 - callers need controlled service results.
        return PlayerRefreshResult(
            observed_count=observed_count,
            stored_count=0,
            ignored_count=ignored_count,
            success=False,
            message="Player registry refresh failed while saving current players.",
            backend_success=False,
            source=safe_source,
            status=safe_status,
            reliable_count=reliable_count,
            reason_class=type(error).__name__,
            dry_run=dry_run,
        )

    try:
        cached_snapshot = (
            player_current_cache.store_persistent_current_roster_snapshot_from_roster(
                roster,
                instance=normalized_instance,
                data_root=data_root,
            )
        )
        player_current_cache.store_current_roster_snapshot(
            cached_snapshot,
            data_root=data_root,
        )
    except player_current_cache.CurrentRosterPersistentCacheError:
        pass

    return PlayerRefreshResult(
        observed_count=observed_count,
        stored_count=snapshot.stored_count,
        ignored_count=snapshot.ignored_count,
        source=safe_source,
        status=safe_status,
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

    result = refresh_current_players(
        normalized_instance,
        data_root=data_root,
    )
    try:
        _append_refresh_audit(
            audit_log_path,
            username=username,
            instance=normalized_instance,
            target=target,
            success=result.success,
            message=result.message,
            phase="outcome",
            source=result.source,
            status=result.status,
            observed_count=result.observed_count,
            reliable_count=result.reliable_count,
            recorded_count=result.stored_count,
            ignored_count=result.ignored_count,
            reason_class=result.reason_class,
            reason_message="" if result.success else result.message,
        )
    except AuditLogError as error:
        message = (
            "Player registry refreshed, but audit logging failed."
            if result.backend_success
            else "Player registry refresh failed, and audit logging also failed."
        )
        return PlayerRefreshResult(
            observed_count=result.observed_count,
            stored_count=result.stored_count,
            ignored_count=result.ignored_count,
            success=False,
            message=message,
            audit_error=_safe_error(error),
            backend_success=result.backend_success,
            audit_written=False,
            source=result.source,
            status=result.status,
            reliable_count=result.reliable_count,
            reason_class=result.reason_class,
        )
    return result
