"""Audited current-player refresh background-job actions for web routes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from armactl import paths
from armactl.web.jobs import player_current
from armactl.web.jobs.models import JobRecord
from armactl.web.services.audit import AuditLogError, append_audit_event

PLAYER_CURRENT_REFRESH_INTENT_FAILED_MESSAGE = (
    "Current player refresh was not queued because audit logging failed."
)


class PlayerCurrentRefreshActionAuditError(RuntimeError):
    """Raised when current-player refresh enqueue could not be audited."""


@dataclass(frozen=True)
class PlayerCurrentRefreshEnqueueResult:
    """Controlled result for a current-player refresh enqueue request."""

    job: JobRecord
    created: bool


def _append_refresh_intent_audit(
    audit_log_path: Path,
    *,
    username: str,
    instance: str,
) -> None:
    append_audit_event(
        audit_log_path,
        username=username,
        action=player_current.PLAYER_CURRENT_REFRESH_ACTION,
        instance=instance,
        target=player_current.PLAYER_CURRENT_REFRESH_JOB_KIND,
        success=True,
        message="Current player refresh requested.",
        exit_code=0,
        details={
            "phase": "intent",
            "job_kind": player_current.PLAYER_CURRENT_REFRESH_JOB_KIND,
            "scope": player_current.PLAYER_CURRENT_REFRESH_SCOPE,
            "observed": "0",
            "stored": "0",
            "ignored": "0",
            "source": "pending",
            "status": "pending",
        },
    )


def ensure_player_current_refresh_job(
    db_path: Path,
    *,
    requested_by_username: str,
    requested_by_user_id: int | None = None,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> tuple[JobRecord, bool]:
    """Return an active current-player refresh job, creating one if needed."""
    return player_current.ensure_player_current_refresh_job(
        db_path,
        requested_by_username=requested_by_username,
        requested_by_user_id=requested_by_user_id,
        instance=instance,
    )


def request_player_current_refresh_and_start(
    db_path: Path,
    *,
    audit_log_path: Path,
    username: str,
    user_id: int | None,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> PlayerCurrentRefreshEnqueueResult:
    """Audit and enqueue a current-roster registry refresh job."""
    normalized_instance = instance or paths.DEFAULT_INSTANCE_NAME
    try:
        _append_refresh_intent_audit(
            audit_log_path,
            username=username,
            instance=normalized_instance,
        )
    except AuditLogError as exc:
        raise PlayerCurrentRefreshActionAuditError(
            PLAYER_CURRENT_REFRESH_INTENT_FAILED_MESSAGE
        ) from exc

    job, created = ensure_player_current_refresh_job(
        db_path,
        requested_by_username=username,
        requested_by_user_id=user_id,
        instance=normalized_instance,
    )
    if created:
        player_current.start_player_current_refresh_worker(db_path, job.id)
    return PlayerCurrentRefreshEnqueueResult(job=job, created=created)
