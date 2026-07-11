from __future__ import annotations

import threading
from pathlib import Path

from armactl import paths, player_log_collector
from armactl.web.jobs.models import JobRecord
from armactl.web.jobs.runner import (
    JobContext,
    JobDispatcher,
    JobHandlerResult,
    dispatch_job,
)
from armactl.web.jobs.store import get_or_create_active_job
from armactl.web.services import player_log_ingest
from armactl.web.services.audit import AuditLogError, append_audit_event

PLAYER_LOG_COLLECTION_JOB_KIND = "players:collect-log-events"
PLAYER_LOG_COLLECTION_ACTION = "players.log-events.collect"
PLAYER_LOG_COLLECTION_SCOPE = player_log_ingest.PLAYER_LOG_INGEST_SCOPE
DEFAULT_MAX_LOG_FILES = player_log_ingest.DEFAULT_MAX_LOG_FILES
DEFAULT_MAX_FILE_BYTES = player_log_collector.DEFAULT_MAX_FILE_BYTES
DEFAULT_MAX_FILE_LINES = player_log_collector.DEFAULT_MAX_FILE_LINES
resolve_allowlisted_player_log_paths = player_log_ingest.resolve_allowlisted_player_log_paths


class PlayerLogCollectionAuditError(RuntimeError):
    pass


def _data_root_from_web_db_path(db_path: Path) -> Path:
    db_path = Path(db_path)
    if db_path.name == "web.db" and db_path.parent.name == "web":
        return db_path.parent.parent
    return paths.DEFAULT_DATA_ROOT


def _safe_instance(instance: object) -> str:
    try:
        return paths.validate_instance_name(str(instance or paths.DEFAULT_INSTANCE_NAME))
    except paths.InvalidInstanceNameError:
        return paths.DEFAULT_INSTANCE_NAME


