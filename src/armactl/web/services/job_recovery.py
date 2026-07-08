"""Audited stale web-job metadata recovery actions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from armactl.web.jobs.models import JOB_STATUS_RUNNING, JOB_WORKER_LEASE_FRESH, JobRecord
from armactl.web.jobs.runner import has_active_worker_token
from armactl.web.jobs.store import (
    JobNotFoundError,
    mark_stale_running_job_abandoned,
)
from armactl.web.services.audit import AuditLogError, append_audit_event

JOB_RECOVERY_AUDIT_FAILED_MESSAGE = (
    "Job recovery action could not be completed because audit logging failed."
)
STALE_RUNNING_JOB_RECOVERY_MARKED_MESSAGE = (
    "Stale running job metadata marked abandoned."
)
STALE_RUNNING_JOB_RECOVERY_NOT_ELIGIBLE_MESSAGE = (
    "Job was not eligible for stale metadata recovery."
)


class JobRecoveryAuditError(RuntimeError):
    """Raised when a job recovery action cannot be audited."""


@dataclass(frozen=True)
class StaleRunningJobRecoveryResult:
    """Controlled result for stale running metadata recovery."""

    marked: bool
    message: str
    job: JobRecord | None = None


def is_stale_running_job_recovery_eligible(job: JobRecord) -> bool:
    """Return whether an operator may mark this running row abandoned."""
    if job.status != JOB_STATUS_RUNNING:
        return False
    if job.worker_lease_state == JOB_WORKER_LEASE_FRESH:
        return False
    if job.worker_id and has_active_worker_token(job.id, job.worker_id):
        return False
    return True


def _audit_stale_recovery_intent(
    audit_log_path: Path,
    *,
    username: str,
    job_id: int,
    instance: str,
) -> None:
    append_audit_event(
        audit_log_path,
        username=username,
        action="job.stale-running.mark-abandoned",
        instance=instance,
        target=f"web_job:{job_id}",
        success=True,
        message="Stale running job recovery requested.",
        exit_code=0,
        details={
            "phase": "intent",
            "job_id": str(job_id),
        },
    )


def _audit_stale_recovery_outcome(
    audit_log_path: Path,
    *,
    username: str,
    job_id: int,
    instance: str,
    marked: bool,
    job: JobRecord | None,
) -> None:
    details = {
        "phase": "outcome",
        "job_id": str(job_id),
        "marked": str(marked).lower(),
    }
    target = f"web_job:{job_id}"
    if job is not None:
        target = job.kind
        details.update(
            {
                "job_kind": job.kind,
                "job_status": job.status,
                "instance": job.instance,
            }
        )
    append_audit_event(
        audit_log_path,
        username=username,
        action="job.stale-running.mark-abandoned",
        instance=instance,
        target=target,
        success=marked,
        message=(
            STALE_RUNNING_JOB_RECOVERY_MARKED_MESSAGE
            if marked
            else STALE_RUNNING_JOB_RECOVERY_NOT_ELIGIBLE_MESSAGE
        ),
        exit_code=0 if marked else 1,
        details=details,
    )


def mark_stale_running_job_abandoned_for_operator(
    db_path: Path,
    job_id: int,
    *,
    audit_log_path: Path,
    username: str,
    instance: str = "default",
) -> StaleRunningJobRecoveryResult:
    """Audit and mark stale running metadata abandoned, if still eligible."""
    try:
        _audit_stale_recovery_intent(
            audit_log_path,
            username=username,
            job_id=job_id,
            instance=instance,
        )
    except AuditLogError as exc:
        raise JobRecoveryAuditError(JOB_RECOVERY_AUDIT_FAILED_MESSAGE) from exc

    try:
        job = mark_stale_running_job_abandoned(
            db_path,
            job_id,
            active_worker_token_checker=has_active_worker_token,
        )
    except JobNotFoundError:
        job = None

    marked = job is not None
    message = (
        STALE_RUNNING_JOB_RECOVERY_MARKED_MESSAGE
        if marked
        else STALE_RUNNING_JOB_RECOVERY_NOT_ELIGIBLE_MESSAGE
    )
    try:
        _audit_stale_recovery_outcome(
            audit_log_path,
            username=username,
            job_id=job_id,
            instance=job.instance if job is not None else instance,
            marked=marked,
            job=job,
        )
    except AuditLogError as exc:
        raise JobRecoveryAuditError(JOB_RECOVERY_AUDIT_FAILED_MESSAGE) from exc

    return StaleRunningJobRecoveryResult(marked=marked, message=message, job=job)

