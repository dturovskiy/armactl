"""Audited server background-job actions for armactl web."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from armactl import paths
from armactl.web.jobs import server as server_jobs
from armactl.web.jobs.models import JobRecord
from armactl.web.jobs.store import cancel_job
from armactl.web.services import server_versions
from armactl.web.services.audit import AuditLogError, append_audit_event

JOB_INTENT_AUDIT_FAILED_MESSAGE = "Job was not queued because audit logging failed."
JOB_OUTCOME_AUDIT_FAILED_MESSAGE = (
    "Job action could not be completed because audit logging failed."
)
JOB_CHECK_AUDIT_FAILED_MESSAGE = (
    "Update check result could not be recorded because audit logging failed."
)
UPDATE_JOB_QUEUED_MESSAGE = "Update job queued."
STOP_RUNNING_SERVER_UPDATE_MESSAGE = "Stop the game server before updating."
SERVER_UPDATE_ACTION_QUEUED = "queued"
SERVER_UPDATE_ACTION_UP_TO_DATE = "up_to_date"
SERVER_UPDATE_ACTION_UNAVAILABLE = "unavailable"
SERVER_UPDATE_ACTION_BLOCKED = "blocked"


class ServerJobActionError(ValueError):
    """Raised when a server job action request is invalid."""


class ServerJobAuditError(RuntimeError):
    """Raised when a queued server job could not be audited."""


@dataclass(frozen=True)
class ServerUpdateActionResult:
    """Controlled result for a server update action request."""

    status: str
    message: str
    version_state: server_versions.ServerVersionState
    job: JobRecord | None = None


def _server_job_kind(action: str) -> str:
    if action == "install":
        return server_jobs.SERVER_INSTALL_JOB_KIND
    if action == "repair":
        return server_jobs.SERVER_REPAIR_JOB_KIND
    if action == "update":
        return server_jobs.SERVER_UPDATE_JOB_KIND
    if action == "update-check":
        return server_jobs.SERVER_UPDATE_CHECK_JOB_KIND
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
    if action == "update-check":
        return server_jobs.ensure_server_update_check_job(
            db_path,
            requested_by_username=username,
            requested_by_user_id=user_id,
            instance=instance,
        )
    if action == "update":
        return server_jobs.ensure_server_update_job(
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


def _audit_server_update_check(
    audit_log_path: Path,
    *,
    version_state: server_versions.ServerVersionState,
    username: str,
    instance: str,
    success: bool,
    message: str,
) -> None:
    append_audit_event(
        audit_log_path,
        username=username,
        action="job.server-update.check",
        instance=instance,
        target=server_jobs.SERVER_UPDATE_JOB_KIND,
        success=success,
        message=message,
        exit_code=0 if success else 1,
        details={
            "phase": "check",
            "job_kind": server_jobs.SERVER_UPDATE_JOB_KIND,
            "check_state": version_state.check_state,
            "status": version_state.status,
            "installed": version_state.installed,
            "latest": version_state.latest,
            "branch": version_state.branch,
            "up_to_date": str(version_state.up_to_date).lower(),
            "can_update": str(version_state.can_update).lower(),
            "server_running": str(version_state.server_running).lower(),
            "update_job_id": str(version_state.update_job_id or ""),
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


def _audit_update_check_or_raise(
    audit_log_path: Path,
    *,
    version_state: server_versions.ServerVersionState,
    username: str,
    instance: str,
    success: bool,
    message: str,
) -> None:
    try:
        _audit_server_update_check(
            audit_log_path,
            version_state=version_state,
            username=username,
            instance=instance,
            success=success,
            message=message,
        )
    except AuditLogError as exc:
        raise ServerJobAuditError(JOB_CHECK_AUDIT_FAILED_MESSAGE) from exc


def request_server_update_check_and_start(
    db_path: Path,
    *,
    audit_log_path: Path,
    username: str,
    user_id: int | None,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> JobRecord:
    """Enqueue an explicit background latest-build check."""
    return enqueue_server_job_and_start(
        db_path,
        action="update-check",
        audit_log_path=audit_log_path,
        username=username,
        user_id=user_id,
        instance=instance,
    )


def request_server_update_and_start(
    db_path: Path,
    *,
    audit_log_path: Path,
    username: str,
    user_id: int | None,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    confirm_running: bool = False,
) -> ServerUpdateActionResult:
    """Check update state and enqueue server:update only when safely available."""
    del confirm_running
    version_state = server_versions.load_server_version_state(
        instance=instance,
        db_path=db_path,
    )

    if version_state.check_state == server_versions.SERVER_VERSION_CHECK_UPTODATE:
        _audit_update_check_or_raise(
            audit_log_path,
            version_state=version_state,
            username=username,
            instance=instance,
            success=True,
            message=server_versions.SERVER_VERSION_MESSAGE_UP_TO_DATE,
        )
        return ServerUpdateActionResult(
            status=SERVER_UPDATE_ACTION_UP_TO_DATE,
            message=server_versions.SERVER_VERSION_MESSAGE_UP_TO_DATE,
            version_state=version_state,
        )

    if version_state.check_state == server_versions.SERVER_VERSION_CHECK_AVAILABLE:
        if version_state.server_running:
            _audit_update_check_or_raise(
                audit_log_path,
                version_state=version_state,
                username=username,
                instance=instance,
                success=False,
                message=STOP_RUNNING_SERVER_UPDATE_MESSAGE,
            )
            return ServerUpdateActionResult(
                status=SERVER_UPDATE_ACTION_BLOCKED,
                message=STOP_RUNNING_SERVER_UPDATE_MESSAGE,
                version_state=version_state,
            )

        job = enqueue_server_job_and_start(
            db_path,
            action="update",
            audit_log_path=audit_log_path,
            username=username,
            user_id=user_id,
            instance=instance,
        )
        return ServerUpdateActionResult(
            status=SERVER_UPDATE_ACTION_QUEUED,
            message=UPDATE_JOB_QUEUED_MESSAGE,
            version_state=version_state,
            job=job,
        )

    message = (
        server_versions.SERVER_VERSION_MESSAGE_FAILED
        if version_state.check_state == server_versions.SERVER_VERSION_CHECK_FAILED
        else server_versions.SERVER_VERSION_MESSAGE_UNKNOWN
    )
    _audit_update_check_or_raise(
        audit_log_path,
        version_state=version_state,
        username=username,
        instance=instance,
        success=False,
        message=message,
    )
    return ServerUpdateActionResult(
        status=SERVER_UPDATE_ACTION_UNAVAILABLE,
        message=message,
        version_state=version_state,
    )
