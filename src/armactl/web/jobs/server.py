"""Explicit server install/repair job handlers for armactl web."""

from __future__ import annotations

import threading
from pathlib import Path

from armactl import discovery, installer, paths, repair
from armactl.web.jobs.models import JobRecord
from armactl.web.jobs.runner import (
    JobContext,
    JobDispatcher,
    JobHandlerResult,
    dispatch_job,
)
from armactl.web.jobs.store import get_or_create_active_job

SERVER_INSTALL_JOB_KIND = "server:install"
SERVER_REPAIR_JOB_KIND = "server:repair"
SERVER_JOB_KINDS = frozenset({SERVER_INSTALL_JOB_KIND, SERVER_REPAIR_JOB_KIND})


def ensure_server_install_job(
    db_path,
    *,
    requested_by_username: str,
    requested_by_user_id: int | None = None,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> tuple[JobRecord, bool]:
    """Return an active install job, creating a queued job if needed."""
    return get_or_create_active_job(
        db_path,
        kind=SERVER_INSTALL_JOB_KIND,
        requested_by_username=requested_by_username,
        requested_by_user_id=requested_by_user_id,
        instance=instance,
        current_step="Queued install",
    )


def enqueue_server_install(
    db_path,
    *,
    requested_by_username: str,
    requested_by_user_id: int | None = None,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> JobRecord:
    """Create or return a queued/running server install job."""
    job, _created = ensure_server_install_job(
        db_path,
        requested_by_username=requested_by_username,
        requested_by_user_id=requested_by_user_id,
        instance=instance,
    )
    return job


def ensure_server_repair_job(
    db_path,
    *,
    requested_by_username: str,
    requested_by_user_id: int | None = None,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> tuple[JobRecord, bool]:
    """Return an active repair job, creating a queued job if needed."""
    return get_or_create_active_job(
        db_path,
        kind=SERVER_REPAIR_JOB_KIND,
        requested_by_username=requested_by_username,
        requested_by_user_id=requested_by_user_id,
        instance=instance,
        current_step="Queued repair",
    )


def enqueue_server_repair(
    db_path,
    *,
    requested_by_username: str,
    requested_by_user_id: int | None = None,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> JobRecord:
    """Create or return a queued/running server repair job."""
    job, _created = ensure_server_repair_job(
        db_path,
        requested_by_username=requested_by_username,
        requested_by_user_id=requested_by_user_id,
        instance=instance,
    )
    return job


def _append_generator_output(context: JobContext, lines) -> None:
    for line in lines:
        context.append_output(stdout=str(line))


def handle_server_install(context: JobContext) -> JobHandlerResult:
    """Run the server installer for a queued web job."""
    instance = context.job.instance or paths.DEFAULT_INSTANCE_NAME
    context.append_output(stdout=f"Starting install for {instance}.")
    _append_generator_output(context, installer.run_install(instance))
    return JobHandlerResult(
        result_message="Server install completed.",
        current_step="Install complete",
        progress_current=1,
        progress_total=1,
    )


def handle_server_repair(context: JobContext) -> JobHandlerResult:
    """Run server repair for a queued web job using discovered safe paths."""
    instance = context.job.instance or paths.DEFAULT_INSTANCE_NAME
    context.append_output(stdout=f"Starting repair for {instance}.")
    state = discovery.discover(instance=instance, save=False)
    install_dir = state.install_dir or str(paths.server_dir(instance))
    config_path = state.config_path or str(paths.config_file(instance))
    _append_generator_output(context, repair.run_repair(instance, install_dir, config_path))
    return JobHandlerResult(
        result_message="Server repair completed.",
        current_step="Repair complete",
        progress_current=1,
        progress_total=1,
    )


def create_server_job_dispatcher() -> JobDispatcher:
    """Return the explicit dispatcher used by a web worker for server jobs."""
    return JobDispatcher(
        {
            SERVER_INSTALL_JOB_KIND: handle_server_install,
            SERVER_REPAIR_JOB_KIND: handle_server_repair,
        }
    )


def dispatch_server_job(db_path, job_id: int):
    """Dispatch one queued server job through the explicit server-job registry."""
    return dispatch_job(db_path, job_id, create_server_job_dispatcher())


def _run_server_job_worker(db_path: Path, job_id: int) -> None:
    try:
        dispatch_server_job(db_path, job_id)
    except Exception:
        # dispatch_job records normal handler failures in web_jobs. This catch
        # only prevents an unexpected worker-level exception from crashing the
        # web process thread.
        return


def start_server_job_worker(db_path, job_id: int) -> threading.Thread:
    """Start one queued server job in a web-process background thread."""
    thread = threading.Thread(
        target=_run_server_job_worker,
        args=(Path(db_path), job_id),
        name=f"armactl-web-server-job-{job_id}",
        daemon=True,
    )
    thread.start()
    return thread
