from __future__ import annotations

import stat as stat_module
import threading
from collections import Counter
from datetime import datetime, timezone
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
from armactl.web.services import player_log_ingest, player_registry
from armactl.web.services.audit import AuditLogError, append_audit_event
from armactl.web.services.player_identity import safe_player_text

PLAYER_LOG_COLLECTION_JOB_KIND = "players:collect-log-events"
PLAYER_LOG_COLLECTION_ACTION = "players.log-events.collect"
PLAYER_LOG_COLLECTION_SCOPE = "instance_config_profile_console_logs"
DEFAULT_MAX_LOG_FILES = 32
_CONTROLLED_PARTIAL_SKIP_ERROR_CODES = frozenset(
    {
        "file_too_large",
        "missing_file",
    }
)


class PlayerLogCollectionAuditError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    return get_or_create_active_job(
        db_path,
        kind=PLAYER_LOG_COLLECTION_JOB_KIND,
        requested_by_username=requested_by_username,
        requested_by_user_id=requested_by_user_id,
        instance=_safe_instance(instance),
        current_step="Queued player log collection",
    )


def _skipped_file_reason_counts(
    summary: PlayerLogCollectionSummary | None,
) -> dict[str, int]:
    if summary is None or not summary.errors:
        return {}
    return dict(sorted(Counter(error.code for error in summary.errors).items()))


def _skipped_reason_counts(
    summary: PlayerLogCollectionSummary | None,
    plan: player_log_ingest.PlayerLogIngestPlan | None,
) -> dict[str, int]:
    return player_log_ingest.combine_reason_counts(
        plan.skipped_reason_counts if plan is not None else {},
        _skipped_file_reason_counts(summary),
    )


def _skipped_file_reason_text(summary: PlayerLogCollectionSummary | None) -> str:
    return player_log_ingest.format_reason_counts(_skipped_file_reason_counts(summary))


def _skipped_reason_text(
    summary: PlayerLogCollectionSummary | None,
    plan: player_log_ingest.PlayerLogIngestPlan | None,
) -> str:
    return player_log_ingest.format_reason_counts(_skipped_reason_counts(summary, plan))


def _audit_success_for_summary(summary: PlayerLogCollectionSummary) -> bool:
    if summary.error_count == 0:
        return True
    if summary.files_scanned < 1:
        return False
    return set(_skipped_file_reason_counts(summary)).issubset(
        _CONTROLLED_PARTIAL_SKIP_ERROR_CODES
    )


def _count_details(
    summary: PlayerLogCollectionSummary | None,
    *,
    phase: str,
    job_id: int | None,
    plan: player_log_ingest.PlayerLogIngestPlan | None = None,
    checkpoint_updated: bool = False,
    freshness_status: str = "",
    freshness_at: str = "",
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
        "files_considered": str(plan.files_considered if plan is not None else 0),
        "files_selected_for_scan": str(
            plan.files_selected_for_scan if plan is not None else 0
        ),
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
        "checkpoint_updated": "true" if checkpoint_updated else "false",
        "freshness_status": safe_player_text(freshness_status, max_length=80),
        "freshness_at": safe_player_text(freshness_at, max_length=80),
    }
    if plan is not None:
        checkpoint_reset_reasons = player_log_ingest.format_reason_counts(
            plan.checkpoint_reset_reason_counts,
        )
        if checkpoint_reset_reasons:
            details["checkpoint_reset_reasons"] = checkpoint_reset_reasons
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
        skipped_file_reasons = _skipped_file_reason_text(summary)
        if skipped_file_reasons:
            details["skipped_file_reasons"] = skipped_file_reasons
    skipped_reasons = _skipped_reason_text(summary, plan)
    if skipped_reasons:
        details["skipped_reasons"] = skipped_reasons
    if reason_class:
        details["reason_class"] = safe_player_text(reason_class, max_length=120)
    if reason_message:
        details["reason_message"] = safe_player_text(reason_message, max_length=240)
    return details


def _summary_message(
    summary: PlayerLogCollectionSummary,
    plan: player_log_ingest.PlayerLogIngestPlan,
) -> str:
    skipped_counts = _skipped_reason_counts(summary, plan)
    non_checkpoint_skips = {
        reason: count
        for reason, count in skipped_counts.items()
        if reason != "unchanged" and count > 0
    }
    if plan.files_considered == 0:
        return "No allowlisted player logs found."
    if non_checkpoint_skips or summary.error_count:
        return "Player log collection completed with skipped files."
    if summary.files_requested == 0 and skipped_counts.get("unchanged"):
        return "No changed player logs found."
    if summary.matched_events == 0:
        return "No matching player log events found."
    if summary.stored_events == 0 and summary.duplicate_events > 0:
        return "No new player log events found."
    return "Player log collection completed."


