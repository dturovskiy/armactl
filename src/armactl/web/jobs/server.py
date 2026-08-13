"""Explicit server install/repair job handlers for armactl web."""

from __future__ import annotations

import threading
from pathlib import Path

from armactl import discovery, installer, paths, repair, safe_update
from armactl.platform.service_adapter import get_service_adapter
from armactl.web.jobs.models import JOB_STATUS_WARNING, JobRecord
from armactl.web.jobs.runner import (
    JobContext,
    JobDispatcher,
    JobHandlerResult,
    dispatch_job,
)
from armactl.web.jobs.store import get_job, get_or_create_active_job

SERVER_INSTALL_JOB_KIND = "server:install"
SERVER_REPAIR_JOB_KIND = "server:repair"
SERVER_UPDATE_JOB_KIND = "server:update"
SERVER_UPDATE_CHECK_JOB_KIND = "server:update-check"
SERVER_VANILLA_JOB_KIND = "server:vanilla"
SERVER_RETRY_MODDED_JOB_KIND = "server:retry-modded"
SERVER_PARKED_PROFILE_TEST_JOB_KIND = "server:test-parked"
SERVER_PROFILE_SWITCH_JOB_PREFIX = "server:ps:"
SERVER_PROFILE_TEST_JOB_PREFIX = "server:pt:"
SERVER_PROFILE_CREATE_JOB_PREFIX = "server:pc:"
SERVER_VANILLA_PROFILE_CREATE_JOB_PREFIX = "server:pv:"
SERVER_JOB_KINDS = frozenset(
    {
        SERVER_INSTALL_JOB_KIND,
        SERVER_REPAIR_JOB_KIND,
        SERVER_UPDATE_JOB_KIND,
        SERVER_UPDATE_CHECK_JOB_KIND,
        SERVER_VANILLA_JOB_KIND,
        SERVER_RETRY_MODDED_JOB_KIND,
        SERVER_PARKED_PROFILE_TEST_JOB_KIND,
    }
)
STOP_RUNNING_SERVER_UPDATE_MESSAGE = "Stop the game server before updating."
STOP_RUNNING_SERVER_PROFILE_MESSAGE = "Stop the game server before testing or changing profiles."


def _service_status_is_active(status: dict[str, object]) -> bool:
    active_state = str(status.get("active_state") or status.get("ActiveState") or "")
    sub_state = str(status.get("sub_state") or status.get("SubState") or "")
    return active_state.strip().lower() in {"active", "activating"} or (
        sub_state.strip().lower() in {"running", "start", "auto-restart"}
    )


def _stop_running_service_for_job(context: JobContext, state, service_name: str):
    adapter = get_service_adapter()
    if not bool(state.server_running):
        return adapter, False
    context.append_output(
        stdout="Stopping the running game server for the maintenance operation."
    )
    result = adapter.stop_service(service_name)
    if not result.success:
        raise RuntimeError(f"Could not stop the game server: {result.message}")
    return adapter, True


def _restore_previously_running_service(
    context: JobContext,
    adapter,
    service_name: str,
    *,
    was_running: bool,
) -> None:
    if not was_running:
        return
    try:
        if _service_status_is_active(adapter.get_service_status(service_name)):
            return
    except Exception:  # noqa: BLE001 - an idempotent start is the safe fallback.
        pass
    context.append_output(
        stdout="Restoring the game service after the maintenance operation."
    )
    result = adapter.start_service(service_name)
    if not result.success:
        raise RuntimeError(f"Could not restore the game server: {result.message}")


def server_profile_job_kind(action: str, name: str = "") -> str:
    """Return one bounded job kind containing only a validated profile name."""
    if action == "vanilla":
        return SERVER_VANILLA_JOB_KIND
    if action == "retry-modded":
        return SERVER_RETRY_MODDED_JOB_KIND
    if action == "test-parked":
        return SERVER_PARKED_PROFILE_TEST_JOB_KIND
    safe_name = safe_update.validate_profile_name(name)
    prefixes = {
        "switch": SERVER_PROFILE_SWITCH_JOB_PREFIX,
        "test": SERVER_PROFILE_TEST_JOB_PREFIX,
        "create": SERVER_PROFILE_CREATE_JOB_PREFIX,
        "create-vanilla": SERVER_VANILLA_PROFILE_CREATE_JOB_PREFIX,
    }
    try:
        prefix = prefixes[action]
    except KeyError as exc:
        raise ValueError("Unknown profile job action.") from exc
    return f"{prefix}{safe_name}"


