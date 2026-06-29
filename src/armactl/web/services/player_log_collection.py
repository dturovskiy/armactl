"""Audited player-log collection background-job actions for web routes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from armactl import paths
from armactl.web.jobs import player_logs
from armactl.web.jobs.models import JobRecord
from armactl.web.services.audit import AuditLogError, append_audit_event

PLAYER_LOG_COLLECTION_INTENT_FAILED_MESSAGE = (
    "Player log collection was not queued because audit logging failed."
)


class PlayerLogCollectionActionAuditError(RuntimeError):
    """Raised when player-log collection enqueue could not be audited."""


@dataclass(frozen=True)
class PlayerLogCollectionEnqueueResult:
    """Controlled result for a player-log collection enqueue request."""

    job: JobRecord
    created: bool


def _append_collection_intent_audit(
    audit_log_path: Path,
    *,
    username: str,
    instance: str,
) -> None:
    append_audit_event(
        audit_log_path,
        username=username,
        action=player_logs.PLAYER_LOG_COLLECTION_ACTION,
        instance=instance,
        target=player_logs.PLAYER_LOG_COLLECTION_JOB_KIND,
        success=True,
        message="Player log collection requested.",
        exit_code=0,
        details={
            "phase": "intent",
            "job_kind": player_logs.PLAYER_LOG_COLLECTION_JOB_KIND,
            "log_scope": player_logs.PLAYER_LOG_COLLECTION_SCOPE,
            "max_file_bytes": str(player_logs.DEFAULT_MAX_FILE_BYTES),
            "max_file_lines": str(player_logs.DEFAULT_MAX_FILE_LINES),
            "max_files": str(player_logs.DEFAULT_MAX_LOG_FILES),
        },
    )


def ensure_player_log_collection_job(
    db_path: Path,
    *,
    requested_by_username: str,
    requested_by_user_id: int | None = None,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> tuple[JobRecord, bool]:
    """Return an active player-log collection job, creating one if needed."""
    return player_logs.ensure_player_log_collection_job(
        db_path,
        requested_by_username=requested_by_username,
        requested_by_user_id=requested_by_user_id,
        instance=instance,
    )


def request_player_log_collection_and_start(
    db_path: Path,
    *,
    audit_log_path: Path,
    username: str,
    user_id: int | None,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> PlayerLogCollectionEnqueueResult:
    """Audit and enqueue a manual allowlisted player-log collection job."""
    normalized_instance = instance or paths.DEFAULT_INSTANCE_NAME
    try:
        _append_collection_intent_audit(
            audit_log_path,
            username=username,
            instance=normalized_instance,
        )
    except AuditLogError as exc:
        raise PlayerLogCollectionActionAuditError(
            PLAYER_LOG_COLLECTION_INTENT_FAILED_MESSAGE
        ) from exc

    job, created = ensure_player_log_collection_job(
        db_path,
        requested_by_username=username,
        requested_by_user_id=user_id,
        instance=normalized_instance,
    )
    if created:
        player_logs.start_player_log_collection_worker(db_path, job.id)
    return PlayerLogCollectionEnqueueResult(job=job, created=created)
