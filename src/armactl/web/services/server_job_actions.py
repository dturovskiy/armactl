"""Audited server background-job actions for armactl web."""

from __future__ import annotations

from pathlib import Path

from armactl import paths
from armactl.web.jobs import server as server_jobs
from armactl.web.jobs.models import JobRecord
from armactl.web.jobs.store import cancel_job, list_recent_jobs
from armactl.web.services.audit import AuditLogError, append_audit_event

JOB_AUDIT_FAILED_MESSAGE = "Job queued but audit logging failed."


class ServerJobActionError(ValueError):
    """Raised when a server job action request is invalid."""


class ServerJobAuditError(RuntimeError):
    """Raised when a queued server job could not be audited."""


def _server_job_kind(action: str) -> str:
    if action == "install":
        return server_jobs.SERVER_INSTALL_JOB_KIND
    if action == "repair":
        return server_jobs.SERVER_REPAIR_JOB_KIND
    raise ServerJobActionError("Unknown job action.")


def _active_job_ids(db_path: Path, *, kind: str, instance: str) -> set[int]:
    return {
        job.id
        for job in list_recent_jobs(db_path, limit=100)
        if job.kind == kind and job.instance == instance and not job.is_terminal
    }


def _enqueue_server_job(
    db_path: Path,
    *,
    action: str,
    username: str,
    user_id: int | None,
    instance: str,
) -> tuple[JobRecord, bool]:
    kind = _server_job_kind(action)
    active_before = _active_job_ids(db_path, kind=kind, instance=instance)
    if action == "install":
        job = server_jobs.enqueue_server_install(
            db_path,
            requested_by_username=username,
            requested_by_user_id=user_id,
            instance=instance,
        )
    elif action == "repair":
        job = server_jobs.enqueue_server_repair(
            db_path,
            requested_by_username=username,
            requested_by_user_id=user_id,
            instance=instance,
        )
    else:
        raise ServerJobActionError("Unknown job action.")
    return job, job.id not in active_before


def _audit_enqueued_server_job(
    audit_log_path: Path,
    *,
    action: str,
    job: JobRecord,
    username: str,
    created: bool,
    instance: str,
) -> None:
    append_audit_event(
        audit_log_path,
        username=username,
        action=f"job.server-{action}.enqueue",
        instance=instance,
        target=job.kind,
        success=True,
        message=f"Server {action} job queued.",
        exit_code=0,
        details={
            "job_id": str(job.id),
            "job_kind": job.kind,
            "job_status": job.status,
            "created": str(created).lower(),
        },
    )


def enqueue_server_job_and_start(
    db_path: Path,
    *,
    action: str,
    audit_log_path: Path,
    username: str,
    user_id: int | None,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> JobRecord:
    """Queue a server install/repair job, audit it, then start its worker."""
    job, created = _enqueue_server_job(
        db_path,
        action=action,
        username=username,
        user_id=user_id,
        instance=instance,
    )
    try:
        _audit_enqueued_server_job(
            audit_log_path,
            action=action,
            job=job,
            username=username,
            created=created,
            instance=instance,
        )
    except AuditLogError as exc:
        if created:
            try:
                cancel_job(
                    db_path,
                    job.id,
                    result_message="Cancelled because audit logging failed.",
                )
            except Exception:
                pass
        raise ServerJobAuditError(JOB_AUDIT_FAILED_MESSAGE) from exc

    server_jobs.start_server_job_worker(db_path, job.id)
    return job
