"""Background job handler for manual player log event collection."""

from __future__ import annotations

import stat as stat_module
import threading
from pathlib import Path

from armactl import paths, player_log_collector
from armactl.player_log_collector import (
    DEFAULT_MAX_FILE_BYTES,
    DEFAULT_MAX_FILE_LINES,
    PlayerLogCollectionSummary,
)
from armactl.web.jobs.models import JobRecord
from armactl.web.jobs.runner import (
    JobContext,
    JobDispatcher,
    JobHandlerResult,
    dispatch_job,
)
from armactl.web.jobs.store import get_or_create_active_job
from armactl.web.services import player_registry
from armactl.web.services.audit import AuditLogError, append_audit_event
from armactl.web.services.player_identity import safe_player_text

PLAYER_LOG_COLLECTION_JOB_KIND = "players:collect-log-events"
PLAYER_LOG_COLLECTION_ACTION = "players.log-events.collect"
PLAYER_LOG_COLLECTION_SCOPE = "instance_config_profile_console_logs"
DEFAULT_MAX_LOG_FILES = 32


class PlayerLogCollectionAuditError(RuntimeError):
    """Raised when player log collection completes but audit cannot be written."""


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


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return -1.0


def _is_allowlisted_console_log(candidate: Path, logs_root: Path) -> bool:
    if candidate.name != "console.log":
        return False
    try:
        relative = candidate.relative_to(logs_root)
    except ValueError:
        return False
    if len(relative.parts) != 2:
        return False

    try:
        root_resolved = logs_root.resolve(strict=True)
        candidate_resolved = candidate.resolve(strict=True)
        candidate_resolved.relative_to(root_resolved)
        stat_result = candidate_resolved.stat()
    except (OSError, ValueError):
        return False
    return stat_module.S_ISREG(stat_result.st_mode)


def resolve_allowlisted_player_log_paths(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
    max_files: int = DEFAULT_MAX_LOG_FILES,
) -> tuple[Path, ...]:
    """Return bounded instance console logs already used by armactl telemetry.

    The web flow never accepts a path from a request. It only scans server
    profile logs created below the instance config directory:
    <data_root>/<instance>/config/logs/*/console.log.
    """
    if max_files < 1:
        return ()
    normalized_instance = _safe_instance(instance)
    logs_root = paths.config_dir(normalized_instance, data_root) / "logs"
    try:
        candidates = tuple(logs_root.glob("*/console.log"))
    except OSError:
        return ()

    allowlisted = tuple(
        candidate
        for candidate in candidates
        if _is_allowlisted_console_log(candidate, logs_root)
    )
    sorted_logs = sorted(
        allowlisted,
        key=lambda candidate: (_safe_mtime(candidate), candidate.name),
        reverse=True,
    )
    return tuple(sorted_logs[:max_files])


