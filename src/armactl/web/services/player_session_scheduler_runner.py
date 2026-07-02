"""Explicit opt-in player-session scheduler runner foundation."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Final
from urllib.parse import quote

from armactl import paths
from armactl.web.jobs.models import JobRecord
from armactl.web.runtime import ensure_web_db
from armactl.web.services import (
    player_live_session_scan,
    player_log_sessionization,
    player_session_maintenance,
    player_session_scheduler_policy,
)

SCHEDULER_USERNAME: Final = "player-session-scheduler"
MAX_STORED_FAILURE_COUNT: Final = 30
_STATE_TABLE: Final = "web_player_session_scheduler_state"


class PlayerSessionSchedulerRunnerError(RuntimeError):
    """Raised when the explicit player-session scheduler cannot run safely."""


@dataclass(frozen=True)
class PlayerSessionSchedulerState:
    """Safe persisted state for one scheduler job kind."""

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
class PlayerSessionSchedulerStatusJob:
    """Read-only scheduler status for one allowed job kind."""

    job_kind: str
    last_attempt_at: str
    last_success_at: str
    last_failure_at: str
    next_due_at: str
    failure_count: int
    due: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_kind": self.job_kind,
            "last_attempt_at": self.last_attempt_at,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "next_due_at": self.next_due_at,
            "failure_count": self.failure_count,
            "due": self.due,
        }


@dataclass(frozen=True)
class PlayerSessionSchedulerStatus:
    """Safe read-only scheduler status for operator visibility."""

    instance: str
    checked_at: str
    state: str
    reason: str
    state_row_count: int
    jobs: tuple[PlayerSessionSchedulerStatusJob, ...]
    automatic_scheduler_enabled: bool = False
    service_timer_daemon_enabled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance": self.instance,
            "checked_at": self.checked_at,
            "state": self.state,
            "reason": self.reason,
            "state_row_count": self.state_row_count,
            "automatic_scheduler_enabled": self.automatic_scheduler_enabled,
            "service_timer_daemon_enabled": self.service_timer_daemon_enabled,
            "jobs": [job.to_dict() for job in self.jobs],
        }


@dataclass(frozen=True)
class PlayerSessionSchedulerJobResult:
    """One job-kind decision from a scheduler pass."""

    job_kind: str
    due: bool
    outcome: str
    created: bool = False
    job_id: int | None = None
    next_due_at: str = ""
    failure_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_kind": self.job_kind,
            "due": self.due,
            "outcome": self.outcome,
            "created": self.created,
            "job_id": self.job_id,
            "next_due_at": self.next_due_at,
            "failure_count": self.failure_count,
        }


@dataclass(frozen=True)
class PlayerSessionSchedulerRunResult:
    """Controlled summary for one explicit scheduler run."""

    instance: str
    checked_at: str
    jobs: tuple[PlayerSessionSchedulerJobResult, ...]

    @property
    def checked_count(self) -> int:
        return len(self.jobs)

    @property
    def due_count(self) -> int:
        return sum(1 for job in self.jobs if job.due)

    @property
    def enqueued_count(self) -> int:
        return sum(1 for job in self.jobs if job.outcome == "enqueued")

    @property
    def active_count(self) -> int:
        return sum(1 for job in self.jobs if job.outcome == "active")

    @property
    def failed_count(self) -> int:
        return sum(1 for job in self.jobs if job.outcome == "failed")

    @property
    def success(self) -> bool:
        return self.failed_count == 0

    @property
    def exit_code(self) -> int:
        return 0 if self.success else 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance": self.instance,
            "checked_at": self.checked_at,
            "checked_count": self.checked_count,
            "due_count": self.due_count,
            "enqueued_count": self.enqueued_count,
            "active_count": self.active_count,
            "failed_count": self.failed_count,
            "success": self.success,
            "jobs": [job.to_dict() for job in self.jobs],
        }


SchedulerEnqueue = Callable[
    [Path, str],
    tuple[JobRecord, bool],
]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _datetime_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    normalized = f"{text[:-1]}+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
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


def _connect(db_path: Path) -> sqlite3.Connection:
    try:
        ensure_web_db(db_path)
        connection = sqlite3.connect(db_path)
        connection.execute("PRAGMA foreign_keys = ON")
    except (OSError, RuntimeError, sqlite3.Error) as error:
        raise PlayerSessionSchedulerRunnerError(
            "Failed to open player-session scheduler state."
        ) from error
    connection.row_factory = sqlite3.Row
    return connection


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


def get_player_session_scheduler_state(
    db_path: Path,
    *,
    job_kind: str,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> PlayerSessionSchedulerState | None:
    """Return safe scheduler state for one job kind, if it exists."""
    normalized_instance = _safe_instance(instance)
    with _connect(db_path) as connection:
        row = connection.execute(
            f"""
            SELECT instance, job_kind, last_attempt_at, last_success_at,
                   last_failure_at, next_due_at, failure_count, updated_at
            FROM {_STATE_TABLE}
            WHERE instance = ? AND job_kind = ?
            """,
            (normalized_instance, job_kind),
        ).fetchone()
    if row is None:
        return None
    return _state_from_row(row)


def list_player_session_scheduler_state(
    db_path: Path,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> tuple[PlayerSessionSchedulerState, ...]:
    """Return safe scheduler state rows for one instance."""
    normalized_instance = _safe_instance(instance)
    with _connect(db_path) as connection:
        rows = connection.execute(
            f"""
            SELECT instance, job_kind, last_attempt_at, last_success_at,
                   last_failure_at, next_due_at, failure_count, updated_at
            FROM {_STATE_TABLE}
            WHERE instance = ?
            ORDER BY job_kind ASC
            """,
            (normalized_instance,),
        ).fetchall()
    return tuple(_state_from_row(row) for row in rows)


def _readonly_db_uri(db_path: Path) -> str:
    quoted_path = quote(str(db_path), safe="/")
    return f"file:{quoted_path}?mode=ro"


def _status_job_from_state(
    job_policy: player_session_scheduler_policy.AutomaticSessionJobPolicy,
    state: PlayerSessionSchedulerState | None,
    *,
    now: datetime,
) -> PlayerSessionSchedulerStatusJob:
    return PlayerSessionSchedulerStatusJob(
        job_kind=job_policy.job_kind,
        last_attempt_at=state.last_attempt_at if state is not None else "",
        last_success_at=state.last_success_at if state is not None else "",
        last_failure_at=state.last_failure_at if state is not None else "",
        next_due_at=state.next_due_at if state is not None else "",
        failure_count=state.failure_count if state is not None else 0,
        due=_is_due(state, now=now),
    )


def _status_from_states(
    *,
    instance: str,
    checked_at: datetime,
    states_by_kind: dict[str, PlayerSessionSchedulerState],
    reason: str,
) -> PlayerSessionSchedulerStatus:
    allowed_kinds = set(player_session_scheduler_policy.AUTOMATIC_SESSION_JOB_KINDS)
    filtered_states = {
        job_kind: state
        for job_kind, state in states_by_kind.items()
        if job_kind in allowed_kinds
    }
    state_row_count = len(filtered_states)
    state_name = "available" if state_row_count else "empty"
    status_reason = reason
    if reason == "ok" and not state_row_count:
        status_reason = "no_rows"
    jobs = tuple(
        _status_job_from_state(
            job_policy,
            filtered_states.get(job_policy.job_kind),
            now=checked_at,
        )
        for job_policy in player_session_scheduler_policy.AUTOMATIC_SESSION_JOB_POLICIES
    )
    return PlayerSessionSchedulerStatus(
        instance=instance,
        checked_at=_datetime_text(checked_at),
        state=state_name,
        reason=status_reason,
        state_row_count=state_row_count,
        automatic_scheduler_enabled=bool(
            player_session_scheduler_policy.AUTOMATIC_SESSION_SCHEDULER_ENABLED
        ),
        service_timer_daemon_enabled=False,
        jobs=jobs,
    )


def _empty_status(
    *,
    instance: str,
    checked_at: datetime,
    reason: str,
    state: str = "empty",
) -> PlayerSessionSchedulerStatus:
    status = _status_from_states(
        instance=instance,
        checked_at=checked_at,
        states_by_kind={},
        reason=reason,
    )
    return PlayerSessionSchedulerStatus(
        instance=status.instance,
        checked_at=status.checked_at,
        state=state,
        reason=reason,
        state_row_count=0,
        automatic_scheduler_enabled=status.automatic_scheduler_enabled,
        service_timer_daemon_enabled=status.service_timer_daemon_enabled,
        jobs=status.jobs,
    )


def read_player_session_scheduler_status(
    db_path: Path,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    now: datetime | None = None,
) -> PlayerSessionSchedulerStatus:
    """Return safe scheduler status without creating or mutating web.db."""
    normalized_instance = _safe_instance(instance)
    checked_at = now.astimezone(timezone.utc) if now is not None else _utc_now()
    try:
        db_exists = db_path.is_file()
    except OSError:
        db_exists = False
    if not db_exists:
        return _empty_status(
            instance=normalized_instance,
            checked_at=checked_at,
            reason="web_db_missing",
        )

    try:
        connection = sqlite3.connect(_readonly_db_uri(db_path), uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        table_row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = ? AND name = ?",
            ("table", _STATE_TABLE),
        ).fetchone()
        if table_row is None:
            return _empty_status(
                instance=normalized_instance,
                checked_at=checked_at,
                reason="state_table_missing",
            )
        placeholders = ", ".join(
            "?" for _ in player_session_scheduler_policy.AUTOMATIC_SESSION_JOB_KINDS
        )
        rows = connection.execute(
            f"""
            SELECT instance, job_kind, last_attempt_at, last_success_at,
                   last_failure_at, next_due_at, failure_count, updated_at
            FROM {_STATE_TABLE}
            WHERE instance = ? AND job_kind IN ({placeholders})
            ORDER BY job_kind ASC
            """,
            (
                normalized_instance,
                *player_session_scheduler_policy.AUTOMATIC_SESSION_JOB_KINDS,
            ),
        ).fetchall()
    except (OSError, RuntimeError, sqlite3.Error):
        return _empty_status(
            instance=normalized_instance,
            checked_at=checked_at,
            reason="state_unavailable",
            state="unavailable",
        )
    finally:
        try:
            connection.close()
        except (NameError, sqlite3.Error):
            pass

    states_by_kind = {str(row["job_kind"] or ""): _state_from_row(row) for row in rows}
    return _status_from_states(
        instance=normalized_instance,
        checked_at=checked_at,
        states_by_kind=states_by_kind,
        reason="ok",
    )


def _write_state(
    db_path: Path,
    *,
    instance: str,
    job_kind: str,
    last_attempt_at: str,
    last_success_at: str,
    last_failure_at: str,
    next_due_at: str,
    failure_count: int,
    updated_at: str,
) -> PlayerSessionSchedulerState:
    bounded_failure_count = min(max(0, int(failure_count)), MAX_STORED_FAILURE_COUNT)
    with _connect(db_path) as connection:
        connection.execute(
            f"""
            INSERT INTO {_STATE_TABLE}(
                instance, job_kind, last_attempt_at, last_success_at,
                last_failure_at, next_due_at, failure_count, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(instance, job_kind) DO UPDATE SET
                last_attempt_at = excluded.last_attempt_at,
                last_success_at = excluded.last_success_at,
                last_failure_at = excluded.last_failure_at,
                next_due_at = excluded.next_due_at,
                failure_count = excluded.failure_count,
                updated_at = excluded.updated_at
            """,
            (
                instance,
                job_kind,
                last_attempt_at,
                last_success_at,
                last_failure_at,
                next_due_at,
                bounded_failure_count,
                updated_at,
            ),
        )
    state = get_player_session_scheduler_state(
        db_path,
        instance=instance,
        job_kind=job_kind,
    )
    if state is None:
        raise PlayerSessionSchedulerRunnerError("Failed to store scheduler state.")
    return state


def _is_due(
    state: PlayerSessionSchedulerState | None,
    *,
    now: datetime,
) -> bool:
    if state is None:
        return True
    next_due = _parse_datetime(state.next_due_at)
    return next_due is None or next_due <= now


def _bounded_failure_count(state: PlayerSessionSchedulerState | None) -> int:
    if state is None:
        return 1
    return min(state.failure_count + 1, MAX_STORED_FAILURE_COUNT)


def _failure_backoff(
    job_policy: player_session_scheduler_policy.AutomaticSessionJobPolicy,
    failure_count: int,
) -> timedelta:
    backoff = job_policy.initial_failure_backoff
    for _ in range(1, max(1, min(failure_count, MAX_STORED_FAILURE_COUNT))):
        backoff *= 2
        if backoff >= job_policy.maximum_failure_backoff:
            return job_policy.maximum_failure_backoff
    return min(backoff, job_policy.maximum_failure_backoff)


def _ensure_live_scan_job(db_path: Path, instance: str) -> tuple[JobRecord, bool]:
    return player_live_session_scan.ensure_player_live_session_scan_job(
        db_path,
        requested_by_username=SCHEDULER_USERNAME,
        instance=instance,
    )


def _ensure_log_sessionization_job(
    db_path: Path,
    instance: str,
) -> tuple[JobRecord, bool]:
    return player_log_sessionization.ensure_player_log_sessionization_job(
        db_path,
        requested_by_username=SCHEDULER_USERNAME,
        instance=instance,
    )


def _ensure_session_maintenance_job(
    db_path: Path,
    instance: str,
) -> tuple[JobRecord, bool]:
    return player_session_maintenance.ensure_player_session_maintenance_job(
        db_path,
        requested_by_username=SCHEDULER_USERNAME,
        instance=instance,
    )


_ENQUEUE_BY_KIND: Final[dict[str, SchedulerEnqueue]] = {
    "players:scan-live-sessions": _ensure_live_scan_job,
    "players:sessionize-log-events": _ensure_log_sessionization_job,
    "players:session-maintenance": _ensure_session_maintenance_job,
}


def _ensure_runner_policy_is_allowed() -> None:
    policy_kinds = set(player_session_scheduler_policy.automatic_job_policy_by_kind())
    enqueued_kinds = set(_ENQUEUE_BY_KIND)
    if policy_kinds != enqueued_kinds:
        raise PlayerSessionSchedulerRunnerError(
            "Player-session scheduler policy and enqueue map do not match."
        )


def _result_for_not_due(
    job_policy: player_session_scheduler_policy.AutomaticSessionJobPolicy,
    state: PlayerSessionSchedulerState,
) -> PlayerSessionSchedulerJobResult:
    return PlayerSessionSchedulerJobResult(
        job_kind=job_policy.job_kind,
        due=False,
        outcome="not_due",
        next_due_at=state.next_due_at,
        failure_count=state.failure_count,
    )


def _record_success(
    db_path: Path,
    *,
    instance: str,
    job_policy: player_session_scheduler_policy.AutomaticSessionJobPolicy,
    state: PlayerSessionSchedulerState | None,
    now: datetime,
    created: bool,
    job_id: int,
) -> PlayerSessionSchedulerJobResult:
    now_text = _datetime_text(now)
    next_due_at = _datetime_text(now + job_policy.minimum_interval)
    stored = _write_state(
        db_path,
        instance=instance,
        job_kind=job_policy.job_kind,
        last_attempt_at=now_text,
        last_success_at=now_text,
        last_failure_at=state.last_failure_at if state is not None else "",
        next_due_at=next_due_at,
        failure_count=0,
        updated_at=now_text,
    )
    return PlayerSessionSchedulerJobResult(
        job_kind=job_policy.job_kind,
        due=True,
        outcome="enqueued" if created else "active",
        created=created,
        job_id=job_id,
        next_due_at=stored.next_due_at,
        failure_count=stored.failure_count,
    )


def _record_failure(
    db_path: Path,
    *,
    instance: str,
    job_policy: player_session_scheduler_policy.AutomaticSessionJobPolicy,
    state: PlayerSessionSchedulerState | None,
    now: datetime,
) -> PlayerSessionSchedulerJobResult:
    failure_count = _bounded_failure_count(state)
    now_text = _datetime_text(now)
    next_due_at = _datetime_text(now + _failure_backoff(job_policy, failure_count))
    stored = _write_state(
        db_path,
        instance=instance,
        job_kind=job_policy.job_kind,
        last_attempt_at=now_text,
        last_success_at=state.last_success_at if state is not None else "",
        last_failure_at=now_text,
        next_due_at=next_due_at,
        failure_count=failure_count,
        updated_at=now_text,
    )
    return PlayerSessionSchedulerJobResult(
        job_kind=job_policy.job_kind,
        due=True,
        outcome="failed",
        next_due_at=stored.next_due_at,
        failure_count=stored.failure_count,
    )


def run_player_session_scheduler_once(
    db_path: Path,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    now: datetime | None = None,
) -> PlayerSessionSchedulerRunResult:
    """Check due session scheduler jobs once and enqueue allowed active jobs."""
    _ensure_runner_policy_is_allowed()
    normalized_instance = _safe_instance(instance)
    checked_at = now.astimezone(timezone.utc) if now is not None else _utc_now()

    results: list[PlayerSessionSchedulerJobResult] = []
    for job_policy in player_session_scheduler_policy.AUTOMATIC_SESSION_JOB_POLICIES:
        state = get_player_session_scheduler_state(
            db_path,
            instance=normalized_instance,
            job_kind=job_policy.job_kind,
        )
        if state is not None and not _is_due(state, now=checked_at):
            results.append(_result_for_not_due(job_policy, state))
            continue

        try:
            job, created = _ENQUEUE_BY_KIND[job_policy.job_kind](
                db_path,
                normalized_instance,
            )
        except Exception:
            results.append(
                _record_failure(
                    db_path,
                    instance=normalized_instance,
                    job_policy=job_policy,
                    state=state,
                    now=checked_at,
                )
            )
            continue

        results.append(
            _record_success(
                db_path,
                instance=normalized_instance,
                job_policy=job_policy,
                state=state,
                now=checked_at,
                created=created,
                job_id=job.id,
            )
        )

    return PlayerSessionSchedulerRunResult(
        instance=normalized_instance,
        checked_at=_datetime_text(checked_at),
        jobs=tuple(results),
    )
