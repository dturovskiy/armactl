"""Background job handler for stored player log event sessionization."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
from armactl.web.services import (
    player_live_session_scanner,
    player_registry,
    player_sessionizer,
)
from armactl.web.services.audit import AuditLogError, append_audit_event
from armactl.web.services.player_identity import safe_player_text

PLAYER_LOG_SESSIONIZATION_JOB_KIND = "players:sessionize-log-events"
PLAYER_LOG_SESSIONIZATION_ACTION = "players.log-events.sessionize"
PLAYER_LOG_SESSIONIZATION_SCOPE = "stored_player_log_events"
PLAYER_SESSION_MAINTENANCE_JOB_KIND = "players:session-maintenance"
PLAYER_SESSION_MAINTENANCE_ACTION = "players.sessions.maintenance"
PLAYER_SESSION_MAINTENANCE_SCOPE = "player_sessions"
PLAYER_LIVE_SESSION_SCAN_JOB_KIND = "players:scan-live-sessions"
PLAYER_LIVE_SESSION_SCAN_ACTION = "players.sessions.scan-live"
PLAYER_LIVE_SESSION_SCAN_SCOPE = "live_current_roster"
DEFAULT_PLAYER_SESSION_STALE_TIMEOUT = timedelta(hours=24)
DEFAULT_CLOSED_PLAYER_SESSION_RETENTION = timedelta(days=90)


class PlayerLogSessionizationAuditError(RuntimeError):
    """Raised when sessionization completes but audit cannot be written."""


class PlayerSessionMaintenanceAuditError(RuntimeError):
    """Raised when player session maintenance completes but audit cannot be written."""


class PlayerLiveSessionScanAuditError(RuntimeError):
    """Raised when live session scan completes but audit cannot be written."""


@dataclass(frozen=True)
class PlayerSessionMaintenanceSummary:
    """Counts-only summary of explicit player-session maintenance."""

    stale_close: player_registry.PlayerSessionStaleCloseResult
    retention_cleanup: player_registry.PlayerSessionRetentionCleanupResult


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


def ensure_player_log_sessionization_job(
    db_path,
    *,
    requested_by_username: str,
    requested_by_user_id: int | None = None,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> tuple[JobRecord, bool]:
    """Return an active stored-log sessionization job, creating one if needed."""
    return get_or_create_active_job(
        db_path,
        kind=PLAYER_LOG_SESSIONIZATION_JOB_KIND,
        requested_by_username=requested_by_username,
        requested_by_user_id=requested_by_user_id,
        instance=_safe_instance(instance),
        current_step="Queued player log sessionization",
    )


def ensure_player_session_maintenance_job(
    db_path,
    *,
    requested_by_username: str,
    requested_by_user_id: int | None = None,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> tuple[JobRecord, bool]:
    """Return an active player-session maintenance job, creating one if needed."""
    return get_or_create_active_job(
        db_path,
        kind=PLAYER_SESSION_MAINTENANCE_JOB_KIND,
        requested_by_username=requested_by_username,
        requested_by_user_id=requested_by_user_id,
        instance=_safe_instance(instance),
        current_step="Queued player session maintenance",
    )


def ensure_player_live_session_scan_job(
    db_path,
    *,
    requested_by_username: str,
    requested_by_user_id: int | None = None,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> tuple[JobRecord, bool]:
    """Return an active live session scan job, creating one if needed."""
    return get_or_create_active_job(
        db_path,
        kind=PLAYER_LIVE_SESSION_SCAN_JOB_KIND,
        requested_by_username=requested_by_username,
        requested_by_user_id=requested_by_user_id,
        instance=_safe_instance(instance),
        current_step="Queued live player session scan",
    )


def _count_details(
    summary: player_sessionizer.PlayerLogSessionizationSummary | None,
    *,
    phase: str,
    job_id: int | None,
    reason_class: str = "",
    reason_message: str = "",
) -> dict[str, object]:
    details: dict[str, object] = {
        "phase": phase,
        "job_kind": PLAYER_LOG_SESSIONIZATION_JOB_KIND,
        "job_id": str(job_id or ""),
        "scope": PLAYER_LOG_SESSIONIZATION_SCOPE,
        "events_scanned": "0",
        "events_ignored": "0",
        "observations_considered": "0",
        "observations_applied": "0",
        "observations_skipped": "0",
        "observations_ignored": "0",
        "sessions_created": "0",
        "sessions_updated": "0",
        "sessions_closed": "0",
    }
    if summary is not None:
        details.update(
            {
                "events_scanned": str(summary.events_scanned),
                "events_ignored": str(summary.events_ignored),
                "observations_considered": str(summary.observations_considered),
                "observations_applied": str(summary.observations_applied),
                "observations_skipped": str(summary.observations_skipped),
                "observations_ignored": str(summary.observations_ignored),
                "sessions_created": str(summary.sessions_created),
                "sessions_updated": str(summary.sessions_updated),
                "sessions_closed": str(summary.sessions_closed),
            }
        )
    if reason_class:
        details["reason_class"] = safe_player_text(reason_class, max_length=120)
    if reason_message:
        details["reason_message"] = safe_player_text(reason_message, max_length=240)
    return details


def _summary_message(
    summary: player_sessionizer.PlayerLogSessionizationSummary,
) -> str:
    if summary.events_scanned == 0:
        return "No stored player log events found."
    if summary.observations_applied == 0:
        return "No new player session observations applied."
    return "Player log sessionization completed."


def _summary_output(summary: player_sessionizer.PlayerLogSessionizationSummary) -> str:
    return (
        "Player log sessionization counts: "
        f"events_scanned={summary.events_scanned}; "
        f"events_ignored={summary.events_ignored}; "
        f"observations_considered={summary.observations_considered}; "
        f"observations_applied={summary.observations_applied}; "
        f"observations_skipped={summary.observations_skipped}; "
        f"observations_ignored={summary.observations_ignored}; "
        f"sessions_created={summary.sessions_created}; "
        f"sessions_updated={summary.sessions_updated}; "
        f"sessions_closed={summary.sessions_closed}"
    )


def _maintenance_count_details(
    summary: PlayerSessionMaintenanceSummary | None,
    *,
    phase: str,
    job_id: int | None,
    reason_class: str = "",
    reason_message: str = "",
) -> dict[str, object]:
    details: dict[str, object] = {
        "phase": phase,
        "job_kind": PLAYER_SESSION_MAINTENANCE_JOB_KIND,
        "job_id": str(job_id or ""),
        "scope": PLAYER_SESSION_MAINTENANCE_SCOPE,
        "open_sessions_scanned": "0",
        "stale_sessions_overdue": "0",
        "stale_sessions_closed": "0",
        "stale_sessions_not_closed": "0",
        "retention_sessions_scanned": "0",
        "retention_sessions_deleted": "0",
        "retention_sessions_not_deleted": "0",
    }
    if summary is not None:
        details.update(
            {
                "open_sessions_scanned": str(summary.stale_close.open_sessions_scanned),
                "stale_sessions_overdue": str(summary.stale_close.sessions_overdue),
                "stale_sessions_closed": str(summary.stale_close.sessions_closed),
                "stale_sessions_not_closed": str(summary.stale_close.sessions_skipped),
                "retention_sessions_scanned": str(
                    summary.retention_cleanup.sessions_scanned
                ),
                "retention_sessions_deleted": str(
                    summary.retention_cleanup.sessions_deleted
                ),
                "retention_sessions_not_deleted": str(
                    summary.retention_cleanup.sessions_skipped
                ),
            }
        )
    if reason_class:
        details["reason_class"] = safe_player_text(reason_class, max_length=120)
    if reason_message:
        details["reason_message"] = safe_player_text(reason_message, max_length=240)
    return details


def _maintenance_summary_message(summary: PlayerSessionMaintenanceSummary) -> str:
    changed = (
        summary.stale_close.sessions_closed
        + summary.retention_cleanup.sessions_deleted
    )
    if changed == 0:
        return "No player session maintenance changes applied."
    return "Player session maintenance completed."


def _maintenance_summary_output(summary: PlayerSessionMaintenanceSummary) -> str:
    return (
        "Player session maintenance counts: "
        f"open_sessions_scanned={summary.stale_close.open_sessions_scanned}; "
        f"stale_sessions_overdue={summary.stale_close.sessions_overdue}; "
        f"stale_sessions_closed={summary.stale_close.sessions_closed}; "
        f"stale_sessions_not_closed={summary.stale_close.sessions_skipped}; "
        f"retention_sessions_scanned="
        f"{summary.retention_cleanup.sessions_scanned}; "
        f"retention_sessions_deleted="
        f"{summary.retention_cleanup.sessions_deleted}; "
        f"retention_sessions_not_deleted="
        f"{summary.retention_cleanup.sessions_skipped}"
    )


def live_session_scan_count_details(
    summary: player_live_session_scanner.LivePlayerSessionScanSummary | None,
    *,
    phase: str,
    job_id: int | None,
) -> dict[str, object]:
    """Return counts-only audit details for explicit live session scans."""
    details: dict[str, object] = {
        "phase": phase,
        "job_kind": PLAYER_LIVE_SESSION_SCAN_JOB_KIND,
        "job_id": str(job_id or ""),
        "scope": PLAYER_LIVE_SESSION_SCAN_SCOPE,
        "observed_count": "0",
        "roster_rows_seen": "0",
        "reliable_rows_seen": "0",
        "unreliable_rows_ignored": "0",
        "duplicate_rows_ignored": "0",
        "scans_considered": "0",
        "observations_considered": "0",
        "observations_applied": "0",
        "observations_not_applied": "0",
        "observations_ignored": "0",
        "absent_sessions_considered": "0",
        "absent_sessions_confirmed": "0",
        "sessions_created": "0",
        "sessions_updated": "0",
        "sessions_closed": "0",
        "sessions_not_closed": "0",
        "source_failures": "0",
        "roster_unavailable": "0",
    }
    if summary is not None:
        details.update(
            {
                "observed_count": str(summary.observed_count),
                "roster_rows_seen": str(summary.roster_rows_seen),
                "reliable_rows_seen": str(summary.reliable_rows_seen),
                "unreliable_rows_ignored": str(summary.unreliable_rows_ignored),
                "duplicate_rows_ignored": str(summary.duplicate_rows_ignored),
                "scans_considered": str(summary.scans_considered),
                "observations_considered": str(summary.observations_considered),
                "observations_applied": str(summary.observations_applied),
                "observations_not_applied": str(summary.observations_skipped),
                "observations_ignored": str(summary.observations_ignored),
                "absent_sessions_considered": str(summary.absent_sessions_considered),
                "absent_sessions_confirmed": str(summary.absent_sessions_confirmed),
                "sessions_created": str(summary.sessions_created),
                "sessions_updated": str(summary.sessions_updated),
                "sessions_closed": str(summary.sessions_closed),
                "sessions_not_closed": str(summary.sessions_skipped),
                "source_failures": str(summary.source_failures),
                "roster_unavailable": str(summary.roster_unavailable),
            }
        )
    return details


def _live_scan_summary_message(
    summary: player_live_session_scanner.LivePlayerSessionScanSummary,
) -> str:
    if not summary.success:
        return "Live player session scan failed."
    if summary.observations_applied == 0 and summary.sessions_closed == 0:
        return "No reliable live player session observations found."
    return "Live player session scan completed."


def _live_scan_summary_output(
    summary: player_live_session_scanner.LivePlayerSessionScanSummary,
) -> str:
    return (
        "Live player session scan counts: "
        f"observed_count={summary.observed_count}; "
        f"roster_rows_seen={summary.roster_rows_seen}; "
        f"reliable_rows_seen={summary.reliable_rows_seen}; "
        f"unreliable_rows_ignored={summary.unreliable_rows_ignored}; "
        f"duplicate_rows_ignored={summary.duplicate_rows_ignored}; "
        f"scans_considered={summary.scans_considered}; "
        f"observations_considered={summary.observations_considered}; "
        f"observations_applied={summary.observations_applied}; "
        f"observations_not_applied={summary.observations_skipped}; "
        f"observations_ignored={summary.observations_ignored}; "
        f"absent_sessions_considered={summary.absent_sessions_considered}; "
        f"absent_sessions_confirmed={summary.absent_sessions_confirmed}; "
        f"sessions_created={summary.sessions_created}; "
        f"sessions_updated={summary.sessions_updated}; "
        f"sessions_closed={summary.sessions_closed}; "
        f"sessions_not_closed={summary.sessions_skipped}; "
        f"source_failures={summary.source_failures}; "
        f"roster_unavailable={summary.roster_unavailable}"
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _run_player_session_maintenance(
    registry_db_path: Path,
    *,
    now: datetime,
) -> PlayerSessionMaintenanceSummary:
    close_observed_at = now.isoformat()
    stale_cutoff = (now - DEFAULT_PLAYER_SESSION_STALE_TIMEOUT).isoformat()
    retention_cutoff = (now - DEFAULT_CLOSED_PLAYER_SESSION_RETENTION).isoformat()
    stale_close = player_registry.close_stale_open_player_sessions(
        registry_db_path,
        last_seen_before=stale_cutoff,
        close_observed_at=close_observed_at,
        source=player_registry.PLAYER_SESSION_SOURCE_SCANNER_CHECKPOINT,
        source_ref="session-maintenance:stale-timeout",
        confidence=player_registry.PLAYER_SESSION_CONFIDENCE_LOW,
    )
    retention_cleanup = player_registry.cleanup_player_sessions_by_retention(
        registry_db_path,
        closed_before=retention_cutoff,
    )
    return PlayerSessionMaintenanceSummary(
        stale_close=stale_close,
        retention_cleanup=retention_cleanup,
    )


def _append_sessionization_outcome_audit(
    audit_log_path: Path,
    *,
    username: str,
    instance: str,
    job_id: int,
    summary: player_sessionizer.PlayerLogSessionizationSummary | None,
    success: bool,
    message: str,
    reason_class: str = "",
    reason_message: str = "",
) -> None:
    append_audit_event(
        audit_log_path,
        username=username,
        action=PLAYER_LOG_SESSIONIZATION_ACTION,
        instance=instance,
        target=PLAYER_LOG_SESSIONIZATION_JOB_KIND,
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
        _append_sessionization_outcome_audit(
            audit_log_path,
            username=username,
            instance=instance,
            job_id=job_id,
            summary=None,
            success=False,
            message="Player log sessionization failed.",
            reason_class=type(error).__name__,
            reason_message="Player log sessionization failed.",
        )
    except AuditLogError as audit_error:
        raise PlayerLogSessionizationAuditError(
            "Player log sessionization failed, and audit logging also failed."
        ) from audit_error


def _append_maintenance_outcome_audit(
    audit_log_path: Path,
    *,
    username: str,
    instance: str,
    job_id: int,
    summary: PlayerSessionMaintenanceSummary | None,
    success: bool,
    message: str,
    reason_class: str = "",
    reason_message: str = "",
) -> None:
    append_audit_event(
        audit_log_path,
        username=username,
        action=PLAYER_SESSION_MAINTENANCE_ACTION,
        instance=instance,
        target=PLAYER_SESSION_MAINTENANCE_JOB_KIND,
        success=success,
        message=message,
        exit_code=0 if success else 1,
        details=_maintenance_count_details(
            summary,
            phase="outcome",
            job_id=job_id,
            reason_class=reason_class,
            reason_message=reason_message,
        ),
    )


def _maintenance_audit_failure_or_raise(
    audit_log_path: Path,
    *,
    username: str,
    instance: str,
    job_id: int,
    error: Exception,
) -> None:
    try:
        _append_maintenance_outcome_audit(
            audit_log_path,
            username=username,
            instance=instance,
            job_id=job_id,
            summary=None,
            success=False,
            message="Player session maintenance failed.",
            reason_class=type(error).__name__,
            reason_message="Player session maintenance failed.",
        )
    except AuditLogError as audit_error:
        raise PlayerSessionMaintenanceAuditError(
            "Player session maintenance failed, and audit logging also failed."
        ) from audit_error


def _append_live_scan_outcome_audit(
    audit_log_path: Path,
    *,
    username: str,
    instance: str,
    job_id: int,
    summary: player_live_session_scanner.LivePlayerSessionScanSummary | None,
    success: bool,
    message: str,
) -> None:
    append_audit_event(
        audit_log_path,
        username=username,
        action=PLAYER_LIVE_SESSION_SCAN_ACTION,
        instance=instance,
        target=PLAYER_LIVE_SESSION_SCAN_JOB_KIND,
        success=success,
        message=message,
        exit_code=0 if success else 1,
        details=live_session_scan_count_details(
            summary,
            phase="outcome",
            job_id=job_id,
        ),
    )


def _live_scan_audit_failure_or_raise(
    audit_log_path: Path,
    *,
    username: str,
    instance: str,
    job_id: int,
) -> None:
    try:
        _append_live_scan_outcome_audit(
            audit_log_path,
            username=username,
            instance=instance,
            job_id=job_id,
            summary=None,
            success=False,
            message="Live player session scan failed.",
        )
    except AuditLogError as audit_error:
        raise PlayerLiveSessionScanAuditError(
            "Live player session scan failed, and audit logging also failed."
        ) from audit_error


def handle_player_log_sessionization(context: JobContext) -> JobHandlerResult:
    """Sessionize stored player log events for one queued job."""
    instance = _safe_instance(context.job.instance)
    data_root = _data_root_from_web_db_path(context.db_path)
    audit_log_path = paths.web_audit_log_file(data_root)
    registry_db_path = player_registry.player_registry_db_path(
        instance,
        data_root=data_root,
    )
    context.append_output(
        stdout=(
            "Starting player log sessionization: "
            f"scope={PLAYER_LOG_SESSIONIZATION_SCOPE}"
        )
    )

    try:
        summary = player_sessionizer.sessionize_stored_player_log_events(registry_db_path)
    except Exception as error:
        _audit_failure_or_raise(
            audit_log_path,
            username=context.job.requested_by_username,
            instance=instance,
            job_id=context.job.id,
            error=error,
        )
        raise RuntimeError("Player log sessionization failed.") from error

    context.append_output(stdout=_summary_output(summary))
    message = _summary_message(summary)
    try:
        _append_sessionization_outcome_audit(
            audit_log_path,
            username=context.job.requested_by_username,
            instance=instance,
            job_id=context.job.id,
            summary=summary,
            success=True,
            message=message,
        )
    except AuditLogError as error:
        raise PlayerLogSessionizationAuditError(
            "Player log sessionization completed, but audit logging failed."
        ) from error

    return JobHandlerResult(
        result_message=message,
        current_step="Sessionization complete",
        progress_current=summary.events_scanned,
        progress_total=summary.events_scanned,
    )


def create_player_log_sessionization_dispatcher() -> JobDispatcher:
    """Return the explicit dispatcher for stored-log sessionization jobs."""
    return JobDispatcher(
        {PLAYER_LOG_SESSIONIZATION_JOB_KIND: handle_player_log_sessionization}
    )


def dispatch_player_log_sessionization_job(db_path, job_id: int):
    """Dispatch one queued stored-log sessionization job through the safe handler."""
    return dispatch_job(db_path, job_id, create_player_log_sessionization_dispatcher())


def _run_player_log_sessionization_worker(db_path: Path, job_id: int) -> None:
    try:
        dispatch_player_log_sessionization_job(db_path, job_id)
    except Exception:
        return


def start_player_log_sessionization_worker(db_path, job_id: int) -> threading.Thread:
    """Start one queued stored-log sessionization job in a background thread."""
    thread = threading.Thread(
        target=_run_player_log_sessionization_worker,
        args=(Path(db_path), job_id),
        name=f"armactl-web-player-sessionization-job-{job_id}",
        daemon=True,
    )
    thread.start()
    return thread


def handle_player_session_maintenance(context: JobContext) -> JobHandlerResult:
    """Run explicit stale-close and retention cleanup for one queued job."""
    instance = _safe_instance(context.job.instance)
    data_root = _data_root_from_web_db_path(context.db_path)
    audit_log_path = paths.web_audit_log_file(data_root)
    registry_db_path = player_registry.player_registry_db_path(
        instance,
        data_root=data_root,
    )
    context.append_output(
        stdout=(
            "Starting player session maintenance: "
            f"scope={PLAYER_SESSION_MAINTENANCE_SCOPE}"
        )
    )

    try:
        summary = _run_player_session_maintenance(registry_db_path, now=_utc_now())
    except Exception as error:
        _maintenance_audit_failure_or_raise(
            audit_log_path,
            username=context.job.requested_by_username,
            instance=instance,
            job_id=context.job.id,
            error=error,
        )
        raise RuntimeError("Player session maintenance failed.") from error

    context.append_output(stdout=_maintenance_summary_output(summary))
    message = _maintenance_summary_message(summary)
    try:
        _append_maintenance_outcome_audit(
            audit_log_path,
            username=context.job.requested_by_username,
            instance=instance,
            job_id=context.job.id,
            summary=summary,
            success=True,
            message=message,
        )
    except AuditLogError as error:
        raise PlayerSessionMaintenanceAuditError(
            "Player session maintenance completed, but audit logging failed."
        ) from error

    progress_count = (
        summary.stale_close.open_sessions_scanned
        + summary.retention_cleanup.sessions_scanned
    )
    return JobHandlerResult(
        result_message=message,
        current_step="Player session maintenance complete",
        progress_current=progress_count,
        progress_total=progress_count,
    )


def create_player_session_maintenance_dispatcher() -> JobDispatcher:
    """Return the explicit dispatcher for player-session maintenance jobs."""
    return JobDispatcher(
        {PLAYER_SESSION_MAINTENANCE_JOB_KIND: handle_player_session_maintenance}
    )


def dispatch_player_session_maintenance_job(db_path, job_id: int):
    """Dispatch one queued player-session maintenance job through the safe handler."""
    return dispatch_job(db_path, job_id, create_player_session_maintenance_dispatcher())


def _run_player_session_maintenance_worker(db_path: Path, job_id: int) -> None:
    try:
        dispatch_player_session_maintenance_job(db_path, job_id)
    except Exception:
        return


def start_player_session_maintenance_worker(db_path, job_id: int) -> threading.Thread:
    """Start one queued player-session maintenance job in a background thread."""
    thread = threading.Thread(
        target=_run_player_session_maintenance_worker,
        args=(Path(db_path), job_id),
        name=f"armactl-web-player-session-maintenance-job-{job_id}",
        daemon=True,
    )
    thread.start()
    return thread


def handle_player_live_session_scan(context: JobContext) -> JobHandlerResult:
    """Scan one live current roster snapshot into session observations."""
    instance = _safe_instance(context.job.instance)
    data_root = _data_root_from_web_db_path(context.db_path)
    audit_log_path = paths.web_audit_log_file(data_root)

    try:
        summary = player_live_session_scanner.scan_live_player_sessions_once(
            instance,
            data_root=data_root,
        )
    except Exception as error:
        _live_scan_audit_failure_or_raise(
            audit_log_path,
            username=context.job.requested_by_username,
            instance=instance,
            job_id=context.job.id,
        )
        raise RuntimeError("Live player session scan failed.") from error

    context.append_output(stdout=_live_scan_summary_output(summary))
    message = _live_scan_summary_message(summary)
    try:
        _append_live_scan_outcome_audit(
            audit_log_path,
            username=context.job.requested_by_username,
            instance=instance,
            job_id=context.job.id,
            summary=summary,
            success=summary.success,
            message=message,
        )
    except AuditLogError as error:
        raise PlayerLiveSessionScanAuditError(
            "Live player session scan completed, but audit logging failed."
        ) from error

    if not summary.success:
        raise RuntimeError(message)

    return JobHandlerResult(
        result_message=message,
        current_step="Live player session scan complete",
        progress_current=summary.observations_considered,
        progress_total=summary.observations_considered,
    )


def create_player_live_session_scan_dispatcher() -> JobDispatcher:
    """Return the explicit dispatcher for live session scan jobs."""
    return JobDispatcher(
        {PLAYER_LIVE_SESSION_SCAN_JOB_KIND: handle_player_live_session_scan}
    )


def dispatch_player_live_session_scan_job(db_path, job_id: int):
    """Dispatch one queued live session scan job through the safe handler."""
    return dispatch_job(db_path, job_id, create_player_live_session_scan_dispatcher())


def _run_player_live_session_scan_worker(db_path: Path, job_id: int) -> None:
    try:
        dispatch_player_live_session_scan_job(db_path, job_id)
    except Exception:
        return


def start_player_live_session_scan_worker(db_path, job_id: int) -> threading.Thread:
    """Start one queued live session scan job in a background thread."""
    thread = threading.Thread(
        target=_run_player_live_session_scan_worker,
        args=(Path(db_path), job_id),
        name=f"armactl-web-player-live-session-scan-job-{job_id}",
        daemon=True,
    )
    thread.start()
    return thread
