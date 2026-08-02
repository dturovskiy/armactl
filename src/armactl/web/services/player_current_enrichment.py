"""Read-only current-player enrichment from shared proven session stats."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from armactl import paths
from armactl.web.services import player_registry, player_session_stats
from armactl.web.services.player_identity import normalize_reliable_player_id

CURRENT_STATS_INGEST_SCOPE = player_session_stats.CURRENT_STATS_INGEST_SCOPE
CURRENT_STATS_FRESHNESS_MAX_AGE_SECONDS = (
    player_session_stats.CURRENT_STATS_FRESHNESS_MAX_AGE_SECONDS
)
CURRENT_STATS_SOURCE_LABEL = player_session_stats.CURRENT_STATS_SOURCE_LABEL
CURRENT_FACTION_EVIDENCE_TITLE = (
    "Last-known faction from fresh play-session log evidence; not guaranteed current."
)
CURRENT_STATS_UNAVAILABLE_REASON = player_session_stats.CURRENT_STATS_UNAVAILABLE_REASON
CURRENT_STATS_NO_RELIABLE_ID_REASON = (
    "Stats unavailable because the current roster has no reliable player ID."
)
CURRENT_STATS_NO_DATABASE_REASON = player_session_stats.CURRENT_STATS_NO_DATABASE_REASON
CURRENT_STATS_NO_SESSION_REASON = player_session_stats.CURRENT_STATS_NO_SESSION_REASON
CURRENT_STATS_SESSION_UNPROVEN_REASON = (
    player_session_stats.CURRENT_STATS_SESSION_UNPROVEN_REASON
)
CURRENT_STATS_FRESHNESS_UNAVAILABLE_REASON = (
    player_session_stats.CURRENT_STATS_FRESHNESS_UNAVAILABLE_REASON
)
CURRENT_STATS_FRESHNESS_INCOMPLETE_REASON = (
    player_session_stats.CURRENT_STATS_FRESHNESS_INCOMPLETE_REASON
)
CURRENT_STATS_FRESHNESS_STALE_REASON = (
    player_session_stats.CURRENT_STATS_FRESHNESS_STALE_REASON
)
CURRENT_STATS_CHECKPOINT_UNAVAILABLE_REASON = (
    player_session_stats.CURRENT_STATS_CHECKPOINT_UNAVAILABLE_REASON
)
CURRENT_STATS_SESSION_COVERAGE_REASON = (
    player_session_stats.CURRENT_STATS_SESSION_COVERAGE_REASON
)

_FRESHNESS_STATUS_UNAVAILABLE = player_session_stats.FRESHNESS_STATUS_UNAVAILABLE
_FRESHNESS_STATUS_INCOMPLETE = player_session_stats.FRESHNESS_STATUS_INCOMPLETE
_FRESHNESS_STATUS_STALE = player_session_stats.FRESHNESS_STATUS_STALE
_FRESHNESS_STATUS_FRESH = player_session_stats.FRESHNESS_STATUS_FRESH


@dataclass(frozen=True)
class CurrentPlayerEnrichment:
    """Nullable current-roster enrichment guarded by the play-session contract."""

    kills: int | None = None
    deaths: int | None = None
    teamkills: int | None = None
    faction: str | None = None
    first_observed_at: str | None = None
    stats_available: bool = False
    stats_source_label: str = ""
    stats_unavailable_reason: str = CURRENT_STATS_UNAVAILABLE_REASON
    stats_freshness_status: str = _FRESHNESS_STATUS_UNAVAILABLE
    stats_freshness_at: str | None = None
    stats_window_started_at: str | None = None
    stats_window_ended_at: str | None = None
    stats_reconnect_merged: bool = False


def _current_enrichment_from_stats(
    stats: player_session_stats.PlayerSessionStats,
) -> CurrentPlayerEnrichment:
    return CurrentPlayerEnrichment(
        kills=stats.kills,
        deaths=stats.deaths,
        teamkills=stats.teamkills,
        faction=stats.faction,
        first_observed_at=stats.window_started_at,
        stats_available=stats.stats_available,
        stats_source_label=stats.source_label,
        stats_unavailable_reason=stats.stats_unavailable_reason or "",
        stats_freshness_status=stats.freshness_status,
        stats_freshness_at=stats.covered_through,
        stats_window_started_at=stats.window_started_at,
        stats_window_ended_at=stats.window_ended_at,
        stats_reconnect_merged=stats.reconnect_merged,
    )


def load_current_player_enrichment(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
    reliable_ids: tuple[str, ...] | list[str] = (),
    now_at: str | None = None,
) -> dict[str, CurrentPlayerEnrichment]:
    """Return shared session-scoped stats keyed by reliable current-player ID."""
    normalized_ids = tuple(
        dict.fromkeys(
            normalized
            for reliable_id in reliable_ids
            if (normalized := normalize_reliable_player_id(reliable_id))
        )
    )
    if not normalized_ids:
        return {}

    db_path = player_registry.player_registry_db_path(instance, data_root=data_root)
    stats_by_id = player_session_stats.load_current_session_stats(
        db_path,
        reliable_ids=normalized_ids,
        now_at=now_at,
    )
    return {
        reliable_id: _current_enrichment_from_stats(stats_by_id[reliable_id])
        for reliable_id in normalized_ids
    }
