"""Pluggable web background job runner foundation."""

from __future__ import annotations

import secrets
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from armactl.redaction import redact_sensitive_text
from armactl.web.jobs.models import (
    JOB_STATUS_QUEUED,
    JOB_STATUS_SUCCEEDED,
    JOB_STATUS_WARNING,
    JobRecord,
)
from armactl.web.jobs.store import (
    DEFAULT_WORKER_LEASE_SECONDS,
    JobTransitionError,
    append_job_output,
    create_job,
    get_job,
    mark_job_failed,
    mark_job_running,
    mark_job_succeeded,
    mark_job_warning,
    refresh_job_heartbeat,
)

DEFAULT_WORKER_HEARTBEAT_INTERVAL_SECONDS = 30.0
_ACTIVE_WORKER_LOCK = threading.Lock()
_ACTIVE_WORKER_TOKENS: dict[int, str] = {}


def _new_worker_id() -> str:
    return secrets.token_urlsafe(24)


def _register_active_worker(job_id: int, worker_id: str) -> None:
    with _ACTIVE_WORKER_LOCK:
        _ACTIVE_WORKER_TOKENS[job_id] = worker_id


def _unregister_active_worker(job_id: int, worker_id: str) -> None:
    with _ACTIVE_WORKER_LOCK:
        if _ACTIVE_WORKER_TOKENS.get(job_id) == worker_id:
            _ACTIVE_WORKER_TOKENS.pop(job_id, None)


def has_active_worker_token(job_id: int, worker_id: str) -> bool:
    """Return whether this process still owns a matching active worker token."""
    with _ACTIVE_WORKER_LOCK:
        return _ACTIVE_WORKER_TOKENS.get(job_id) == worker_id


def _heartbeat_until_stopped(
    db_path: Path,
    job_id: int,
    worker_id: str,
    stop_event: threading.Event,
) -> None:
    while not stop_event.wait(DEFAULT_WORKER_HEARTBEAT_INTERVAL_SECONDS):
        try:
            refresh_job_heartbeat(db_path, job_id, worker_id=worker_id)
        except Exception:
            return


class JobRunnerError(RuntimeError):
    """Raised when a web job cannot be dispatched safely."""


@dataclass(frozen=True)
class JobHandlerResult:
    """Safe result returned by a registered job handler."""

    result_message: str = "Job completed."
    current_step: str = "Done"
    progress_current: int | None = None
    progress_total: int | None = None
    status: str = JOB_STATUS_SUCCEEDED


@dataclass(frozen=True)
class JobDispatchResult:
    """Result of attempting to dispatch one queued job."""

    job: JobRecord
    ran: bool
    message: str


@dataclass(frozen=True)
class JobContext:
    """Context passed to explicit registered web job handlers."""

    db_path: Path = field(repr=False)
    job: JobRecord
    worker_id: str = field(repr=False)

    def append_output(self, *, stdout: str = "", stderr: str = "") -> JobRecord:
        """Append bounded redacted output through the job store."""
        return append_job_output(
            self.db_path,
            self.job.id,
            stdout=stdout,
            stderr=stderr,
            worker_id=self.worker_id,
        )


JobHandler = Callable[[JobContext], JobHandlerResult | str | None]


class JobDispatcher:
    """Small explicit registry for safe web job handlers."""

    def __init__(self, handlers: Mapping[str, JobHandler] | None = None) -> None:
        self._handlers: dict[str, JobHandler] = {}
        for kind, handler in (handlers or {}).items():
            self.register(kind, handler)

    def register(self, kind: str, handler: JobHandler) -> None:
        """Register one safe handler for an explicit job kind."""
        if not callable(handler):
            raise JobRunnerError("Job handler must be callable.")
        normalized = _normalize_handler_kind(kind)
        self._handlers[normalized] = handler

    def handler_for(self, kind: str) -> JobHandler | None:
        """Return a registered handler for a job kind, if any."""
        return self._handlers.get(_normalize_handler_kind(kind))

    @property
    def registered_kinds(self) -> tuple[str, ...]:
        """Return registered job kinds for diagnostics/tests."""
        return tuple(sorted(self._handlers))