def _freshness_status(
    summary: PlayerLogCollectionSummary,
    plan: player_log_ingest.PlayerLogIngestPlan,
    *,
    audit_success: bool,
) -> str:
    if not audit_success:
        return player_registry.PLAYER_LOG_INGEST_STATUS_FAILED
    if plan.files_considered == 0:
        return player_registry.PLAYER_LOG_INGEST_STATUS_NO_LOGS
    skipped_counts = _skipped_reason_counts(summary, plan)
    if summary.error_count or any(
        reason != "unchanged" and count > 0 for reason, count in skipped_counts.items()
    ):
        return player_registry.PLAYER_LOG_INGEST_STATUS_PARTIAL
    return player_registry.PLAYER_LOG_INGEST_STATUS_FRESH


def _summary_output(
    summary: PlayerLogCollectionSummary,
    *,
    plan: player_log_ingest.PlayerLogIngestPlan,
    checkpoint_updated: bool,
    freshness_status: str,
    freshness_at: str,
) -> str:
    checkpoint_text = "true" if checkpoint_updated else "false"
    output = (
        "Player log collection counts: "
        f"files_considered={plan.files_considered}; "
        f"files_selected_for_scan={plan.files_selected_for_scan}; "
        f"files_requested={summary.files_requested}; "
        f"files_scanned={summary.files_scanned}; "
        f"files_skipped={summary.files_skipped}; "
        f"scanned_lines={summary.lines_scanned}; "
        f"parsed_events={summary.matched_events}; "
        f"stored_events={summary.stored_events}; "
        f"duplicates={summary.duplicate_events}; "
        f"skipped_lines={summary.skipped_lines}; "
        f"errors={summary.error_count}; "
        f"checkpoint_updated={checkpoint_text}; "
        f"freshness_status={safe_player_text(freshness_status, max_length=80)}; "
        f"freshness_at={safe_player_text(freshness_at, max_length=80)}"
    )
    skipped_file_reasons = _skipped_file_reason_text(summary)
    if skipped_file_reasons:
        output = f"{output}; skipped_file_reasons={skipped_file_reasons}"
    skipped_reasons = _skipped_reason_text(summary, plan)
    if skipped_reasons:
        output = f"{output}; skipped_reasons={skipped_reasons}"
    checkpoint_reset_reasons = player_log_ingest.format_reason_counts(
        plan.checkpoint_reset_reason_counts,
    )
    if checkpoint_reset_reasons:
        output = f"{output}; checkpoint_reset_reasons={checkpoint_reset_reasons}"
    return output


def _append_collection_outcome_audit(
    audit_log_path: Path,
    *,
    username: str,
    instance: str,
    job_id: int,
    summary: PlayerLogCollectionSummary | None,
    success: bool,
    message: str,
    plan: player_log_ingest.PlayerLogIngestPlan | None = None,
    checkpoint_updated: bool = False,
    freshness_status: str = "",
    freshness_at: str = "",
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
            plan=plan,
            checkpoint_updated=checkpoint_updated,
            freshness_status=freshness_status,
            freshness_at=freshness_at,
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
    plan: player_log_ingest.PlayerLogIngestPlan | None = None,
    freshness_at: str = "",
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
            plan=plan,
            freshness_status=player_registry.PLAYER_LOG_INGEST_STATUS_FAILED,
            freshness_at=freshness_at,
            reason_class=type(error).__name__,
            reason_message="Player log collection failed.",
        )
    except AuditLogError as audit_error:
        raise PlayerLogCollectionAuditError(
            "Player log collection failed, and audit logging also failed."
        ) from audit_error


