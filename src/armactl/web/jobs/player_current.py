"""Background job handler for current-player registry refresh."""

from __future__ import annotations

import threading
from pathlib import Path

from armactl import paths
from armactl.web.jobs.models import JobRecord
from armactl.web.jobs.runner import (
    JobContext,
    JobDispatcher,
    JobHandlerResult,
    dispatch_job,
)
from armactl.web.jobs.store import get_or_create_active_job
from armactl.web.services import player_actions
from armactl.web.services.audit import AuditLogError, append_audit_event
from armactl.web.services.player_identity import safe_player_text

PLAYER_CURRENT_REFRESH_JOB_KIND = "players:refresh-current"
PLAYER_CURRENT_REFRESH_ACTION = "players.refresh-current"
PLAYER_CURRENT_REFRESH_SCOPE = "current_roster"


class PlayerCurrentRefreshAuditError(RuntimeError):
    """Raised when current-player refresh completes but audit cannot be written."""


def _data_root_from_web_db_path(db_path: Path) -> Path:
    """Infer the armactl data root from the standard web DB location."""
    db_path = Path(db_path)
    if db_path.name == "web.db" and db_path.parent.name == "web":
        return db_path.parent.parent
    return paths.DEFAULT_DATA_ROOT


def _safe_instance(instance: object) -> str:
    try:
        return paths.validate_instance_name(str(instance or paths.DEFAULT_INSTANCE_NAME))
    except paths.InvalidInstanceNameError:
        return paths.DEFAULT_INSTANCE_NAME


def ensure_player_current_refresh_job(
    db_path,
    *,
    requested_by_username: str,
    requested_by_user_id: int | None = None,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> tuple[JobRecord, bool]:
    """Return an active current-player refresh job, creating one if needed."""
    return get_or_create_active_job(
        db_path,
        kind=PLAYER_CURRENT_REFRESH_JOB_KIND,
        requested_by_username=requested_by_username,
        requested_by_user_id=requested_by_user_id,
        instance=_safe_instance(instance),
        current_step="Queued current-player refresh",
    )


def _count_details(
    result: player_actions.PlayerRefreshResult | None,
    *,
    phase: str,
    job_id: int | None,
    reason_class: str = "",
    reason_message: str = "",
) -> dict[str, object]:
    details: dict[str, object] = {
        "phase": phase,
        "job_kind": PLAYER_CURRENT_REFRESH_JOB_KIND,
        "job_id": str(job_id or ""),
        "scope": PLAYER_CURRENT_REFRESH_SCOPE,
        "observed": "0",
        "stored": "0",
        "ignored": "0",
        "source": "pending",
        "status": "pending",
    }
    if result is not None:
        details.update(
            {
                "observed": str(result.observed_count),
                "stored": str(result.stored_count),
                "ignored": str(result.ignored_count),
                "source": safe_player_text(result.source) or "unavailable",
                "status": safe_player_text(result.status) or "unknown",
            }
        )
    if reason_class:
        details["reason_class"] = safe_player_text(reason_class, max_length=120)
    if reason_message:
        details["reason_message"] = safe_player_text(reason_message, max_length=240)
    return details


def _summary_output(result: player_actions.PlayerRefreshResult) -> str:
    return (
        "Current player refresh counts: "
        f"observed={result.observed_count}; "
        f"stored={result.stored_count}; "
        f"ignored={result.ignored_count}; "
        f"source={safe_player_text(result.source) or 'unavailable'}; "
        f"status={safe_player_text(result.status) or 'unknown'}"
    )


def _append_refresh_outcome_audit(
    audit_log_path: Path,
    *,
    username: str,
    instance: str,
    job_id: int,
    result: player_actions.PlayerRefreshResult,
) -> None:
    append_audit_event(
        audit_log_path,
        username=username,
        action=PLAYER_CURRENT_REFRESH_ACTION,
        instance=instance,
        target=PLAYER_CURRENT_REFRESH_JOB_KIND,
        success=result.success,
        message=result.message,
        exit_code=0 if result.success else 1,
        details=_count_details(
            result,
            phase="outcome",
            job_id=job_id,
            reason_class=result.reason_class,
            reason_message="" if result.success else result.message,
        ),
    )


def handle_player_current_refresh(context: JobContext) -> JobHandlerResult:
    """Refresh known players from the current roster for one queued job."""
    instance = _safe_instance(context.job.instance)
    data_root = _data_root_from_web_db_path(context.db_path)
    audit_log_path = paths.web_audit_log_file(data_root)
    context.append_output(
        stdout=(
            "Starting current player refresh: "
            f"scope={PLAYER_CURRENT_REFRESH_SCOPE}"
        )
    )

    result = player_actions.refresh_current_players(instance, data_root=data_root)
    context.append_output(stdout=_summary_output(result))
    try:
        _append_refresh_outcome_audit(
            audit_log_path,
            username=context.job.requested_by_username,
            instance=instance,
            job_id=context.job.id,
            result=result,
        )
    except AuditLogError as error:
        raise PlayerCurrentRefreshAuditError(
            "Current player refresh completed, but audit logging failed."
        ) from error

    if not result.success:
        raise RuntimeError(result.message)

    return JobHandlerResult(
        result_message=result.message,
        current_step="Current-player refresh complete",
        progress_current=result.observed_count,
        progress_total=result.observed_count,
    )


def create_player_current_refresh_dispatcher() -> JobDispatcher:
    """Return the explicit dispatcher for current-player refresh jobs."""
    return JobDispatcher({PLAYER_CURRENT_REFRESH_JOB_KIND: handle_player_current_refresh})


def dispatch_player_current_refresh_job(db_path, job_id: int):
    """Dispatch one queued current-player refresh job through the safe handler."""
    return dispatch_job(db_path, job_id, create_player_current_refresh_dispatcher())


def _run_player_current_refresh_worker(db_path: Path, job_id: int) -> None:
    try:
        dispatch_player_current_refresh_job(db_path, job_id)
    except Exception:
        return


def start_player_current_refresh_worker(db_path, job_id: int) -> threading.Thread:
    """Start one queued current-player refresh job in a background thread."""
    thread = threading.Thread(
        target=_run_player_current_refresh_worker,
        args=(Path(db_path), job_id),
        name=f"armactl-web-player-current-job-{job_id}",
        daemon=True,
    )
    thread.start()
    return thread
