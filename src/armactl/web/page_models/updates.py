# Lightweight server-update page model.

from __future__ import annotations

from pathlib import Path
from typing import Any

from armactl import paths
from armactl.platform.service_adapter import get_service_adapter
from armactl.state import ServerState, load_state
from armactl.web.jobs import SERVER_UPDATE_CHECK_JOB_KIND, SERVER_UPDATE_JOB_KIND
from armactl.web.jobs import store as job_store
from armactl.web.jobs.models import JOB_STATUS_FAILED, JobRecord
from armactl.web.services import server_versions


def _state_from_disk(instance: str, data_root: object) -> ServerState:
    data_root_path = Path(data_root)
    state = load_state(paths.state_file(instance, data_root_path))
    if state is None:
        state = ServerState()

    if not state.instance_root:
        state.instance_root = str(paths.instance_root(instance, data_root_path))
    if not state.install_dir:
        state.install_dir = str(paths.server_dir(instance, data_root_path))
    if not state.config_path:
        state.config_path = str(paths.config_file(instance, data_root_path))
    return state


def _job_summary(job: JobRecord) -> dict[str, object]:
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "started_at": job.started_at or "",
        "finished_at": job.finished_at or "",
    }


def _active_update_job_summary(
    db_path: Path,
    *,
    instance: str,
    check_state: str,
) -> dict[str, object] | None:
    if check_state == server_versions.SERVER_VERSION_CHECK_UPDATING:
        kind = SERVER_UPDATE_JOB_KIND
    elif check_state == server_versions.SERVER_VERSION_CHECK_CHECKING:
        kind = SERVER_UPDATE_CHECK_JOB_KIND
    else:
        return None

    try:
        job = job_store.get_active_job(db_path, kind=kind, instance=instance)
    except Exception:
        return None
    return _job_summary(job) if job is not None else None


def _latest_failed_update_job_summary(
    db_path: Path,
    *,
    instance: str,
) -> dict[str, object] | None:
    try:
        jobs = job_store.list_recent_jobs(db_path, limit=25)
    except Exception:
        return None

    for job in jobs:
        if job.kind != SERVER_UPDATE_JOB_KIND or job.instance != instance:
            continue
        return _job_summary(job) if job.status == JOB_STATUS_FAILED else None
    return None



def load_updates_page(
    instance: str,
    *,
    web_config: Any,
) -> dict[str, Any]:
    normalized_instance = paths.validate_instance_name(instance)
    state = _state_from_disk(normalized_instance, web_config.data_root)
    try:
        service_status = get_service_adapter().get_service_status(state.service_name)
        live_server_running = server_versions.service_status_blocks_update(service_status)
    except Exception:
        live_server_running = True

    try:
        version_state = server_versions.load_server_version_state(
            instance=normalized_instance,
            state=state,
            db_path=web_config.db_path,
            server_running=live_server_running,
        )
    except Exception as error:  # noqa: BLE001 - page rendering must fail closed.
        version_state = server_versions.failed_server_version_state(
            failure_reason=error,
            server_running=live_server_running,
        )
    version = version_state.to_dict()
    active_job = _active_update_job_summary(
        web_config.db_path,
        instance=normalized_instance,
        check_state=version_state.check_state,
    )
    if active_job is not None:
        version["active_job"] = active_job
    failed_update_job = _latest_failed_update_job_summary(
        web_config.db_path,
        instance=normalized_instance,
    )
    if failed_update_job is not None:
        version["failed_update_job"] = failed_update_job

    return dict(
        instance=normalized_instance,
        server_installed=bool(state.server_installed),
        server_running=bool(version_state.server_running),
        version=version,
    )
