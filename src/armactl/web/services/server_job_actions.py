"""Audited server background-job actions for armactl web."""

from __future__ import annotations

from pathlib import Path

from armactl import paths
from armactl.web.jobs import server as server_jobs
from armactl.web.jobs.models import JobRecord
from armactl.web.jobs.store import cancel_job
from armactl.web.services.audit import AuditLogError, append_audit_event

JOB_INTENT_AUDIT_FAILED_MESSAGE = "Job was not queued because audit logging failed."
JOB_OUTCOME_AUDIT_FAILED_MESSAGE = "Job action could not be completed because audit logging failed."


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


def _enqueue_server_job(
    db_path: Path,
    *,
    action: str,
    username: str,
    user_id: int | None,
    instance: str,
) -> tuple[JobRecord, bool]:
    if action == "install":
        return server_jobs.ensure_server_install_job(
            db_path,
            requested_by_username=username,
            requested_by_user_id=user_id,
            instance=instance,
        )
    if action == "repair":
        return server_jobs.ensure_server_repair_job(
            db_path,
            requested_by_username=username,
            requested_by_user_id=user_id,
            instance=instance,
        )
    raise ServerJobActionError("Unknown job action.")


def _audit_server_job_intent(
    audit_log_path: Path,
    *,
    action: str,
    username: str,
    instance: str,
) -> None:
    job_kind = _server_job_kind(action)
    append_audit_event(
        audit_log_path,
        username=username,
        action=f"job.server-{action}.enqueue",
        instance=instance,
        target=job_kind,
        success=True,
        message=f"Server {action} job requested.",
        exit_code=0,
        details={
            "phase": "intent",
            "job_kind": job_kind,
        },
    )


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
            "phase": "outcome",
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
    _server_job_kind(action)
    try:
        _audit_server_job_intent(
            audit_log_path,
            action=action,
            username=username,
            instance=instance,
        )
    except AuditLogError as exc:
        raise ServerJobAuditError(JOB_INTENT_AUDIT_FAILED_MESSAGE) from exc

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
            except Exception:  # noqa: BLE001 - best-effort cleanup only.
                pass
        raise ServerJobAuditError(JOB_OUTCOME_AUDIT_FAILED_MESSAGE) from exc

    if created:
        server_jobs.start_server_job_worker(db_path, job.id)
    return job