def _normalize_handler_kind(kind: str) -> str:
    if not isinstance(kind, str):
        raise JobRunnerError("Job kind is invalid.")
    normalized = kind.strip().lower()
    if not normalized or len(normalized) > 80:
        raise JobRunnerError("Job kind is invalid.")
    if normalized[0] < "a" or normalized[0] > "z":
        raise JobRunnerError("Job kind is invalid.")
    for char in normalized:
        if not (char.isalnum() or char in {":", "_", "-"}):
            raise JobRunnerError("Job kind is invalid.")
    return normalized


def _result_from_handler(value: JobHandlerResult | str | None) -> JobHandlerResult:
    if isinstance(value, JobHandlerResult):
        result = value
    elif isinstance(value, str):
        result = JobHandlerResult(result_message=value)
    else:
        result = JobHandlerResult()
    if result.status not in {JOB_STATUS_SUCCEEDED, JOB_STATUS_WARNING}:
        raise JobRunnerError("Job handler returned an invalid terminal status.")
    return result


def create_default_dispatcher() -> JobDispatcher:
    """Return the default safe dispatcher with no dangerous handlers registered."""
    return JobDispatcher()


def enqueue_job(
    db_path: Path,
    *,
    kind: str,
    requested_by_username: str,
    requested_by_user_id: int | None = None,
    instance: str = "default",
    current_step: str = "Queued",
    progress_total: int = 0,
) -> JobRecord:
    """Create queued job metadata for a future worker."""
    return create_job(
        db_path,
        kind=kind,
        requested_by_username=requested_by_username,
        requested_by_user_id=requested_by_user_id,
        instance=instance,
        current_step=current_step,
        progress_total=progress_total,
    )


def dispatch_job(
    db_path: Path,
    job_id: int,
    dispatcher: JobDispatcher,
) -> JobDispatchResult:
    """Run one queued job through an explicit registered handler."""
    job = get_job(db_path, job_id)
    if job is None:
        raise JobRunnerError("Web job was not found.")
    if job.is_terminal:
        return JobDispatchResult(job=job, ran=False, message="Job is already finished.")
    if job.status != JOB_STATUS_QUEUED:
        return JobDispatchResult(job=job, ran=False, message="Job is not queued.")

    worker_id = _new_worker_id()
    try:
        running = mark_job_running(
            db_path,
            job.id,
            current_step="Running",
            worker_id=worker_id,
            worker_lease_seconds=DEFAULT_WORKER_LEASE_SECONDS,
        )
    except JobTransitionError as exc:
        refreshed = get_job(db_path, job.id) or job
        message = redact_sensitive_text(exc) or "Job is not queued."
        return JobDispatchResult(job=refreshed, ran=False, message=message)

    _register_active_worker(running.id, worker_id)
    stop_event = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_heartbeat_until_stopped,
        args=(Path(db_path), running.id, worker_id, stop_event),
        name=f"armactl-web-job-heartbeat-{running.id}",
        daemon=True,
    )
    heartbeat_thread.start()
    try:
        handler = dispatcher.handler_for(running.kind)
        if handler is None:
            failed = mark_job_failed(
                db_path,
                running.id,
                error_message="No registered handler for job kind.",
                error_class="UnknownJobKind",
                result_message="Job failed.",
            )
            return JobDispatchResult(
                job=failed,
                ran=True,
                message="No registered handler.",
            )

        try:
            handler_result = _result_from_handler(
                handler(
                    JobContext(
                        db_path=db_path,
                        job=running,
                        worker_id=worker_id,
                    )
                )
            )
        except Exception as exc:
            failed = mark_job_failed(
                db_path,
                running.id,
                error_message=redact_sensitive_text(exc) or "Job handler failed.",
                error_class=exc.__class__.__name__,
                result_message="Job failed.",
            )
            return JobDispatchResult(job=failed, ran=True, message="Job failed.")

        finish = (
            mark_job_warning
            if handler_result.status == JOB_STATUS_WARNING
            else mark_job_succeeded
        )
        finished = finish(
            db_path,
            running.id,
            result_message=handler_result.result_message,
            current_step=handler_result.current_step,
            progress_current=handler_result.progress_current,
            progress_total=handler_result.progress_total,
        )
        message = (
            "Job completed with warning."
            if handler_result.status == JOB_STATUS_WARNING
            else "Job succeeded."
        )
        return JobDispatchResult(job=finished, ran=True, message=message)
    finally:
        stop_event.set()
        heartbeat_thread.join(timeout=1.0)
        _unregister_active_worker(running.id, worker_id)
