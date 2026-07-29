"""Audited stored-log sessionization background-job actions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from armactl import paths
from armactl.web.jobs import player_sessions
from armactl.web.jobs.models import JobRecord
from armactl.web.services.audit import AuditLogError, append_audit_event

PLAYER_LOG_SESSIONIZATION_INTENT_FAILED_MESSAGE = (
    "Player log sessionization was not queued because audit logging failed."
)


class PlayerLogSessionizationActionAuditError(RuntimeError):
    """Raised when stored-log sessionization enqueue could not be audited."""


@dataclass(frozen=True)
class PlayerLogSessionizationEnqueueResult:
    """Controlled result for a stored-log sessionization enqueue request."""

    job: JobRecord
    created: bool


def _append_sessionization_intent_audit(
    audit_log_path: Path,
    *,
    username: str,
    instance: str,
) -> None:
    append_audit_event(
        audit_log_path,
        username=username,
        action=player_sessions.PLAYER_LOG_SESSIONIZATION_ACTION,
        instance=instance,
        target=player_sessions.PLAYER_LOG_SESSIONIZATION_JOB_KIND,
        success=True,
        message="Player log sessionization requested.",
        exit_code=0,
        details={
            "events_scanned": "0",
            "events_ignored": "0",
            "observations_considered": "0",
            "observations_applied": "0",
            "observations_skipped": "0",
            "observations_ignored": "0",
            "sessions_created": "0",
            "sessions_updated": "0",
            "sessions_closed": "0",
        },
    )


def ensure_player_log_sessionization_job(
    db_path: Path,
    *,
    requested_by_username: str,
    requested_by_user_id: int | None = None,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> tuple[JobRecord, bool]:
    """Return an active stored-log sessionization job, creating one if needed."""
    return player_sessions.ensure_player_log_sessionization_job(
        db_path,
        requested_by_username=requested_by_username,
        requested_by_user_id=requested_by_user_id,
        instance=instance,
    )


def request_player_log_sessionization_and_start(
    db_path: Path,
    *,
    audit_log_path: Path,
    username: str,
    user_id: int | None,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> PlayerLogSessionizationEnqueueResult:
    """Audit and enqueue stored player-log sessionization."""
    normalized_instance = instance or paths.DEFAULT_INSTANCE_NAME
    try:
        _append_sessionization_intent_audit(
            audit_log_path,
            username=username,
            instance=normalized_instance,
        )
    except AuditLogError as exc:
        raise PlayerLogSessionizationActionAuditError(
            PLAYER_LOG_SESSIONIZATION_INTENT_FAILED_MESSAGE
        ) from exc

    job, created = ensure_player_log_sessionization_job(
        db_path,
        requested_by_username=username,
        requested_by_user_id=user_id,
        instance=normalized_instance,
    )
    if created:
        player_sessions.start_player_log_sessionization_worker(db_path, job.id)
    return PlayerLogSessionizationEnqueueResult(job=job, created=created)