def _record_failed_freshness(
    registry_db_path: Path,
    *,
    plan: player_log_ingest.PlayerLogIngestPlan | None,
    run_at: str,
) -> None:
    skipped_reasons = player_log_ingest.format_reason_counts(
        plan.skipped_reason_counts if plan is not None else {},
    )
    try:
        player_registry.record_player_log_ingest_freshness(
            registry_db_path,
            scope=PLAYER_LOG_COLLECTION_SCOPE,
            status=player_registry.PLAYER_LOG_INGEST_STATUS_FAILED,
            last_run_at=run_at,
            scanned_files=0,
            parsed_events=0,
            stored_events=0,
            skipped_files=plan.skipped_files if plan is not None else 0,
            skipped_reasons=skipped_reasons,
            checkpoint_updated=False,
        )
    except Exception:
        return


def handle_player_log_collection(context: JobContext) -> JobHandlerResult:
    instance = _safe_instance(context.job.instance)
    data_root = _data_root_from_web_db_path(context.db_path)
    audit_log_path = paths.web_audit_log_file(data_root)
    registry_db_path = player_registry.player_registry_db_path(instance, data_root=data_root)
    log_paths = resolve_allowlisted_player_log_paths(
        instance,
        data_root=data_root,
        max_files=DEFAULT_MAX_LOG_FILES,
    )
    run_started_at = _utc_now()
    plan = player_log_ingest.plan_player_log_ingest(
        registry_db_path,
        log_paths,
        scope=PLAYER_LOG_COLLECTION_SCOPE,
    )
    context.append_output(
        stdout=(
            "Starting allowlisted player log collection: "
            f"scope={PLAYER_LOG_COLLECTION_SCOPE}; "
            f"files_considered={plan.files_considered}; "
            f"files_selected_for_scan={plan.files_selected_for_scan}"
        )
    )

    try:
        summary = player_log_collector.collect_player_log_events(
            plan.files_to_scan,
            registry_db_path,
            dry_run=False,
            max_bytes=DEFAULT_MAX_FILE_BYTES,
            max_lines=DEFAULT_MAX_FILE_LINES,
        )
    except Exception as error:
        _record_failed_freshness(
            registry_db_path,
            plan=plan,
            run_at=run_started_at,
        )
        _audit_failure_or_raise(
            audit_log_path,
            username=context.job.requested_by_username,
            instance=instance,
            job_id=context.job.id,
            error=error,
            plan=plan,
            freshness_at=run_started_at,
        )
        raise RuntimeError("Player log collection failed.") from error

    freshness_at = _utc_now()
    checkpoint_records = player_log_ingest.checkpoint_records_for_collection_summary(
        plan,
        summary,
        updated_at=freshness_at,
    )
    checkpoint_updated = bool(
        player_registry.upsert_player_log_ingest_checkpoints(
            registry_db_path,
            checkpoint_records,
        )
    )
    audit_success = _audit_success_for_summary(summary)
    freshness_status = _freshness_status(summary, plan, audit_success=audit_success)
    skipped_reasons = _skipped_reason_text(summary, plan)
    player_registry.record_player_log_ingest_freshness(
        registry_db_path,
        scope=PLAYER_LOG_COLLECTION_SCOPE,
        status=freshness_status,
        last_run_at=freshness_at,
        scanned_files=summary.files_scanned,
        parsed_events=summary.matched_events,
        stored_events=summary.stored_events,
        skipped_files=summary.files_skipped + plan.skipped_files,
        skipped_reasons=skipped_reasons,
        checkpoint_updated=checkpoint_updated,
    )

    context.append_output(
        stdout=_summary_output(
            summary,
            plan=plan,
            checkpoint_updated=checkpoint_updated,
            freshness_status=freshness_status,
            freshness_at=freshness_at,
        ),
    )
    message = _summary_message(summary, plan)
    try:
        _append_collection_outcome_audit(
            audit_log_path,
            username=context.job.requested_by_username,
            instance=instance,
            job_id=context.job.id,
            summary=summary,
            success=audit_success,
            message=message,
            plan=plan,
            checkpoint_updated=checkpoint_updated,
            freshness_status=freshness_status,
            freshness_at=freshness_at,
        )
    except AuditLogError as error:
        raise PlayerLogCollectionAuditError(
            "Player log collection completed, but audit logging failed."
        ) from error

    return JobHandlerResult(
        result_message=message,
        current_step="Collection complete",
        progress_current=summary.files_scanned + summary.files_skipped + plan.skipped_files,
        progress_total=plan.files_considered,
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
    thread = threading.Thread(
        target=_run_player_log_collection_worker,
        args=(Path(db_path), job_id),
        name=f"armactl-web-player-log-job-{job_id}",
        daemon=True,
    )
    thread.start()
    return thread
