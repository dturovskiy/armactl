"""Declarative defaults for a future automatic player-session scheduler.

Importing this module must remain side-effect free: it does not enqueue jobs,
start workers, install timers, or poll live player sources.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from armactl import player_current_cache_updater
from armactl.web.jobs import player_sessions
from armactl.web.services import player_registry

AUTOMATIC_SESSION_SCHEDULER_ENABLED: Final = False
SESSION_SCHEDULER_POLICY_VERSION: Final = "phase-4e-4f-planning"

CURRENT_ROSTER_CACHE_DEFAULT_INTERVAL: Final = timedelta(
    seconds=player_current_cache_updater.DEFAULT_INTERVAL_SECONDS
)
LIVE_SCAN_CACHE_OFFSET_GRACE: Final = timedelta(seconds=15)


@dataclass(frozen=True)
class AutomaticSessionJobPolicy:
    """Minimum timing and retry policy for one future automatic session job."""

    job_kind: str
    minimum_interval: timedelta
    initial_failure_backoff: timedelta
    maximum_failure_backoff: timedelta
    notes: str


AUTOMATIC_SESSION_JOB_POLICIES: Final = (
    AutomaticSessionJobPolicy(
        job_kind=player_sessions.PLAYER_LIVE_SESSION_SCAN_JOB_KIND,
        minimum_interval=CURRENT_ROSTER_CACHE_DEFAULT_INTERVAL * 2,
        initial_failure_backoff=timedelta(minutes=1),
        maximum_failure_backoff=timedelta(minutes=15),
        notes=(
            "Reliable RCON roster scan only; keep offset from the current-roster "
            "cache updater and rely on job-layer active dedupe."
        ),
    ),
    AutomaticSessionJobPolicy(
        job_kind=player_sessions.PLAYER_LOG_SESSIONIZATION_JOB_KIND,
        minimum_interval=timedelta(minutes=5),
        initial_failure_backoff=timedelta(minutes=5),
        maximum_failure_backoff=timedelta(minutes=30),
        notes=(
            "Stored sanitized player_log_events only; do not read live logs or "
            "operator-supplied paths from the scheduler."
        ),
    ),
    AutomaticSessionJobPolicy(
        job_kind=player_sessions.PLAYER_SESSION_MAINTENANCE_JOB_KIND,
        minimum_interval=timedelta(hours=1),
        initial_failure_backoff=timedelta(minutes=15),
        maximum_failure_backoff=timedelta(hours=1),
        notes=(
            "Stale-close and closed-session retention only, in bounded batches "
            "with counts-only output."
        ),
    ),
)

AUTOMATIC_SESSION_JOB_KINDS: Final = tuple(
    policy.job_kind for policy in AUTOMATIC_SESSION_JOB_POLICIES
)

ALLOWED_AUTOMATIC_CLOSE_END_REASONS: Final = (
    player_registry.PLAYER_SESSION_END_REASON_DISCONNECT,
    player_registry.PLAYER_SESSION_END_REASON_SERVER_BOUNDARY,
    player_registry.PLAYER_SESSION_END_REASON_STALE_ABSENCE,
    player_registry.PLAYER_SESSION_END_REASON_STALE_TIMEOUT,
)

COUNT_ONLY_EVIDENCE_SOURCES: Final = (
    "a2s",
    "fps_logstats_player_count",
    "job_output",
    "audit_details",
)

SANITIZED_ONLY_SESSION_FIELDS: Final = (
    "display_name",
    "reliable_id",
    "source",
    "source_ref",
    "confidence",
    "end_reason",
    "faction_or_side_snapshot",
)

FORBIDDEN_AUTOMATIC_SESSION_DATA: Final = (
    "ip",
    "ip_address",
    "raw_log_line",
    "raw_log_path",
    "raw_rcon_row",
    "secret",
    "public_player_id",
)

FORBIDDEN_AUTOMATIC_SESSION_FEATURES: Final = (
    "k_d",
    "role",
    "playtime",
    "discord_enrichment",
    "ban",
    "kick",
    "banlist",
    "current_faction_truth",
)


def automatic_job_policy_by_kind() -> dict[str, AutomaticSessionJobPolicy]:
    """Return future automatic job policies keyed by job kind."""
    return {policy.job_kind: policy for policy in AUTOMATIC_SESSION_JOB_POLICIES}