def ensure_player_log_collection_job(
    db_path,
    *,
    requested_by_username: str,
    requested_by_user_id: int | None = None,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> tuple[JobRecord, bool]:
    """Return an active manual collection job, creating one atomically if needed."""
    return get_or_create_active_job(
        db_path,
        kind=PLAYER_LOG_COLLECTION_JOB_KIND,
        requested_by_username=requested_by_username,
        requested_by_user_id=requested_by_user_id,
        instance=_safe_instance(instance),
        current_step="Queued player log collection",
    )


def _summary_message(result: player_log_ingest.PlayerLogIngestResult) -> str:
    if result.busy:
        return "Player log collection already running."
    if not result.completed:
        return "Player log collection failed."
    non_checkpoint_skips = {
        reason: count
        for reason, count in result.skipped_reason_counts.items()
        if reason != "unchanged" and count > 0
    }
    if result.files_considered == 0:
        return "No allowlisted player logs found."
    if non_checkpoint_skips or result.error_count:
        return "Player log collection completed with skipped files."
    if result.files_requested == 0 and result.skipped_reason_counts.get("unchanged"):
        return "No changed player logs found."
    if result.parsed_events == 0:
        return "No matching player log events found."
    if result.stored_events == 0 and result.duplicate_events > 0:
        return "No new player log events found."
    return "Player log collection completed."


def _count_details(
    result: player_log_ingest.PlayerLogIngestResult,
    *,
    job_id: int,
) -> dict[str, object]:
    details: dict[str, object] = {
        "phase": "outcome",
        "job_kind": PLAYER_LOG_COLLECTION_JOB_KIND,
        "job_id": str(job_id),
        "log_scope": result.scope,
        "max_file_bytes": str(DEFAULT_MAX_FILE_BYTES),
        "max_file_lines": str(DEFAULT_MAX_FILE_LINES),
        "max_files": str(DEFAULT_MAX_LOG_FILES),
        "files_considered": str(result.files_considered),
        "files_selected_for_scan": str(result.files_selected_for_scan),
        "files_requested": str(result.files_requested),
        "files_scanned": str(result.files_scanned),
        "files_skipped": str(result.files_skipped),
        "scanned_lines": str(result.scanned_lines),
        "parsed_events": str(result.parsed_events),
        "stored_events": str(result.stored_events),
        "duplicate_events": str(result.duplicate_events),
        "unmatched_lines": str(result.unmatched_lines),
        "skipped_lines": str(result.skipped_lines),
        "error_count": str(result.error_count),
        "checkpoint_updated": "true" if result.checkpoint_updated else "false",
        "freshness_status": result.freshness_status,
        "freshness_at": result.freshness_at,
        "outcome": result.outcome,
    }
    if result.skipped_reasons:
        details["skipped_reasons"] = result.skipped_reasons
        collector_reasons = {
            reason: count
            for reason, count in result.skipped_reason_counts.items()
            if reason != "unchanged"
        }
        if collector_reasons:
            details["skipped_file_reasons"] = player_log_ingest.format_reason_counts(
                collector_reasons
            )
    if result.checkpoint_reset_reasons:
        details["checkpoint_reset_reasons"] = result.checkpoint_reset_reasons
    if result.failure_code:
        details["reason_class"] = result.failure_code
        details["reason_message"] = _summary_message(result)
    return details


def _summary_output(result: player_log_ingest.PlayerLogIngestResult) -> str:
    output = (
        "Player log collection counts: "
        f"files_considered={result.files_considered}; "
        f"files_selected_for_scan={result.files_selected_for_scan}; "
        f"files_requested={result.files_requested}; "
        f"files_scanned={result.files_scanned}; "
        f"files_skipped={result.files_skipped}; "
        f"scanned_lines={result.scanned_lines}; "
        f"parsed_events={result.parsed_events}; "
        f"stored_events={result.stored_events}; "
        f"duplicates={result.duplicate_events}; "
        f"skipped_lines={result.skipped_lines}; "
        f"errors={result.error_count}; "
        f"checkpoint_updated={'true' if result.checkpoint_updated else 'false'}; "
        f"freshness_status={result.freshness_status}; "
        f"freshness_at={result.freshness_at}; "
        f"outcome={result.outcome}"
    )
    if result.skipped_reasons:
        output = f"{output}; skipped_reasons={result.skipped_reasons}"
        collector_reasons = {
            reason: count
            for reason, count in result.skipped_reason_counts.items()
            if reason != "unchanged"
        }
        if collector_reasons:
            output = (
                f"{output}; skipped_file_reasons="
                f"{player_log_ingest.format_reason_counts(collector_reasons)}"
            )
    if result.checkpoint_reset_reasons:
        output = (
            f"{output}; checkpoint_reset_reasons={result.checkpoint_reset_reasons}"
        )
    if result.failure_code:
        output = f"{output}; failure_code={result.failure_code}"
    return output


def _append_collection_outcome_audit(
    audit_log_path: Path,
    *,
    username: str,
    instance: str,
    job_id: int,
    result: player_log_ingest.PlayerLogIngestResult,
) -> None:
    append_audit_event(
        audit_log_path,
        username=username,
        action=PLAYER_LOG_COLLECTION_ACTION,
        instance=instance,
        target=PLAYER_LOG_COLLECTION_JOB_KIND,
        success=result.success,
        message=_summary_message(result),
        exit_code=result.exit_code,
        details=_count_details(result, job_id=job_id),
    )


def handle_player_log_collection(context: JobContext) -> JobHandlerResult:
    """Thin web-job adapter around the shared synchronous one-shot service."""
    instance = _safe_instance(context.job.instance)
    data_root = _data_root_from_web_db_path(context.db_path)
    audit_log_path = paths.web_audit_log_file(data_root)
    context.append_output(
        stdout=(
            "Starting allowlisted player log collection: "
            f"scope={PLAYER_LOG_COLLECTION_SCOPE}"
        )
    )

    result = player_log_ingest.run_player_log_ingest_once(
        instance,
        data_root=data_root,
        max_files=DEFAULT_MAX_LOG_FILES,
        max_bytes=DEFAULT_MAX_FILE_BYTES,
        max_lines=DEFAULT_MAX_FILE_LINES,
    )
    context.append_output(stdout=_summary_output(result))

    try:
        _append_collection_outcome_audit(
            audit_log_path,
            username=context.job.requested_by_username,
            instance=instance,
            job_id=context.job.id,
            result=result,
        )
    except AuditLogError as error:
        raise PlayerLogCollectionAuditError(
            "Player log collection completed, but audit logging failed."
        ) from error

    if not result.completed:
        raise RuntimeError(_summary_message(result))

    return JobHandlerResult(
        result_message=_summary_message(result),
        current_step="Collection complete",
        progress_current=result.files_scanned + result.files_skipped,
        progress_total=result.files_considered,
    )


def create_player_log_collection_dispatcher() -> JobDispatcher:
    return JobDispatcher({PLAYER_LOG_COLLECTION_JOB_KIND: handle_player_log_collection})


def dispatch_player_log_collection_job(db_path, job_id: int):
    return dispatch_job(db_path, job_id, create_player_log_collection_dispatcher())


def _run_player_log_collection_worker(db_path: Path, job_id: int) -> None:
    try:
        dispatch_player_log_collection_job(db_path, job_id)
    except Exception:
        return


def start_player_log_collection_worker(db_path, job_id: int) -> threading.Thread:
    """Start the existing web-only daemon worker for an already persisted job."""
    thread = threading.Thread(
        target=_run_player_log_collection_worker,
        args=(Path(db_path), job_id),
        name=f"armactl-web-player-log-job-{job_id}",
        daemon=True,
    )
    thread.start()
    return thread