def _profile_job_action(kind: str) -> tuple[str, str] | None:
    if kind == SERVER_VANILLA_JOB_KIND:
        return "vanilla", ""
    if kind == SERVER_RETRY_MODDED_JOB_KIND:
        return "retry-modded", ""
    if kind == SERVER_PARKED_PROFILE_TEST_JOB_KIND:
        return "test-parked", ""
    for prefix, action in (
        (SERVER_PROFILE_SWITCH_JOB_PREFIX, "switch"),
        (SERVER_PROFILE_TEST_JOB_PREFIX, "test"),
        (SERVER_PROFILE_CREATE_JOB_PREFIX, "create"),
        (SERVER_VANILLA_PROFILE_CREATE_JOB_PREFIX, "create-vanilla"),
    ):
        if kind.startswith(prefix):
            try:
                return action, safe_update.validate_profile_name(kind.removeprefix(prefix))
            except safe_update.UpdateProfileError:
                return None
    return None


def parse_server_profile_job_kind(kind: str) -> tuple[str, str] | None:
    """Return the validated profile action/target encoded in a job kind."""
    return _profile_job_action(kind)


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


def ensure_server_update_check_job(
    db_path,
    *,
    requested_by_username: str,
    requested_by_user_id: int | None = None,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> tuple[JobRecord, bool]:
    """Return an active update-check job, creating a queued job if needed."""
    return get_or_create_active_job(
        db_path,
        kind=SERVER_UPDATE_CHECK_JOB_KIND,
        requested_by_username=requested_by_username,
        requested_by_user_id=requested_by_user_id,
        instance=instance,
        current_step="Queued update check",
    )


def ensure_server_update_job(
    db_path,
    *,
    requested_by_username: str,
    requested_by_user_id: int | None = None,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> tuple[JobRecord, bool]:
    """Return an active update job, creating a queued job if needed."""
    return get_or_create_active_job(
        db_path,
        kind=SERVER_UPDATE_JOB_KIND,
        requested_by_username=requested_by_username,
        requested_by_user_id=requested_by_user_id,
        instance=instance,
        current_step="Queued update",
    )


def enqueue_server_update(
    db_path,
    *,
    requested_by_username: str,
    requested_by_user_id: int | None = None,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> JobRecord:
    """Create or return a queued/running server update job."""
    job, _created = ensure_server_update_job(
        db_path,
        requested_by_username=requested_by_username,
        requested_by_user_id=requested_by_user_id,
        instance=instance,
    )
    return job


def enqueue_server_update_check(
    db_path,
    *,
    requested_by_username: str,
    requested_by_user_id: int | None = None,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> JobRecord:
    """Create or return a queued/running server update-check job."""
    job, _created = ensure_server_update_check_job(
        db_path,
        requested_by_username=requested_by_username,
        requested_by_user_id=requested_by_user_id,
        instance=instance,
    )
    return job


def ensure_server_profile_job(
    db_path,
    *,
    action: str,
    name: str = "",
    requested_by_username: str,
    requested_by_user_id: int | None = None,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> tuple[JobRecord, bool]:
    """Return an active profile job, creating a queued job if needed."""
    kind = server_profile_job_kind(action, name)
    return get_or_create_active_job(
        db_path,
        kind=kind,
        requested_by_username=requested_by_username,
        requested_by_user_id=requested_by_user_id,
        instance=instance,
        current_step="Queued profile operation",
    )


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


def handle_server_update_check(context: JobContext) -> JobHandlerResult:
    """Run an explicit latest-build check and cache its safe result."""
    from armactl.web.services import server_versions

    instance = context.job.instance or paths.DEFAULT_INSTANCE_NAME
    context.append_output(stdout=f"Checking latest server build for {instance}.")
    state = discovery.discover(instance=instance, save=False)
    version_state = server_versions.refresh_latest_server_version_state(
        context.db_path,
        instance=instance,
        state=state,
        job_id=context.job.id,
    )
    installed = version_state.installed or "unknown"
    latest = version_state.latest or "unknown"
    context.append_output(
        stdout=(
            f"{version_state.message}: installed={installed}; "
            f"latest={latest}; branch={version_state.branch}"
        )
    )
    if version_state.check_state == server_versions.SERVER_VERSION_CHECK_FAILED:
        raise RuntimeError(
            version_state.failure_reason
            or server_versions.SERVER_VERSION_MESSAGE_FAILED
        )
    return JobHandlerResult(
        result_message=server_versions.SERVER_VERSION_MESSAGE_CHECK_COMPLETED,
        current_step="Update check complete",
        progress_current=1,
        progress_total=1,
    )


def handle_server_update(context: JobContext) -> JobHandlerResult:
    """Run an isolated update canary with promotion and automatic rollback."""
    instance = context.job.instance or paths.DEFAULT_INSTANCE_NAME
    context.append_output(stdout=f"Starting update for {instance}.")
    state = discovery.discover(instance=instance, save=False)
    install_dir = Path(state.install_dir or paths.server_dir(instance))
    install_dir = paths.validate_server_install_dir(install_dir, instance=instance)
    if not install_dir.exists():
        raise RuntimeError("Server install directory is missing.")
    config_path = Path(state.config_path or paths.config_file(instance))
    service_name = state.service_name or paths.SERVICE_NAME
    adapter, was_running = _stop_running_service_for_job(
        context,
        state,
        service_name,
    )
    try:
        _append_generator_output(
            context,
            safe_update.stream_safe_server_update(
                install_dir,
                config_path,
                service_name,
                instance=instance,
            ),
        )
    finally:
        _restore_previously_running_service(
            context,
            adapter,
            service_name,
            was_running=was_running,
        )
    discovery.discover(instance=instance, save=True)
    return JobHandlerResult(
        result_message="Server update verified and started.",
        current_step="Update complete",
        progress_current=1,
        progress_total=1,
    )


def handle_server_profile_action(context: JobContext) -> JobHandlerResult:
    """Run one validated profile create/switch/fallback operation."""
    parsed = _profile_job_action(context.job.kind)
    if parsed is None:
        raise RuntimeError("Profile job kind is invalid.")
    action, name = parsed
    instance = context.job.instance or paths.DEFAULT_INSTANCE_NAME
    state = discovery.discover(instance=instance, save=False)
    install_dir = Path(state.install_dir or paths.server_dir(instance))
    install_dir = paths.validate_server_install_dir(install_dir, instance=instance)
    config_path = Path(state.config_path or paths.config_file(instance))
    service_name = state.service_name or paths.SERVICE_NAME

    if action == "create" or action == "create-vanilla":
        created = safe_update.create_named_profile(
            install_dir,
            config_path,
            name=name,
            vanilla=action == "create-vanilla",
        )
        context.append_output(stdout=f"Created profile {created.name} at {created.path}.")
        message = f"Profile {created.name} created."
    else:
        adapter, was_running = _stop_running_service_for_job(
            context,
            state,
            service_name,
        )
        try:
            if action == "test":
                try:
                    lines = safe_update.verify_named_profile(
                        install_dir,
                        config_path,
                        name=name,
                    )
                    _append_generator_output(context, lines)
                except safe_update.ProfileIncompatibleError as exc:
                    return JobHandlerResult(
                        result_message=str(exc),
                        current_step="Profile incompatible",
                        progress_current=1,
                        progress_total=1,
                        status=JOB_STATUS_WARNING,
                    )
                message = f"Profile {name} is compatible with the current build."
            elif action == "test-parked":
                try:
                    lines = safe_update.verify_parked_modded_profile(
                        install_dir,
                        config_path,
                    )
                    _append_generator_output(context, lines)
                except safe_update.ProfileIncompatibleError as exc:
                    return JobHandlerResult(
                        result_message=str(exc),
                        current_step="Profile incompatible",
                        progress_current=1,
                        progress_total=1,
                        status=JOB_STATUS_WARNING,
                    )
                message = "Parked profile is compatible with the current build."
            elif action == "switch":
                lines = safe_update.switch_named_profile(
                    install_dir,
                    config_path,
                    service_name,
                    name=name,
                )
                message = "Server profile verified and activated."
            else:
                operation = {
                    "vanilla": safe_update.activate_vanilla,
                    "retry-modded": safe_update.retry_modded,
                }.get(action)
                if operation is None:
                    raise RuntimeError("Unknown profile operation.")
                lines = operation(
                    install_dir,
                    config_path,
                    service_name,
                )
                message = "Server profile verified and activated."
            if action not in {"test", "test-parked"}:
                _append_generator_output(context, lines)
            if action not in {"test", "test-parked"}:
                discovery.discover(instance=instance, save=True)
        finally:
            _restore_previously_running_service(
                context,
                adapter,
                service_name,
                was_running=was_running,
            )
    return JobHandlerResult(
        result_message=message,
        current_step="Profile operation complete",
        progress_current=1,
        progress_total=1,
    )


def create_server_job_dispatcher(job_kind: str = "") -> JobDispatcher:
    """Return the explicit dispatcher used by a web worker for server jobs."""
    handlers = {
            SERVER_INSTALL_JOB_KIND: handle_server_install,
            SERVER_REPAIR_JOB_KIND: handle_server_repair,
            SERVER_UPDATE_CHECK_JOB_KIND: handle_server_update_check,
            SERVER_UPDATE_JOB_KIND: handle_server_update,
        }
    if _profile_job_action(job_kind) is not None:
        handlers[job_kind] = handle_server_profile_action
    return JobDispatcher(handlers)


def dispatch_server_job(db_path, job_id: int):
    """Dispatch one queued server job through the explicit server-job registry."""
    job = get_job(db_path, job_id)
    return dispatch_job(
        db_path,
        job_id,
        create_server_job_dispatcher(job.kind if job is not None else ""),
    )


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
