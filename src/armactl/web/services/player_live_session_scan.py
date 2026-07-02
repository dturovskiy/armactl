"""Audited live player-session scanner background-job actions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from armactl import paths
from armactl.web.jobs import player_sessions
from armactl.web.jobs.models import JobRecord
from armactl.web.services.audit import AuditLogError, append_audit_event

PLAYER_LIVE_SESSION_SCAN_INTENT_FAILED_MESSAGE = (
    "Live player session scan was not queued because audit logging failed."
)


class PlayerLiveSessionScanActionAuditError(RuntimeError):
    """Raised when live session scan enqueue could not be audited."""


@dataclass(frozen=True)
class PlayerLiveSessionScanEnqueueResult:
    """Controlled result for a live session scan enqueue request."""

    job: JobRecord
    created: bool


def _append_live_session_scan_intent_audit(
    audit_log_path: Path,
    *,
    username: str,
    instance: str,
) -> None:
    append_audit_event(
        audit_log_path,
        username=username,
        action=player_sessions.PLAYER_LIVE_SESSION_SCAN_ACTION,
        instance=instance,
        target=player_sessions.PLAYER_LIVE_SESSION_SCAN_JOB_KIND,
        success=True,
        message="Live player session scan requested.",
        exit_code=0,
        details=player_sessions.live_session_scan_count_details(
            None,
            phase="intent",
            job_id=None,
        ),
    )


def ensure_player_live_session_scan_job(
    db_path: Path,
    *,
    requested_by_username: str,
    requested_by_user_id: int | None = None,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> tuple[JobRecord, bool]:
    """Return an active live session scan job, creating one if needed."""
    return player_sessions.ensure_player_live_session_scan_job(
        db_path,
        requested_by_username=requested_by_username,
        requested_by_user_id=requested_by_user_id,
        instance=instance,
    )


def request_player_live_session_scan_and_start(
    db_path: Path,
    *,
    audit_log_path: Path,
    username: str,
    user_id: int | None,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> PlayerLiveSessionScanEnqueueResult:
    """Audit and enqueue one explicit live player-session scan."""
    normalized_instance = instance or paths.DEFAULT_INSTANCE_NAME
    try:
        _append_live_session_scan_intent_audit(
            audit_log_path,
            username=username,
            instance=normalized_instance,
        )
    except AuditLogError as exc:
        raise PlayerLiveSessionScanActionAuditError(
            PLAYER_LIVE_SESSION_SCAN_INTENT_FAILED_MESSAGE
        ) from exc

    job, created = ensure_player_live_session_scan_job(
        db_path,
        requested_by_username=username,
        requested_by_user_id=user_id,
        instance=normalized_instance,
    )
    if created:
        player_sessions.start_player_live_session_scan_worker(db_path, job.id)
    return PlayerLiveSessionScanEnqueueResult(job=job, created=created)
