"""Data models for web background job metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

JOB_STATUS_QUEUED = "queued"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_SUCCEEDED = "succeeded"
JOB_STATUS_FAILED = "failed"
JOB_STATUS_CANCELLED = "cancelled"
JOB_STATUS_ABANDONED = "abandoned"

JOB_STATUSES = frozenset(
    {
        JOB_STATUS_QUEUED,
        JOB_STATUS_RUNNING,
        JOB_STATUS_SUCCEEDED,
        JOB_STATUS_FAILED,
        JOB_STATUS_CANCELLED,
        JOB_STATUS_ABANDONED,
    }
)
TERMINAL_JOB_STATUSES = frozenset(
    {
        JOB_STATUS_SUCCEEDED,
        JOB_STATUS_FAILED,
        JOB_STATUS_CANCELLED,
        JOB_STATUS_ABANDONED,
    }
)

JOB_WORKER_LEASE_FRESH = "fresh"
JOB_WORKER_LEASE_EXPIRED = "expired"
JOB_WORKER_LEASE_UNKNOWN = "unknown"
JOB_WORKER_LEASE_INACTIVE = "inactive"


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class JobRecord:
    """Persisted web background-job metadata with bounded safe text fields."""

    id: int
    kind: str
    status: str
    requested_by_username: str
    instance: str
    created_at: str
    updated_at: str
    requested_by_user_id: int | None = None
    started_at: str | None = None
    finished_at: str | None = None
    progress_current: int = 0
    progress_total: int = 0
    current_step: str = ""
    result_message: str = field(default="", repr=False)
    stdout_tail: str = field(default="", repr=False)
    stderr_tail: str = field(default="", repr=False)
    error_message: str = field(default="", repr=False)
    error_class: str = field(default="", repr=False)
    worker_id: str = field(default="", repr=False)
    worker_started_at: str | None = None
    worker_heartbeat_at: str | None = None
    worker_lease_expires_at: str | None = None

    @property
    def is_terminal(self) -> bool:
        """Return whether this job can no longer transition."""
        return self.status in TERMINAL_JOB_STATUSES

    @property
    def worker_lease_state(self) -> str:
        """Return a display-safe worker lease state for this job."""
        if self.status != JOB_STATUS_RUNNING:
            return JOB_WORKER_LEASE_INACTIVE
        expires_at = _parse_timestamp(self.worker_lease_expires_at)
        if expires_at is None:
            return JOB_WORKER_LEASE_UNKNOWN
        if expires_at <= datetime.now(timezone.utc):
            return JOB_WORKER_LEASE_EXPIRED
        return JOB_WORKER_LEASE_FRESH

    @property
    def has_fresh_worker_lease(self) -> bool:
        """Return whether a running job currently has a fresh worker lease."""
        return self.worker_lease_state == JOB_WORKER_LEASE_FRESH
