"""Audited player-session maintenance background-job actions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from armactl import paths
from armactl.web.jobs import player_sessions
from armactl.web.jobs.models import JobRecord
from armactl.web.services.audit import AuditLogError, append_audit_event

PLAYER_SESSION_MAINTENANCE_INTENT_FAILED_MESSAGE = (
    "Player session maintenance was not queued because audit logging failed."
)


class PlayerSessionMaintenanceActionAuditError(RuntimeError):
    """Raised when player-session maintenance enqueue could not be audited."""


@dataclass(frozen=True)
class PlayerSessionMaintenanceEnqueueResult:
    """Controlled result for a player-session maintenance enqueue request."""

    job: JobRecord
    created: bool


def _append_maintenance_intent_audit(
    audit_log_path: Path,
    *,
    username: str,
    instance: str,
) -> None:
    append_audit_event(
        audit_log_path,
        username=username,
        action=player_sessions.PLAYER_SESSION_MAINTENANCE_ACTION,
        instance=instance,
        target=player_sessions.PLAYER_SESSION_MAINTENANCE_JOB_KIND,
        success=True,
        message="Player session maintenance requested.",
        exit_code=0,
        details={
            "phase": "intent",
            "job_kind": player_sessions.PLAYER_SESSION_MAINTENANCE_JOB_KIND,
            "scope": player_sessions.PLAYER_SESSION_MAINTENANCE_SCOPE,
            "open_sessions_scanned": "0",
            "stale_sessions_overdue": "0",
            "stale_sessions_closed": "0",
            "stale_sessions_not_closed": "0",
            "retention_sessions_scanned": "0",
            "retention_sessions_deleted": "0",
            "retention_sessions_not_deleted": "0",
        },
    )


def ensure_player_session_maintenance_job(
    db_path: Path,
    *,
    requested_by_username: str,
    requested_by_user_id: int | None = None,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> tuple[JobRecord, bool]:
    """Return an active player-session maintenance job, creating one if needed."""
    return player_sessions.ensure_player_session_maintenance_job(
        db_path,
        requested_by_username=requested_by_username,
        requested_by_user_id=requested_by_user_id,
        instance=instance,
    )


def request_player_session_maintenance_and_start(
    db_path: Path,
    *,
    audit_log_path: Path,
    username: str,
    user_id: int | None,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> PlayerSessionMaintenanceEnqueueResult:
    """Audit and enqueue explicit player-session stale-close/cleanup."""
    normalized_instance = instance or paths.DEFAULT_INSTANCE_NAME
    try:
        _append_maintenance_intent_audit(
            audit_log_path,
            username=username,
            instance=normalized_instance,
        )
    except AuditLogError as exc:
        raise PlayerSessionMaintenanceActionAuditError(
            PLAYER_SESSION_MAINTENANCE_INTENT_FAILED_MESSAGE
        ) from exc

    job, created = ensure_player_session_maintenance_job(
        db_path,
        requested_by_username=username,
        requested_by_user_id=user_id,
        instance=normalized_instance,
    )
    if created:
        player_sessions.start_player_session_maintenance_worker(db_path, job.id)
    return PlayerSessionMaintenanceEnqueueResult(job=job, created=created)
