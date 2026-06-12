"""Background job metadata primitives for armactl web."""

from __future__ import annotations

from armactl.web.jobs.models import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    JobRecord,
)
from armactl.web.jobs.runner import (
    JobContext,
    JobDispatcher,
    JobDispatchResult,
    JobHandlerResult,
    JobRunnerError,
    create_default_dispatcher,
    dispatch_job,
    enqueue_job,
)
from armactl.web.jobs.store import (
    JobNotFoundError,
    JobStoreError,
    JobTransitionError,
    append_job_output,
    cancel_job,
    create_job,
    get_job,
    list_recent_jobs,
    mark_job_failed,
    mark_job_running,
    mark_job_succeeded,
)

__all__ = [
    "JOB_STATUS_CANCELLED",
    "JOB_STATUS_FAILED",
    "JOB_STATUS_QUEUED",
    "JOB_STATUS_RUNNING",
    "JOB_STATUS_SUCCEEDED",
    "JobContext",
    "JobDispatchResult",
    "JobDispatcher",
    "JobHandlerResult",
    "JobNotFoundError",
    "JobRunnerError",
    "JobRecord",
    "JobStoreError",
    "JobTransitionError",
    "append_job_output",
    "create_default_dispatcher",
    "cancel_job",
    "create_job",
    "dispatch_job",
    "enqueue_job",
    "get_job",
    "list_recent_jobs",
    "mark_job_failed",
    "mark_job_running",
    "mark_job_succeeded",
]