def ensure_player_log_collection_job(
    db_path,
    *,
    requested_by_username: str,
    requested_by_user_id: int | None = None,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> tuple[JobRecord, bool]:
    """Return an active player-log collection job, creating one if needed."""
    return get_or_create_active_job(
        db_path,
        kind=PLAYER_LOG_COLLECTION_JOB_KIND,
        requested_by_username=requested_by_username,
        requested_by_user_id=requested_by_user_id,
        instance=_safe_instance(instance),
        current_step="Queued player log collection",
    )


def _count_details(
    summary: PlayerLogCollectionSummary | None,
    *,
    phase: str,
    job_id: int | None,
    reason_class: str = "",
    reason_message: str = "",
) -> dict[str, object]:
    details: dict[str, object] = {
        "phase": phase,
        "job_kind": PLAYER_LOG_COLLECTION_JOB_KIND,
        "job_id": str(job_id or ""),
        "log_scope": PLAYER_LOG_COLLECTION_SCOPE,
        "max_file_bytes": str(DEFAULT_MAX_FILE_BYTES),
        "max_file_lines": str(DEFAULT_MAX_FILE_LINES),
        "max_files": str(DEFAULT_MAX_LOG_FILES),
        "files_requested": "0",
        "files_scanned": "0",
        "files_skipped": "0",
        "scanned_lines": "0",
        "parsed_events": "0",
        "stored_events": "0",
        "duplicate_events": "0",
        "unmatched_lines": "0",
        "skipped_lines": "0",
        "error_count": "0",
    }
    if summary is not None:
        details.update(
            {
                "files_requested": str(summary.files_requested),
                "files_scanned": str(summary.files_scanned),
                "files_skipped": str(summary.files_skipped),
                "scanned_lines": str(summary.lines_scanned),
                "parsed_events": str(summary.matched_events),
                "stored_events": str(summary.stored_events),
                "duplicate_events": str(summary.duplicate_events),
                "unmatched_lines": str(summary.unmatched_lines),
                "skipped_lines": str(summary.skipped_lines),
                "error_count": str(summary.error_count),
            }
        )
    if reason_class:
        details["reason_class"] = safe_player_text(reason_class, max_length=120)
    if reason_message:
        details["reason_message"] = safe_player_text(reason_message, max_length=240)
    return details


def _summary_message(summary: PlayerLogCollectionSummary) -> str:
    if summary.files_requested == 0:
        return "No allowlisted player logs found."
    if summary.error_count:
        return "Player log collection completed with skipped files."
    if summary.matched_events == 0:
        return "No matching player log events found."
    if summary.stored_events == 0 and summary.duplicate_events > 0:
        return "No new player log events found."
    return "Player log collection completed."


def _summary_output(summary: PlayerLogCollectionSummary) -> str:
    return (
        "Player log collection counts: "
        f"files_requested={summary.files_requested}; "
        f"files_scanned={summary.files_scanned}; "
        f"files_skipped={summary.files_skipped}; "
        f"scanned_lines={summary.lines_scanned}; "
        f"parsed_events={summary.matched_events}; "
        f"stored_events={summary.stored_events}; "
        f"duplicates={summary.duplicate_events}; "
        f"skipped_lines={summary.skipped_lines}; "
        f"errors={summary.error_count}"
    )


def _append_collection_outcome_audit(
    audit_log_path: Path,
    *,
    username: str,
    instance: str,
    job_id: int,
    summary: PlayerLogCollectionSummary | None,
    success: bool,
    message: str,
    reason_class: str = "",
    reason_message: str = "",
) -> None:
    append_audit_event(
        audit_log_path,
        username=username,
        action=PLAYER_LOG_COLLECTION_ACTION,
        instance=instance,
        target=PLAYER_LOG_COLLECTION_JOB_KIND,
        success=success,
        message=message,
        exit_code=0 if success else 1,
        details=_count_details(
            summary,
            phase="outcome",
            job_id=job_id,
            reason_class=reason_class,
            reason_message=reason_message,
        ),
    )


def _audit_failure_or_raise(
    audit_log_path: Path,
    *,
    username: str,
    instance: str,
    job_id: int,
    error: Exception,
) -> None:
    try:
        _append_collection_outcome_audit(
            audit_log_path,
            username=username,
            instance=instance,
            job_id=job_id,
            summary=None,
            success=False,
            message="Player log collection failed.",
            reason_class=type(error).__name__,
            reason_message="Player log collection failed.",
        )
    except AuditLogError as audit_error:
        raise PlayerLogCollectionAuditError(
            "Player log collection failed, and audit logging also failed."
        ) from audit_error


def handle_player_log_collection(context: JobContext) -> JobHandlerResult:
    """Collect allowlisted server log events into the instance player DB."""
    instance = _safe_instance(context.job.instance)
    data_root = _data_root_from_web_db_path(context.db_path)
    audit_log_path = paths.web_audit_log_file(data_root)
    registry_db_path = player_registry.player_registry_db_path(instance, data_root=data_root)
    log_paths = resolve_allowlisted_player_log_paths(
        instance,
        data_root=data_root,
        max_files=DEFAULT_MAX_LOG_FILES,
    )
    context.append_output(
        stdout=(
            "Starting allowlisted player log collection: "
            f"scope={PLAYER_LOG_COLLECTION_SCOPE}; files={len(log_paths)}"
        )
    )

    try:
        summary = player_log_collector.collect_player_log_events(
            log_paths,
            registry_db_path,
            dry_run=False,
            max_bytes=DEFAULT_MAX_FILE_BYTES,
            max_lines=DEFAULT_MAX_FILE_LINES,
        )
    except Exception as error:
        _audit_failure_or_raise(
            audit_log_path,
            username=context.job.requested_by_username,
            instance=instance,
            job_id=context.job.id,
            error=error,
        )
        raise RuntimeError("Player log collection failed.") from error

    context.append_output(stdout=_summary_output(summary))
    message = _summary_message(summary)
    try:
        _append_collection_outcome_audit(
            audit_log_path,
            username=context.job.requested_by_username,
            instance=instance,
            job_id=context.job.id,
            summary=summary,
            success=summary.error_count == 0,
            message=message,
        )
    except AuditLogError as error:
        raise PlayerLogCollectionAuditError(
            "Player log collection completed, but audit logging failed."
        ) from error

    return JobHandlerResult(
        result_message=message,
        current_step="Collection complete",
        progress_current=summary.files_scanned + summary.files_skipped,
        progress_total=summary.files_requested,
    )


def create_player_log_collection_dispatcher() -> JobDispatcher:
    """Return the explicit dispatcher for player log collection jobs."""
    return JobDispatcher({PLAYER_LOG_COLLECTION_JOB_KIND: handle_player_log_collection})


def dispatch_player_log_collection_job(db_path, job_id: int):
    """Dispatch one queued player-log collection job through the safe handler."""
    return dispatch_job(db_path, job_id, create_player_log_collection_dispatcher())


def _run_player_log_collection_worker(db_path: Path, job_id: int) -> None:
    try:
        dispatch_player_log_collection_job(db_path, job_id)
    except Exception:
        return


def start_player_log_collection_worker(db_path, job_id: int) -> threading.Thread:
    """Start one queued player-log collection job in a background thread."""
    thread = threading.Thread(
        target=_run_player_log_collection_worker,
        args=(Path(db_path), job_id),
        name=f"armactl-web-player-log-job-{job_id}",
        daemon=True,
    )
    thread.start()
    return thread
