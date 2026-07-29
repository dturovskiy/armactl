"""Synchronous supervised player-session pipeline orchestrator."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Final
from urllib.parse import quote

from armactl import paths
from armactl.web.services import (
    player_log_ingest,
    player_registry,
    player_session_mutation,
    player_sessionizer,
)

_STATE_TABLE: Final = "web_player_session_scheduler_state"
DEFAULT_MAINTENANCE_INTERVAL: Final = timedelta(hours=1)
MAX_STORED_FAILURE_COUNT: Final = 30
SESSIONIZATION_ORDER_VERSION: Final = 1

_DIAGNOSTIC_CODE_RE: Final = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


def _safe_diagnostic_code(value: object, *, fallback: str = "unknown") -> str:
    text = str(value or "").strip()
    return text if not text or _DIAGNOSTIC_CODE_RE.fullmatch(text) else fallback


class PlayerSessionSchedulerRunnerError(RuntimeError):
    """Raised when the supervised pipeline cannot be executed safely."""


@dataclass(frozen=True)
class PlayerSessionSchedulerState:
    """Read-only compatibility diagnostics from the legacy web scheduler table."""

    instance: str
    job_kind: str
    last_attempt_at: str
    last_success_at: str
    last_failure_at: str
    next_due_at: str
    failure_count: int
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance": self.instance,
            "job_kind": self.job_kind,
            "last_attempt_at": self.last_attempt_at,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "next_due_at": self.next_due_at,
            "failure_count": self.failure_count,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class PlayerSessionSchedulerRunResult:
    """Counts-only outcome of one synchronous ordered pipeline pass."""

    instance: str
    checked_at: str
    outcome: str
    failure_stage: str = ""
    error_code: str = ""
    generation_proven: bool = False
    session_pages_completed: int = 0
    session_events_scanned: int = 0
    session_observations_applied: int = 0
    session_sessions_created: int = 0
    session_sessions_updated: int = 0
    session_sessions_closed: int = 0
    live_reliable: bool = False
    live_observed_count: int = 0
    live_observations_applied: int = 0
    live_sessions_created: int = 0
    live_sessions_updated: int = 0
    live_sessions_closed: int = 0
    maintenance_due: bool = False
    maintenance_performed: bool = False
    stale_sessions_closed: int = 0
    retained_sessions_deleted: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "failure_stage", _safe_diagnostic_code(self.failure_stage)
        )
        object.__setattr__(self, "error_code", _safe_diagnostic_code(self.error_code))

    @property
    def success(self) -> bool:
        return self.outcome in {
            "backlog_remaining",
            "completed",
            "no_new_generation",
        }

    @property
    def exit_code(self) -> int:
        return 0 if self.success else 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance": self.instance,
            "checked_at": self.checked_at,
            "outcome": self.outcome,
            "failure_stage": self.failure_stage,
            "error_code": self.error_code,
            "success": self.success,
            "generation_proven": self.generation_proven,
            "session_pages_completed": self.session_pages_completed,
            "session_events_scanned": self.session_events_scanned,
            "session_observations_applied": self.session_observations_applied,
            "session_sessions_created": self.session_sessions_created,
            "session_sessions_updated": self.session_sessions_updated,
            "session_sessions_closed": self.session_sessions_closed,
            "live_reliable": self.live_reliable,
            "live_observed_count": self.live_observed_count,
            "live_observations_applied": self.live_observations_applied,
            "live_sessions_created": self.live_sessions_created,
            "live_sessions_updated": self.live_sessions_updated,
            "live_sessions_closed": self.live_sessions_closed,
            "maintenance_due": self.maintenance_due,
            "maintenance_performed": self.maintenance_performed,
            "stale_sessions_closed": self.stale_sessions_closed,
            "retained_sessions_deleted": self.retained_sessions_deleted,
        }


@dataclass(frozen=True)
class PlayerSessionSchedulerStatus:
    """Bounded read-only pipeline and compatibility status."""

    instance: str
    checked_at: str
    state: str
    reason: str
    freshness_status: str = player_registry.PLAYER_LOG_INGEST_STATUS_UNAVAILABLE
    completed_generation: int = 0
    generation_max_event_id: int = 0
    last_attempt_at: str = ""
    last_started_at: str = ""
    last_completed_at: str = ""
    last_success_at: str = ""
    last_failure_at: str = ""
    last_processed_ingest_generation: int = 0
    last_consumed_ingest_success_at: str = ""
    last_consumed_ingest_generation_max_event_id: int = 0
    current_ingest_generation: int = 0
    current_ingest_success_at: str = ""
    current_generation_max_event_id: int = 0
    last_sessionized_event_id: int = 0
    last_sessionized_event_time: str = ""
    sessionization_order_version: int = 0
    last_live_scan_at: str = ""
    last_maintenance_at: str = ""
    next_maintenance_due_at: str = ""
    consecutive_failures: int = 0
    last_failure_stage: str = ""
    last_result: str = ""
    last_error_code: str = ""
    interrupted: bool = False
    lock_state: str = "unknown"
    legacy_state_row_count: int = 0
    automatic_scheduler_enabled: bool = False
    service_timer_daemon_enabled: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "state",
            "reason",
            "freshness_status",
            "last_failure_stage",
            "last_result",
            "last_error_code",
            "lock_state",
        ):
            object.__setattr__(
                self,
                field_name,
                _safe_diagnostic_code(getattr(self, field_name)),
            )

    @property
    def state_row_count(self) -> int:
        return int(bool(self.last_result or self.last_success_at or self.consecutive_failures))

    @property
    def jobs(self) -> tuple[object, ...]:
        return ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance": self.instance,
            "checked_at": self.checked_at,
            "state": self.state,
            "reason": self.reason,
            "freshness_status": self.freshness_status,
            "completed_generation": self.completed_generation,
            "generation_max_event_id": self.generation_max_event_id,
            "last_attempt_at": self.last_attempt_at,
            "last_started_at": self.last_started_at,
            "last_completed_at": self.last_completed_at,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "last_processed_ingest_generation": self.last_processed_ingest_generation,
            "last_consumed_ingest_success_at": self.last_consumed_ingest_success_at,
            "last_consumed_ingest_generation_max_event_id": (
                self.last_consumed_ingest_generation_max_event_id
            ),
            "current_ingest_generation": self.current_ingest_generation,
            "current_ingest_success_at": self.current_ingest_success_at,
            "current_generation_max_event_id": self.current_generation_max_event_id,
            "last_sessionized_event_id": self.last_sessionized_event_id,
            "last_sessionized_event_time": self.last_sessionized_event_time,
            "sessionization_order_version": self.sessionization_order_version,
            "last_live_scan_at": self.last_live_scan_at,
            "last_maintenance_at": self.last_maintenance_at,
            "next_maintenance_due_at": self.next_maintenance_due_at,
            "consecutive_failures": self.consecutive_failures,
            "last_failure_stage": self.last_failure_stage,
            "last_result": self.last_result,
            "last_error_code": self.last_error_code,
            "interrupted": self.interrupted,
            "lock_state": self.lock_state,
            "legacy_state_row_count": self.legacy_state_row_count,
            "automatic_scheduler_enabled": self.automatic_scheduler_enabled,
            "service_timer_daemon_enabled": self.service_timer_daemon_enabled,
            "jobs": [],
        }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _datetime_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_instance(instance: object) -> str:
    try:
        return paths.validate_instance_name(str(instance or paths.DEFAULT_INSTANCE_NAME))
    except paths.InvalidInstanceNameError as error:
        raise PlayerSessionSchedulerRunnerError("Instance name is invalid.") from error


def _data_root_from_legacy_db_path(db_path: Path | None) -> Path:
    if db_path is not None:
        candidate = Path(db_path)
        if candidate.name == "web.db" and candidate.parent.name == "web":
            return candidate.parent.parent
    return paths.DEFAULT_DATA_ROOT


def _readonly_db_uri(db_path: Path) -> str:
    return f"file:{quote(str(db_path), safe='/')}?mode=ro"


def _state_from_row(row: sqlite3.Row) -> PlayerSessionSchedulerState:
    return PlayerSessionSchedulerState(
        instance=str(row["instance"] or ""),
        job_kind=str(row["job_kind"] or ""),
        last_attempt_at=str(row["last_attempt_at"] or ""),
        last_success_at=str(row["last_success_at"] or ""),
        last_failure_at=str(row["last_failure_at"] or ""),
        next_due_at=str(row["next_due_at"] or ""),
        failure_count=max(0, int(row["failure_count"] or 0)),
        updated_at=str(row["updated_at"] or ""),
    )


def list_player_session_scheduler_state(
    db_path: Path,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> tuple[PlayerSessionSchedulerState, ...]:
    """Read legacy enqueue diagnostics without creating or migrating web.db."""
    normalized = _safe_instance(instance)
    try:
        if not Path(db_path).is_file():
            return ()
        connection = sqlite3.connect(_readonly_db_uri(Path(db_path)), uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (_STATE_TABLE,),
        ).fetchone()
        if table is None:
            return ()
        rows = connection.execute(
            f"""
            SELECT instance, job_kind, last_attempt_at, last_success_at,
                   last_failure_at, next_due_at, failure_count, updated_at
            FROM {_STATE_TABLE}
            WHERE instance = ?
            ORDER BY job_kind ASC
            """,
            (normalized,),
        ).fetchall()
        return tuple(_state_from_row(row) for row in rows)
    except (OSError, sqlite3.Error, ValueError):
        return ()
    finally:
        try:
            connection.close()
        except (NameError, sqlite3.Error):
            pass


def get_player_session_scheduler_state(
    db_path: Path,
    *,
    job_kind: str,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> PlayerSessionSchedulerState | None:
    for state in list_player_session_scheduler_state(db_path, instance=instance):
        if state.job_kind == job_kind:
            return state
    return None


def _failure_state(
    state: player_registry.PlayerSessionPipelineState,
    *,
    stage: str,
    error_code: str,
    now_text: str,
) -> player_registry.PlayerSessionPipelineState:
    return replace(
        state,
        last_completed_at=now_text,
        last_failure_at=now_text,
        consecutive_failures=min(
            MAX_STORED_FAILURE_COUNT,
            state.consecutive_failures + 1,
        ),
        last_failure_stage=stage,
        last_result="failed",
        last_error_code=error_code,
        interrupted=False,
        updated_at=now_text,
    )


def _persist_failure(
    db_path: Path,
    state: player_registry.PlayerSessionPipelineState,
    *,
    stage: str,
    error_code: str,
    now_text: str,
) -> None:
    try:
        player_registry.upsert_player_session_pipeline_state(
            db_path,
            _failure_state(
                state,
                stage=stage,
                error_code=error_code,
                now_text=now_text,
            ),
        )
    except (OSError, sqlite3.Error, ValueError):
        return


def _failed_result(
    *,
    instance: str,
    checked_at: str,
    stage: str,
    error_code: str,
    generation_proven: bool = False,
    session_summary: player_sessionizer.PlayerLogSessionizationSummary | None = None,
    live_summary: Any | None = None,
    maintenance_due: bool = False,
) -> PlayerSessionSchedulerRunResult:
    session = session_summary or player_sessionizer.PlayerLogSessionizationSummary()
    return PlayerSessionSchedulerRunResult(
        instance=instance,
        checked_at=checked_at,
        outcome="failed",
        failure_stage=stage,
        error_code=error_code,
        generation_proven=generation_proven,
        session_pages_completed=session.pages_completed,
        session_events_scanned=session.events_scanned,
        session_observations_applied=session.observations_applied,
        session_sessions_created=session.sessions_created,
        session_sessions_updated=session.sessions_updated,
        session_sessions_closed=session.sessions_closed,
        live_reliable=bool(getattr(live_summary, "reliable_evidence", False)),
        live_observed_count=max(0, int(getattr(live_summary, "observed_count", 0))),
        live_observations_applied=max(
            0,
            int(getattr(live_summary, "observations_applied", 0)),
        ),
        live_sessions_created=max(0, int(getattr(live_summary, "sessions_created", 0))),
        live_sessions_updated=max(0, int(getattr(live_summary, "sessions_updated", 0))),
        live_sessions_closed=max(0, int(getattr(live_summary, "sessions_closed", 0))),
        maintenance_due=maintenance_due,
    )


def _maintenance_is_due(
    state: player_registry.PlayerSessionPipelineState,
    *,
    now: datetime,
) -> bool:
    next_due = _parse_datetime(state.next_maintenance_due_at)
    if next_due is not None:
        return next_due <= now
    last = _parse_datetime(state.last_maintenance_at)
    return last is None or last + DEFAULT_MAINTENANCE_INTERVAL <= now


def _fresh_generation_is_proven(
    db_path: Path,
    freshness: player_registry.PlayerLogIngestFreshness,
) -> bool:
    if freshness.status != player_registry.PLAYER_LOG_INGEST_STATUS_FRESH:
        return False
    if not freshness.last_success_at or not freshness.coverage_started_at:
        return False
    checkpoints = player_registry.list_player_log_ingest_checkpoints(
        db_path,
        scope=player_log_ingest.PLAYER_LOG_INGEST_SCOPE,
    )
    return any(
        checkpoint.last_scanned_at and checkpoint.coverage_started_at
        for checkpoint in checkpoints
    )


def run_player_session_scheduler_once(
    db_path: Path | None = None,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    data_root: Path | None = None,
    now: datetime | None = None,
    page_limit: int = player_registry.DEFAULT_PLAYER_SESSIONIZATION_EVENT_PAGE_LIMIT,
    max_pages: int = player_sessionizer.DEFAULT_SESSIONIZATION_MAX_PAGES,
) -> PlayerSessionSchedulerRunResult:
    """Execute one ordered synchronous pipeline pass without web jobs or threads."""
    normalized = _safe_instance(instance)
    root = data_root or _data_root_from_legacy_db_path(db_path)
    checked_at = now.astimezone(timezone.utc) if now is not None else _utc_now()
    checked_at_text = _datetime_text(checked_at)
    registry_db_path = player_registry.player_registry_db_path(
        normalized,
        data_root=root,
    )
    try:
        if not registry_db_path.is_file():
            return _failed_result(
                instance=normalized,
                checked_at=checked_at_text,
                stage="ingest_generation",
                error_code="players_db_missing",
            )
    except OSError:
        return _failed_result(
            instance=normalized,
            checked_at=checked_at_text,
            stage="ingest_generation",
            error_code="players_db_unavailable",
        )

    try:
        with player_session_mutation.acquire_player_session_mutation_lock(
            normalized,
            data_root=root,
        ):
            try:
                player_registry.ensure_player_registry_db(registry_db_path)
                state = player_registry.get_player_session_pipeline_state(
                    registry_db_path,
                    instance=normalized,
                )
                state = player_registry.upsert_player_session_pipeline_state(
                    registry_db_path,
                    replace(
                        state,
                        last_attempt_at=checked_at_text,
                        last_result="evaluating",
                        last_failure_stage="",
                        last_error_code="",
                        updated_at=checked_at_text,
                    ),
                )
            except (OSError, sqlite3.Error, RuntimeError, ValueError):
                return _failed_result(
                    instance=normalized,
                    checked_at=checked_at_text,
                    stage="ingest_generation",
                    error_code="players_db_unavailable",
                )
            freshness = player_registry.get_player_log_ingest_freshness(
                registry_db_path,
                scope=player_log_ingest.PLAYER_LOG_INGEST_SCOPE,
            )
            if freshness.status != player_registry.PLAYER_LOG_INGEST_STATUS_FRESH:
                _persist_failure(
                    registry_db_path,
                    state,
                    stage="ingest_generation",
                    error_code="fresh_generation_unavailable",
                    now_text=checked_at_text,
                )
                return _failed_result(
                    instance=normalized,
                    checked_at=checked_at_text,
                    stage="ingest_generation",
                    error_code="fresh_generation_unavailable",
                )
            if freshness.completed_generation <= state.last_processed_ingest_generation:
                skipped_state = replace(
                    state,
                    last_completed_at=checked_at_text,
                    last_result="no_new_generation",
                    last_failure_stage="",
                    last_error_code="",
                    interrupted=False,
                    updated_at=checked_at_text,
                )
                player_registry.upsert_player_session_pipeline_state(
                    registry_db_path,
                    skipped_state,
                )
                return PlayerSessionSchedulerRunResult(
                    instance=normalized,
                    checked_at=checked_at_text,
                    outcome="no_new_generation",
                )
            if not _fresh_generation_is_proven(registry_db_path, freshness):
                _persist_failure(
                    registry_db_path,
                    state,
                    stage="ingest_generation",
                    error_code="fresh_generation_unproven",
                    now_text=checked_at_text,
                )
                return _failed_result(
                    instance=normalized,
                    checked_at=checked_at_text,
                    stage="ingest_generation",
                    error_code="fresh_generation_unproven",
                )
            if freshness.generation_max_event_id < state.last_sessionized_event_id:
                _persist_failure(
                    registry_db_path,
                    state,
                    stage="sessionize",
                    error_code="historical_backfill_required",
                    now_text=checked_at_text,
                )
                return _failed_result(
                    instance=normalized,
                    checked_at=checked_at_text,
                    stage="sessionize",
                    error_code="historical_backfill_required",
                    generation_proven=True,
                )

            generation_floor = state.last_consumed_ingest_generation_max_event_id
            resuming_generation = (
                state.current_ingest_generation
                > state.last_processed_ingest_generation
                and state.current_generation_max_event_id > generation_floor
            )
            if resuming_generation:
                target_generation = state.current_ingest_generation
                target_success_at = state.current_ingest_success_at
                target_max_event_id = state.current_generation_max_event_id
            else:
                target_generation = freshness.completed_generation
                target_success_at = freshness.last_success_at
                target_max_event_id = freshness.generation_max_event_id

            legacy_cursor_promoted = False
            if state.sessionization_order_version < SESSIONIZATION_ORDER_VERSION:
                if not player_registry.legacy_sessionization_cursor_matches_trusted_time_prefix(
                    registry_db_path,
                    event_id_floor=generation_floor,
                    after_event_time=state.last_sessionized_event_time,
                    after_event_id=state.last_sessionized_event_id,
                    through_event_id=target_max_event_id,
                ):
                    _persist_failure(
                        registry_db_path,
                        state,
                        stage="sessionize",
                        error_code="historical_backfill_required",
                        now_text=checked_at_text,
                    )
                    return _failed_result(
                        instance=normalized,
                        checked_at=checked_at_text,
                        stage="sessionize",
                        error_code="historical_backfill_required",
                        generation_proven=True,
                    )
                state = replace(
                    state,
                    sessionization_order_version=SESSIONIZATION_ORDER_VERSION,
                )
                legacy_cursor_promoted = True

            if (
                not resuming_generation
                and not legacy_cursor_promoted
                and not player_registry.legacy_sessionization_cursor_matches_trusted_time_prefix(
                    registry_db_path,
                    event_id_floor=generation_floor,
                    after_event_time=state.last_sessionized_event_time,
                    after_event_id=generation_floor,
                    through_event_id=target_max_event_id,
                )
            ):
                _persist_failure(
                    registry_db_path,
                    state,
                    stage="sessionize",
                    error_code="historical_backfill_required",
                    now_text=checked_at_text,
                )
                return _failed_result(
                    instance=normalized,
                    checked_at=checked_at_text,
                    stage="sessionize",
                    error_code="historical_backfill_required",
                    generation_proven=True,
                )

            working_state = replace(
                state,
                last_started_at=checked_at_text,
                current_ingest_generation=target_generation,
                current_ingest_success_at=target_success_at,
                current_generation_max_event_id=target_max_event_id,
                sessionization_order_version=SESSIONIZATION_ORDER_VERSION,
                last_result="sessionizing",
                last_failure_stage="",
                last_error_code="",
                interrupted=True,
                updated_at=checked_at_text,
            )
            player_registry.upsert_player_session_pipeline_state(
                registry_db_path,
                working_state,
            )

            def persist_page(
                summary: player_sessionizer.PlayerLogSessionizationSummary,
            ) -> None:
                nonlocal working_state
                working_state = replace(
                    working_state,
                    last_sessionized_event_id=summary.last_event_id,
                    last_sessionized_event_time=summary.last_event_time,
                    last_result="sessionizing",
                    updated_at=checked_at_text,
                )
                player_registry.upsert_player_session_pipeline_state(
                    registry_db_path,
                    working_state,
                )

            try:
                session_summary = player_session_mutation.run_player_log_sessionization(
                    normalized,
                    data_root=root,
                    event_id_floor=generation_floor,
                    after_event_time=working_state.last_sessionized_event_time,
                    after_event_id=working_state.last_sessionized_event_id,
                    through_event_id=target_max_event_id,
                    page_limit=page_limit,
                    max_pages=max_pages,
                    progress_callback=persist_page,
                    acquire_lock=False,
                )
            except player_sessionizer.HistoricalBackfillRequiredError:
                _persist_failure(
                    registry_db_path,
                    working_state,
                    stage="sessionize",
                    error_code="historical_backfill_required",
                    now_text=checked_at_text,
                )
                return _failed_result(
                    instance=normalized,
                    checked_at=checked_at_text,
                    stage="sessionize",
                    error_code="historical_backfill_required",
                    generation_proven=True,
                )
            except (OSError, sqlite3.Error, RuntimeError, ValueError):
                _persist_failure(
                    registry_db_path,
                    working_state,
                    stage="sessionize",
                    error_code="sessionize_failed",
                    now_text=checked_at_text,
                )
                return _failed_result(
                    instance=normalized,
                    checked_at=checked_at_text,
                    stage="sessionize",
                    error_code="sessionize_failed",
                    generation_proven=True,
                )
            if session_summary.backlog_remaining:
                backlog_state = replace(
                    working_state,
                    last_completed_at=checked_at_text,
                    last_sessionized_event_id=session_summary.last_event_id,
                    last_sessionized_event_time=session_summary.last_event_time,
                    consecutive_failures=0,
                    last_failure_stage="",
                    last_result="backlog_remaining",
                    last_error_code="",
                    interrupted=False,
                    updated_at=checked_at_text,
                )
                player_registry.upsert_player_session_pipeline_state(
                    registry_db_path,
                    backlog_state,
                )
                return PlayerSessionSchedulerRunResult(
                    instance=normalized,
                    checked_at=checked_at_text,
                    outcome="backlog_remaining",
                    generation_proven=True,
                    session_pages_completed=session_summary.pages_completed,
                    session_events_scanned=session_summary.events_scanned,
                    session_observations_applied=(
                        session_summary.observations_applied
                    ),
                    session_sessions_created=session_summary.sessions_created,
                    session_sessions_updated=session_summary.sessions_updated,
                    session_sessions_closed=session_summary.sessions_closed,
                )

            if target_generation < freshness.completed_generation:
                backlog_state = replace(
                    working_state,
                    last_completed_at=checked_at_text,
                    last_processed_ingest_generation=target_generation,
                    last_consumed_ingest_success_at=target_success_at,
                    last_consumed_ingest_generation_max_event_id=target_max_event_id,
                    last_sessionized_event_id=session_summary.last_event_id,
                    last_sessionized_event_time=session_summary.last_event_time,
                    consecutive_failures=0,
                    last_result="backlog_remaining",
                    last_failure_stage="",
                    last_error_code="",
                    interrupted=False,
                    updated_at=checked_at_text,
                )
                player_registry.upsert_player_session_pipeline_state(
                    registry_db_path,
                    backlog_state,
                )
                return PlayerSessionSchedulerRunResult(
                    instance=normalized,
                    checked_at=checked_at_text,
                    outcome="backlog_remaining",
                    generation_proven=True,
                    session_pages_completed=session_summary.pages_completed,
                    session_events_scanned=session_summary.events_scanned,
                    session_observations_applied=(
                        session_summary.observations_applied
                    ),
                    session_sessions_created=session_summary.sessions_created,
                    session_sessions_updated=session_summary.sessions_updated,
                    session_sessions_closed=session_summary.sessions_closed,
                )

            try:
                live_summary = player_session_mutation.run_live_player_session_scan(
                    normalized,
                    data_root=root,
                    observed_at=checked_at_text,
                    acquire_lock=False,
                )
            except (OSError, sqlite3.Error, RuntimeError, ValueError):
                _persist_failure(
                    registry_db_path,
                    working_state,
                    stage="live_scan",
                    error_code="live_scan_failed",
                    now_text=checked_at_text,
                )
                return _failed_result(
                    instance=normalized,
                    checked_at=checked_at_text,
                    stage="live_scan",
                    error_code="live_scan_failed",
                    generation_proven=True,
                    session_summary=session_summary,
                )
            if not live_summary.success or not live_summary.reliable_evidence:
                error_code = live_summary.reliability_error_code or "unreliable_roster"
                _persist_failure(
                    registry_db_path,
                    working_state,
                    stage="live_scan",
                    error_code=error_code,
                    now_text=checked_at_text,
                )
                return _failed_result(
                    instance=normalized,
                    checked_at=checked_at_text,
                    stage="live_scan",
                    error_code=error_code,
                    generation_proven=True,
                    session_summary=session_summary,
                    live_summary=live_summary,
                )
            working_state = replace(
                working_state,
                last_live_scan_at=checked_at_text,
                last_result="live_scan_completed",
                updated_at=checked_at_text,
            )
            player_registry.upsert_player_session_pipeline_state(
                registry_db_path,
                working_state,
            )

            maintenance_due = _maintenance_is_due(working_state, now=checked_at)
            maintenance_summary = None
            if maintenance_due:
                try:
                    maintenance_summary = (
                        player_session_mutation.run_player_session_maintenance(
                            normalized,
                            data_root=root,
                            now=checked_at,
                            acquire_lock=False,
                        )
                    )
                except (OSError, sqlite3.Error, RuntimeError, ValueError):
                    _persist_failure(
                        registry_db_path,
                        working_state,
                        stage="maintenance",
                        error_code="maintenance_failed",
                        now_text=checked_at_text,
                    )
                    return _failed_result(
                        instance=normalized,
                        checked_at=checked_at_text,
                        stage="maintenance",
                        error_code="maintenance_failed",
                        generation_proven=True,
                        session_summary=session_summary,
                        live_summary=live_summary,
                        maintenance_due=True,
                    )

            next_maintenance_due_at = working_state.next_maintenance_due_at
            if maintenance_due:
                next_maintenance_due_at = _datetime_text(
                    checked_at + DEFAULT_MAINTENANCE_INTERVAL
                )
            completed_state = replace(
                working_state,
                last_completed_at=checked_at_text,
                last_success_at=checked_at_text,
                last_processed_ingest_generation=target_generation,
                last_consumed_ingest_success_at=target_success_at,
                last_consumed_ingest_generation_max_event_id=(
                    target_max_event_id
                ),
                current_ingest_generation=target_generation,
                current_ingest_success_at=target_success_at,
                current_generation_max_event_id=target_max_event_id,
                last_sessionized_event_id=session_summary.last_event_id,
                last_sessionized_event_time=session_summary.last_event_time,
                sessionization_order_version=SESSIONIZATION_ORDER_VERSION,
                last_maintenance_at=(
                    checked_at_text if maintenance_due else working_state.last_maintenance_at
                ),
                next_maintenance_due_at=next_maintenance_due_at,
                consecutive_failures=0,
                last_failure_stage="",
                last_result="completed",
                last_error_code="",
                interrupted=False,
                updated_at=checked_at_text,
            )
            player_registry.upsert_player_session_pipeline_state(
                registry_db_path,
                completed_state,
            )
            return PlayerSessionSchedulerRunResult(
                instance=normalized,
                checked_at=checked_at_text,
                outcome="completed",
                generation_proven=True,
                session_pages_completed=session_summary.pages_completed,
                session_events_scanned=session_summary.events_scanned,
                session_observations_applied=session_summary.observations_applied,
                session_sessions_created=session_summary.sessions_created,
                session_sessions_updated=session_summary.sessions_updated,
                session_sessions_closed=session_summary.sessions_closed,
                live_reliable=True,
                live_observed_count=live_summary.observed_count,
                live_observations_applied=live_summary.observations_applied,
                live_sessions_created=live_summary.sessions_created,
                live_sessions_updated=live_summary.sessions_updated,
                live_sessions_closed=live_summary.sessions_closed,
                maintenance_due=maintenance_due,
                maintenance_performed=maintenance_summary is not None,
                stale_sessions_closed=(
                    maintenance_summary.stale_close.sessions_closed
                    if maintenance_summary is not None
                    else 0
                ),
                retained_sessions_deleted=(
                    maintenance_summary.retention_cleanup.sessions_deleted
                    if maintenance_summary is not None
                    else 0
                ),
            )
    except player_session_mutation.PlayerSessionMutationBusyError:
        return _failed_result(
            instance=normalized,
            checked_at=checked_at_text,
            stage="lock",
            error_code="db_contention",
        )
    except (OSError, sqlite3.Error, RuntimeError, ValueError):
        return _failed_result(
            instance=normalized,
            checked_at=checked_at_text,
            stage="pipeline",
            error_code="pipeline_failed",
        )


def read_player_session_scheduler_status(
    db_path: Path | None = None,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    data_root: Path | None = None,
    now: datetime | None = None,
) -> PlayerSessionSchedulerStatus:
    """Read players.db and legacy diagnostics without creating or migrating either."""
    normalized = _safe_instance(instance)
    root = data_root or _data_root_from_legacy_db_path(db_path)
    checked_at = now.astimezone(timezone.utc) if now is not None else _utc_now()
    registry_db_path = player_registry.player_registry_db_path(
        normalized,
        data_root=root,
    )
    legacy_count = (
        len(list_player_session_scheduler_state(db_path, instance=normalized))
        if db_path is not None
        else 0
    )
    lock_state = player_session_mutation.read_player_session_mutation_lock_state(
        normalized,
        data_root=root,
    )
    try:
        exists = registry_db_path.is_file()
    except OSError:
        exists = False
    if not exists:
        return PlayerSessionSchedulerStatus(
            instance=normalized,
            checked_at=_datetime_text(checked_at),
            state="empty",
            reason="players_db_missing",
            lock_state=lock_state,
            legacy_state_row_count=legacy_count,
        )
    freshness = player_registry.get_player_log_ingest_freshness(
        registry_db_path,
        scope=player_log_ingest.PLAYER_LOG_INGEST_SCOPE,
    )
    pipeline = player_registry.get_player_session_pipeline_state(
        registry_db_path,
        instance=normalized,
    )
    has_state = bool(pipeline.updated_at)
    reason = "pipeline_state_missing"
    state_name = "empty"
    if has_state:
        if pipeline.interrupted:
            state_name = "degraded"
            reason = "interrupted"
        elif pipeline.last_result == "failed":
            state_name = "failed"
            reason = pipeline.last_error_code or "pipeline_failed"
        elif lock_state == "busy":
            state_name = "busy"
            reason = "mutation_in_progress"
        elif freshness.status != player_registry.PLAYER_LOG_INGEST_STATUS_FRESH:
            state_name = "waiting_for_ingest"
            reason = freshness.status or "fresh_generation_unavailable"
        elif pipeline.last_result == "backlog_remaining":
            state_name = "catching_up"
            reason = "backlog_remaining"
        elif pipeline.last_result in {"completed", "no_new_generation"}:
            state_name = "healthy"
            reason = "ok"
        else:
            state_name = "available"
            reason = "ready"
    return PlayerSessionSchedulerStatus(
        instance=normalized,
        checked_at=_datetime_text(checked_at),
        state=state_name,
        reason=reason,
        freshness_status=freshness.status,
        completed_generation=freshness.completed_generation,
        generation_max_event_id=freshness.generation_max_event_id,
        last_attempt_at=pipeline.last_attempt_at,
        last_started_at=pipeline.last_started_at,
        last_completed_at=pipeline.last_completed_at,
        last_success_at=pipeline.last_success_at,
        last_failure_at=pipeline.last_failure_at,
        last_processed_ingest_generation=pipeline.last_processed_ingest_generation,
        last_consumed_ingest_success_at=pipeline.last_consumed_ingest_success_at,
        last_consumed_ingest_generation_max_event_id=(
            pipeline.last_consumed_ingest_generation_max_event_id
        ),
        current_ingest_generation=pipeline.current_ingest_generation,
        current_ingest_success_at=pipeline.current_ingest_success_at,
        current_generation_max_event_id=pipeline.current_generation_max_event_id,
        last_sessionized_event_id=pipeline.last_sessionized_event_id,
        last_sessionized_event_time=pipeline.last_sessionized_event_time,
        sessionization_order_version=pipeline.sessionization_order_version,
        last_live_scan_at=pipeline.last_live_scan_at,
        last_maintenance_at=pipeline.last_maintenance_at,
        next_maintenance_due_at=pipeline.next_maintenance_due_at,
        consecutive_failures=pipeline.consecutive_failures,
        last_failure_stage=pipeline.last_failure_stage,
        last_result=pipeline.last_result,
        last_error_code=pipeline.last_error_code,
        interrupted=pipeline.interrupted,
        lock_state=lock_state,
        legacy_state_row_count=legacy_count,
    )
