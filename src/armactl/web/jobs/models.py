"""Data models for web background job metadata."""

from __future__ import annotations

from dataclasses import dataclass, field

JOB_STATUS_QUEUED = "queued"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_SUCCEEDED = "succeeded"
JOB_STATUS_FAILED = "failed"
JOB_STATUS_CANCELLED = "cancelled"

JOB_STATUSES = frozenset(
    {
        JOB_STATUS_QUEUED,
        JOB_STATUS_RUNNING,
        JOB_STATUS_SUCCEEDED,
        JOB_STATUS_FAILED,
        JOB_STATUS_CANCELLED,
    }
)
TERMINAL_JOB_STATUSES = frozenset(
    {
        JOB_STATUS_SUCCEEDED,
        JOB_STATUS_FAILED,
        JOB_STATUS_CANCELLED,
    }
)


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

    @property
    def is_terminal(self) -> bool:
        """Return whether this job can no longer transition."""
        return self.status in TERMINAL_JOB_STATUSES
