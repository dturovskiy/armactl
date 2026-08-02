"""Player registry and moderation page DTO loaders."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from armactl import paths, player_log_events
from armactl.web.jobs import models as job_models
from armactl.web.jobs import player_logs as player_log_jobs
from armactl.web.jobs import player_sessions as player_session_jobs
from armactl.web.jobs import store as job_store
from armactl.web.services import (
    player_current_cache,
    player_current_enrichment,
    player_registry,
    player_session_details,
    player_session_stats,
    player_sources,
)
from armactl.web.services.player_identity import normalize_player_query, safe_player_text

PLAYER_HISTORY_EVENT_TYPE_LABELS = {
    player_log_events.EVENT_TYPE_PLAYER_AUTHENTICATED: "Authenticated",
    player_log_events.EVENT_TYPE_PLAYER_UPDATE: "Player update",
    player_log_events.EVENT_TYPE_FACTION_JOIN: "Faction join",
    player_log_events.EVENT_TYPE_PLAYER_DISCONNECTED: "Disconnect",
    player_log_events.EVENT_TYPE_SERVER_LIFECYCLE: "Server lifecycle",
    player_log_events.EVENT_TYPE_KILL: "Kill",
    player_log_events.EVENT_TYPE_SUICIDE: "Suicide",
    player_log_events.EVENT_TYPE_TEAMKILL: "Teamkill",
    player_log_events.EVENT_TYPE_OTHER_DEATH: "Other death",
    player_log_events.EVENT_TYPE_COMBAT_HINT: "Combat hint",
}
PLAYER_HISTORY_EVENT_TYPES = (
    ("", "All event types"),
    *PLAYER_HISTORY_EVENT_TYPE_LABELS.items(),
)
PLAYER_HISTORY_EVENT_TYPE_VALUES = frozenset(
    event_type for event_type, _label in PLAYER_HISTORY_EVENT_TYPES if event_type
)
PLAYER_HISTORY_MODE_PLAYER_EVENTS = "player_events"
PLAYER_HISTORY_MODE_SESSION_EVIDENCE = "session_evidence"
PLAYER_HISTORY_MODE_OPTIONS = (
    (PLAYER_HISTORY_MODE_PLAYER_EVENTS, "Player events"),
    (PLAYER_HISTORY_MODE_SESSION_EVIDENCE, "Session evidence"),
)
PLAYER_HISTORY_MODE_VALUES = frozenset(value for value, _label in PLAYER_HISTORY_MODE_OPTIONS)
PLAYER_HISTORY_SOURCE_LABELS = {
    player_log_events.SOURCE_BACKEND_AUTH: "Backend auth",
    player_log_events.SOURCE_NETWORK_PLAYER_UPDATE: "Network player update",
    player_log_events.SOURCE_SCRIPT_FACTION_JOIN: "Faction event",
    player_log_events.SOURCE_RPL_DISCONNECT: "RPL disconnect",
    player_log_events.SOURCE_NETWORK_DISCONNECT: "Network disconnect",
    player_log_events.SOURCE_BATTLEYE_DISCONNECT: "BattlEye disconnect",
    player_log_events.SOURCE_SERVER_LIFECYCLE: "Service lifecycle",
    player_log_events.SOURCE_SCRIPT_KILL: "Combat event",
    player_log_events.SOURCE_SERVER_ADMIN_TOOLS_KILL: "ServerAdminTools event",
}
PLAYER_HISTORY_CONFIDENCE_LABELS = {
    player_log_events.CONFIDENCE_HIGH: "High",
    player_log_events.CONFIDENCE_MEDIUM: "Medium",
    player_log_events.CONFIDENCE_LOW: "Low",
}
PLAYER_HISTORY_TIME_SOURCE_LABELS = {
    player_log_events.EVENT_TIME_SOURCE_LOG_PREFIX_WITH_DATE: "Log timestamp",
    player_log_events.EVENT_TIME_SOURCE_LOG_PREFIX_WITHOUT_DATE: "Log timestamp without date",
    player_log_events.EVENT_TIME_SOURCE_CALLER_OCCURRED_AT: "Caller event time",
    player_log_events.EVENT_TIME_SOURCE_CALLER_OBSERVED_AT: "Caller observed time",
    player_log_events.EVENT_TIME_SOURCE_UNAVAILABLE: "Unavailable",
}
PLAYER_HISTORY_TIME_CONFIDENCE_LABELS = {
    player_log_events.EVENT_TIME_CONFIDENCE_EXACT: "Exact",
    player_log_events.EVENT_TIME_CONFIDENCE_DERIVED: "Derived",
    player_log_events.EVENT_TIME_CONFIDENCE_AMBIGUOUS: "Ambiguous",
}
PLAYER_LOG_INGEST_STATUS_LABELS = {
    player_registry.PLAYER_LOG_INGEST_STATUS_UNAVAILABLE: "No ingest status yet",
    player_registry.PLAYER_LOG_INGEST_STATUS_FRESH: "Fresh",
    player_registry.PLAYER_LOG_INGEST_STATUS_NO_LOGS: "No allowlisted logs",
    player_registry.PLAYER_LOG_INGEST_STATUS_PARTIAL: "Partial",
    player_registry.PLAYER_LOG_INGEST_STATUS_FAILED: "Failed",
}

PLAYER_HISTORY_COMBAT_EVENT_TYPES = frozenset(
    {
        player_log_events.EVENT_TYPE_KILL,
        player_log_events.EVENT_TYPE_SUICIDE,
        player_log_events.EVENT_TYPE_TEAMKILL,
        player_log_events.EVENT_TYPE_OTHER_DEATH,
        player_log_events.EVENT_TYPE_COMBAT_HINT,
    }
)

PLAYER_SESSION_STATUS_LABELS = {
    player_registry.PLAYER_SESSION_STATUS_OPEN: "Session not closed",
    player_registry.PLAYER_SESSION_STATUS_CLOSED: "Session closed",
}
PLAYER_SESSION_STATUS_OPTIONS = (
    ("", "All statuses"),
    *PLAYER_SESSION_STATUS_LABELS.items(),
)
PLAYER_SESSION_END_REASON_LABELS = {
    player_registry.PLAYER_SESSION_END_REASON_DISCONNECT: "Disconnect evidence",
    player_registry.PLAYER_SESSION_END_REASON_SERVER_BOUNDARY: (
        "Lifecycle/server boundary"
    ),
    player_registry.PLAYER_SESSION_END_REASON_STALE_TIMEOUT: (
        "Stale absence / stale timeout"
    ),
    player_registry.PLAYER_SESSION_END_REASON_STALE_ABSENCE: (
        "Stale absence / stale timeout"
    ),
    player_registry.PLAYER_SESSION_END_REASON_SCANNER_CHECKPOINT: (
        "Scanner checkpoint boundary"
    ),
    player_registry.PLAYER_SESSION_END_REASON_IMPORT_WINDOW: (
        "Stored log import window"
    ),
    player_registry.PLAYER_SESSION_END_REASON_UNKNOWN: "Unknown close reason",
}
PLAYER_SESSION_END_REASON_OPTIONS = (
    ("", "All end reasons"),
    *PLAYER_SESSION_END_REASON_LABELS.items(),
)
PLAYER_SESSION_SOURCE_LABELS = {
    player_registry.PLAYER_SESSION_SOURCE_BACKEND_AUTH: "Log evidence",
    player_registry.PLAYER_SESSION_SOURCE_NETWORK_PLAYER_UPDATE: "Log evidence",
    player_registry.PLAYER_SESSION_SOURCE_RCON_ROSTER: "Reliable roster evidence",
    player_registry.PLAYER_SESSION_SOURCE_SCRIPT_FACTION_JOIN: "Log evidence",
    player_registry.PLAYER_SESSION_SOURCE_SCRIPT_KILL: "Log evidence",
    player_registry.PLAYER_SESSION_SOURCE_SERVER_ADMIN_TOOLS_KILL: "Log evidence",
    player_registry.PLAYER_SESSION_SOURCE_SERVICE_LIFECYCLE: (
        "Lifecycle/server boundary"
    ),
    player_registry.PLAYER_SESSION_SOURCE_SCANNER_CHECKPOINT: (
        "Stale absence / stale timeout"
    ),
    player_registry.PLAYER_SESSION_SOURCE_MANUAL_IMPORT: "Log evidence",
}
PLAYER_SESSION_SOURCE_OPTIONS = (
    ("", "All sources"),
    *PLAYER_SESSION_SOURCE_LABELS.items(),
)
PLAYER_SESSION_CONFIDENCE_LABELS = {
    player_registry.PLAYER_SESSION_CONFIDENCE_HIGH: "High confidence",
    player_registry.PLAYER_SESSION_CONFIDENCE_MEDIUM: "Medium confidence",
    player_registry.PLAYER_SESSION_CONFIDENCE_LOW: "Low confidence",
}
PLAYER_SESSION_JOB_KIND_LABELS = {
    player_session_jobs.PLAYER_LIVE_SESSION_SCAN_JOB_KIND: "Scan live sessions",
    player_session_jobs.PLAYER_LOG_SESSIONIZATION_JOB_KIND: "Sessionize log events",
    player_session_jobs.PLAYER_SESSION_MAINTENANCE_JOB_KIND: "Session maintenance",
}
PLAYER_SESSION_JOB_STATUS_LABELS = {
    job_models.JOB_STATUS_QUEUED: "Queued",
    job_models.JOB_STATUS_RUNNING: "Running",
}
PLAYER_SESSION_OPERATOR_JOB_KINDS = frozenset(
    {
        player_session_jobs.PLAYER_LIVE_SESSION_SCAN_JOB_KIND,
        player_session_jobs.PLAYER_LOG_SESSIONIZATION_JOB_KIND,
        player_session_jobs.PLAYER_SESSION_MAINTENANCE_JOB_KIND,
    }
)
PLAYER_SESSION_ACTIVE_JOB_STATUSES = frozenset(
    {
        job_models.JOB_STATUS_QUEUED,
        job_models.JOB_STATUS_RUNNING,
    }
)
PLAYER_SESSION_TIMELINE_DEFAULT_LIMIT = 50
PLAYER_SESSION_STATE_EXPLANATIONS = {
    player_registry.PLAYER_SESSION_STATUS_OPEN: (
        "Stored as not closed; this is recorded evidence, not a live online guarantee."
    ),
    player_registry.PLAYER_SESSION_STATUS_CLOSED: (
        "Stored as closed from recorded evidence; the close time is not an exact leave time."
    ),
}
PLAYER_SESSION_FRESHNESS_LABELS = {
    player_session_stats.FRESHNESS_STATUS_UNAVAILABLE: "Unavailable",
    player_session_stats.FRESHNESS_STATUS_INCOMPLETE: "Incomplete",
    player_session_stats.FRESHNESS_STATUS_STALE: "Stale",
    player_session_stats.FRESHNESS_STATUS_FRESH: "Fresh",
}


@dataclass(frozen=True)
class ModerationPlayer:
    """One safe player row for moderation views."""

    display_name: str
    identity_id: str
    admin_reference: str
    source: str
    status: str
    last_seen: str

    @property
    def can_add_admin(self) -> bool:
        return bool(self.admin_reference)


@dataclass(frozen=True)
class PlayerModerationPanel:
    """Safe current-player DTO for web templates."""

    available: bool
    query: str
    players: tuple[ModerationPlayer, ...]
    total_count: int
    filtered_count: int
    source: str
    status: str
    error: str = ""


@dataclass(frozen=True)
class PlayerRegistryPage:
    """Read-only player registry page model."""

    instance: str
    query: str
    players: tuple[player_registry.KnownPlayer, ...]


@dataclass(frozen=True)
class CurrentPlayerTableRow:
    """One safe current-roster row with nullable stored-evidence enrichment."""

    display_name: str
    reliable_id: str
    source: str
    kills: int | None = None
    deaths: int | None = None
    teamkills: int | None = None
    faction: str | None = None
    first_observed_at: str | None = None
    stats_available: bool = False
    stats_source_label: str = ""
    stats_unavailable_reason: str = (
        player_current_enrichment.CURRENT_STATS_UNAVAILABLE_REASON
    )
    stats_freshness_status: str = "unavailable"
    stats_freshness_at: str | None = None
    stats_window_started_at: str | None = None
    stats_window_ended_at: str | None = None
    stats_reconnect_merged: bool = False


@dataclass(frozen=True)
class CurrentPlayersPage:
    """Read-only current-player roster page model."""

    instance: str
    query: str
    available: bool
    source: str
    status: str
    error: str
    refresh_error: str
    collected_at: str
    updated_at: str
    age_seconds: int | None
    is_stale: bool
    freshness: str
    cache_status: str
    observed_count: int
    total_count: int
    filtered_count: int
    count_source: str
    roster_available: bool
    roster_configured: bool
    stale_named_roster: bool
    players: tuple[CurrentPlayerTableRow, ...]


@dataclass(frozen=True)
class PlayerHistoryField:
    """One safe player-history detail or diagnostic field."""

    label: str
    value: str = ""
    display_value: str = ""
    title: str = ""
    kind: str = "text"
    value_labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlayerHistoryEventRow:
    """Template-ready player-history row with safe summaries."""

    record: player_registry.PlayerLogEventRecord
    label: str
    actor_label: str
    actor_value: str
    details: tuple[PlayerHistoryField, ...]
    diagnostics: tuple[PlayerHistoryField, ...]

    @property
    def event_type(self) -> str:
        return self.record.event_type

    @property
    def event_time(self) -> str:
        return self.record.event_time

    @property
    def event_time_label(self) -> str:
        return self.record.event_time_label


@dataclass(frozen=True)
class PlayerLogIngestFreshnessIndicator:
    """Safe counts-only player-log ingest freshness indicator."""

    status: str
    status_label: str
    updated_at: str
    last_success_at: str
    scanned_files: int
    parsed_events: int
    stored_events: int
    skipped_files: int
    skipped_reasons: str
    checkpoint_updated: bool


@dataclass(frozen=True)
class PlayerHistoryPage:
    """Read-only stored player event history page model."""

    instance: str
    query: str
    event_type: str
    mode: str
    reliable_id: str
    limit: int
    events: tuple[PlayerHistoryEventRow, ...]
    event_type_options: tuple[tuple[str, str], ...]
    event_type_labels: dict[str, str]
    mode_options: tuple[tuple[str, str], ...]
    ingest_freshness: PlayerLogIngestFreshnessIndicator


@dataclass(frozen=True)
class PlayerSessionJobIndicator:
    """Safe active job indicator for player-session operator UX."""

    job_id: int
    kind: str
    kind_label: str
    status: str
    status_label: str
    jobs_url: str = "/jobs#background-jobs"


@dataclass(frozen=True)
class PlayerSessionsPage:
    """Read-only stored player sessions page model."""

    instance: str
    query: str
    reliable_id: str
    status: str
    end_reason: str
    source: str
    from_time: str
    to_time: str
    before_time: str
    before_session_id: str
    limit: int
    search_status: str
    sessions: tuple[player_session_details.PlayerSessionSearchItem, ...]
    summary: player_registry.PlayerSessionSummary
    active_jobs: tuple[PlayerSessionJobIndicator, ...]
    refresh_url: str
    next_page_url: str
    session_detail_urls: dict[int, str]
    has_filters: bool
    filters_active: bool
    status_options: tuple[tuple[str, str], ...]
    status_labels: dict[str, str]
    end_reason_options: tuple[tuple[str, str], ...]
    end_reason_labels: dict[str, str]
    source_options: tuple[tuple[str, str], ...]
    source_labels: dict[str, str]
    confidence_labels: dict[str, str]


@dataclass(frozen=True)
class PlayerSessionTimelineParticipant:
    """One safe named timeline participant without a reliable/internal ID."""

    label: str
    name: str


@dataclass(frozen=True)
class PlayerSessionTimelineRow:
    """Template-ready high-signal timeline row."""

    event_time: str
    event_label: str
    participants: tuple[PlayerSessionTimelineParticipant, ...]
    participant_fallback: str
    details: tuple[PlayerHistoryField, ...]
    diagnostics: tuple[PlayerHistoryField, ...]


@dataclass(frozen=True)
class PlayerSessionDetailPage:
    """Read-only authenticated session detail integration model."""

    instance: str
    status: str
    detail: player_session_details.PlayerSessionDetail | None
    timeline_status: str
    timeline_rows: tuple[PlayerSessionTimelineRow, ...]
    timeline_unavailable_reason: str
    back_url: str
    older_timeline_url: str
    status_labels: dict[str, str]
    state_explanations: dict[str, str]
    end_reason_labels: dict[str, str]
    source_labels: dict[str, str]
    confidence_labels: dict[str, str]
    freshness_labels: dict[str, str]


def _moderation_player(player: player_sources.CurrentPlayer) -> ModerationPlayer:
    return ModerationPlayer(
        display_name=player.display_name,
        identity_id=player.reliable_id,
        admin_reference=player.admin_reference,
        source=player.source,
        status="active",
        last_seen="online now",
    )


def _matches_query(player: ModerationPlayer, query: str) -> bool:
    if not query:
        return True
    needle = query.casefold()
    return any(
        needle in value.casefold()
        for value in (
            player.display_name,
            player.identity_id,
            player.admin_reference,
            player.source,
        )
    )


def load_player_moderation_panel(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    query: str = "",
) -> PlayerModerationPanel:
    """Return safe current-player data for moderation UI."""
    normalized_query = normalize_player_query(query)
    try:
        roster = player_sources.load_current_player_roster(instance)
    except Exception as error:  # noqa: BLE001 - read-only page data degrades safely.
        return PlayerModerationPanel(
            available=False,
            query=normalized_query,
            players=(),
            total_count=0,
            filtered_count=0,
            source="unavailable",
            status="unavailable",
            error=safe_player_text(error),
        )

    players = tuple(_moderation_player(player) for player in roster.players)
    filtered = tuple(player for player in players if _matches_query(player, normalized_query))
    return PlayerModerationPanel(
        available=roster.available,
        query=normalized_query,
        players=filtered,
        total_count=roster.total_count,
        filtered_count=len(filtered),
        source=safe_player_text(roster.source) or "unavailable",
        status=safe_player_text(roster.status) or "unknown",
        error=safe_player_text(roster.error),
    )


def _current_player_row(
    player: player_current_cache.CurrentRosterPlayerSnapshot,
    enrichment: player_current_enrichment.CurrentPlayerEnrichment | None = None,
) -> CurrentPlayerTableRow:
    reliable_id = safe_player_text(player.reliable_id)
    unavailable_reason = player_current_enrichment.CURRENT_STATS_UNAVAILABLE_REASON
    if not reliable_id:
        unavailable_reason = player_current_enrichment.CURRENT_STATS_NO_RELIABLE_ID_REASON
    elif enrichment and enrichment.stats_available:
        unavailable_reason = ""
    elif enrichment and enrichment.stats_unavailable_reason:
        unavailable_reason = safe_player_text(
            enrichment.stats_unavailable_reason,
            max_length=180,
        )
    return CurrentPlayerTableRow(
        display_name=safe_player_text(player.display_name) or "Unknown player",
        reliable_id=reliable_id,
        source=safe_player_text(player.source) or "unknown",
        kills=enrichment.kills if enrichment else None,
        deaths=enrichment.deaths if enrichment else None,
        teamkills=enrichment.teamkills if enrichment else None,
        faction=safe_player_text(enrichment.faction, max_length=80)
        if enrichment and enrichment.faction
        else None,
        first_observed_at=safe_player_text(enrichment.first_observed_at, max_length=80)
        if enrichment and enrichment.first_observed_at
        else None,
        stats_available=bool(enrichment and enrichment.stats_available),
        stats_source_label=safe_player_text(enrichment.stats_source_label, max_length=80)
        if enrichment
        else "",
        stats_unavailable_reason=unavailable_reason,
        stats_freshness_status=(
            safe_player_text(enrichment.stats_freshness_status, max_length=40)
            if enrichment
            else "unavailable"
        ),
        stats_freshness_at=(
            safe_player_text(enrichment.stats_freshness_at, max_length=80)
            if enrichment and enrichment.stats_freshness_at
            else None
        ),
        stats_window_started_at=(
            safe_player_text(enrichment.stats_window_started_at, max_length=80)
            if enrichment and enrichment.stats_window_started_at
            else None
        ),
        stats_window_ended_at=(
            safe_player_text(enrichment.stats_window_ended_at, max_length=80)
            if enrichment and enrichment.stats_window_ended_at
            else None
        ),
        stats_reconnect_merged=bool(enrichment and enrichment.stats_reconnect_merged),
    )


def _matches_current_player(player: CurrentPlayerTableRow, query: str) -> bool:
    if not query:
        return True
    needle = query.casefold()
    return any(
        needle in value.casefold()
        for value in (
            player.display_name,
            player.reliable_id,
            player.source,
        )
    )


def load_current_players_page(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
    query: str = "",
) -> CurrentPlayersPage:
    """Return the live player roster without mutating persistent state."""
    normalized_instance = instance or paths.DEFAULT_INSTANCE_NAME
    normalized_query = normalize_player_query(query)
    result = player_current_cache.load_current_roster_snapshot(
        normalized_instance,
        data_root=data_root,
    )
    snapshot = result.snapshot

    reliable_ids = tuple(
        safe_player_text(player.reliable_id) for player in snapshot.players
    )
    enrichments = player_current_enrichment.load_current_player_enrichment(
        normalized_instance,
        data_root=data_root,
        reliable_ids=reliable_ids,
    )
    rows = tuple(
        _current_player_row(player, enrichments.get(safe_player_text(player.reliable_id)))
        for player in snapshot.players
    )
    filtered = tuple(row for row in rows if _matches_current_player(row, normalized_query))
    observed_count = (
        result.observed_count
        if result.observed_count is not None
        else snapshot.total_count
    )
    count_source = result.count_source or snapshot.count_source
    roster_available = (
        snapshot.roster_available
        if result.roster_available is None
        else result.roster_available
    )
    roster_configured = (
        snapshot.roster_configured
        if result.roster_configured is None
        else result.roster_configured
    )
    refresh_error = safe_player_text(
        result.refresh_error or snapshot.error,
        max_length=240,
    )
    return CurrentPlayersPage(
        instance=normalized_instance,
        query=normalized_query,
        available=snapshot.available,
        source=safe_player_text(snapshot.source) or "unavailable",
        status=safe_player_text(snapshot.status) or "unknown",
        error=refresh_error,
        refresh_error=refresh_error,
        collected_at=snapshot.collected_at,
        updated_at=snapshot.updated_at,
        age_seconds=result.age_seconds,
        is_stale=result.is_stale,
        freshness=(
            "stale"
            if result.is_stale
            else ("fresh" if snapshot.available else "unavailable")
        ),
        cache_status=safe_player_text(result.cache_status, max_length=40),
        observed_count=max(0, int(observed_count)),
        total_count=max(0, int(observed_count)),
        filtered_count=len(filtered),
        count_source=safe_player_text(count_source, max_length=80) or "unknown",
        roster_available=bool(roster_available),
        roster_configured=bool(roster_configured),
        stale_named_roster=bool(result.is_stale and snapshot.players),
        players=filtered,
    )


def load_player_registry_page(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
    query: str = "",
) -> PlayerRegistryPage:
    """Return known reliable players for the registry page."""
    normalized_instance = instance or paths.DEFAULT_INSTANCE_NAME
    normalized_query = normalize_player_query(query)
    registry_path = player_registry.player_registry_db_path(
        normalized_instance,
        data_root=data_root,
    )
    return PlayerRegistryPage(
        instance=normalized_instance,
        query=normalized_query,
        players=tuple(player_registry.list_known_players(registry_path, query=normalized_query)),
    )


def _normalize_history_limit(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return player_registry.DEFAULT_PLAYER_HISTORY_EVENT_LIMIT
    return max(1, min(parsed, player_registry.MAX_PLAYER_HISTORY_EVENT_LIMIT))


def _normalize_history_event_type(value: object) -> str:
    candidate = safe_player_text(value, max_length=80)
    if candidate in PLAYER_HISTORY_EVENT_TYPE_VALUES:
        return candidate
    return ""


def _normalize_history_mode(value: object) -> str:
    candidate = safe_player_text(value, max_length=80)
    if candidate in PLAYER_HISTORY_MODE_VALUES:
        return candidate
    return PLAYER_HISTORY_MODE_PLAYER_EVENTS


def _history_fallback_label(value: str) -> str:
    text = safe_player_text(value, max_length=80)
    if not text:
        return "Unknown"
    words = text.replace("_", " ").replace("-", " ").split()
    return " ".join(word.capitalize() for word in words) or "Unknown"


def _history_source_labels(source: str) -> tuple[str, ...]:
    parts = tuple(part for part in source.split("+") if part)
    if not parts:
        return ("Unknown",)
    return tuple(
        PLAYER_HISTORY_SOURCE_LABELS.get(part, _history_fallback_label(part))
        for part in parts
    )


def _history_label_value(
    labels: dict[str, str],
    value: str,
) -> tuple[str, ...]:
    if not value:
        return ()
    return (labels.get(value, _history_fallback_label(value)),)


def _history_text_field(
    label: str,
    value: object,
    *,
    max_length: int = 160,
) -> PlayerHistoryField | None:
    text = safe_player_text(value, max_length=max_length)
    if not text:
        return None
    return PlayerHistoryField(label=label, value=text, display_value=text)


def _history_id_field(label: str, value: object) -> PlayerHistoryField | None:
    text = safe_player_text(value, max_length=120)
    if not text:
        return None
    if len(text) > 18:
        display_value = f"{text[:8]}...{text[-5:]}"
    else:
        display_value = text
    return PlayerHistoryField(
        label=label,
        value=text,
        display_value=display_value,
        title=text,
        kind="id",
    )


def _history_labels_field(label: str, labels: tuple[str, ...]) -> PlayerHistoryField | None:
    clean_labels = tuple(label for label in labels if label)
    if not clean_labels:
        return None
    return PlayerHistoryField(label=label, kind="labels", value_labels=clean_labels)


def _history_time_field(label: str, value: object) -> PlayerHistoryField | None:
    text = safe_player_text(value, max_length=80)
    if not text:
        return None
    return PlayerHistoryField(label=label, value=text, kind="time")


def _history_distance_field(distance_m: float | None) -> PlayerHistoryField | None:
    if distance_m is None:
        return None
    return PlayerHistoryField(
        label="Distance",
        value=f"{distance_m:g}",
        display_value=f"{distance_m:g}",
        kind="distance",
    )


def _history_badges_field(event: player_registry.PlayerLogEventRecord) -> PlayerHistoryField | None:
    labels: list[str] = []
    if event.teamkill:
        labels.append("TK")
    if event.suicide:
        labels.append("Suicide")
    if event.ai_instigator:
        labels.append("AI")
    return _history_labels_field("Markers", tuple(labels))


def _add_history_field(
    fields: list[PlayerHistoryField],
    field: PlayerHistoryField | None,
) -> None:
    if field is not None:
        fields.append(field)


def _history_actor(event: player_registry.PlayerLogEventRecord) -> tuple[str, str]:
    if event.event_type == player_log_events.EVENT_TYPE_SERVER_LIFECYCLE:
        return "System", ""
    actor = event.player_name or event.victim_name or event.instigator_name
    if actor:
        return "", actor
    if event.event_type == player_log_events.EVENT_TYPE_PLAYER_DISCONNECTED:
        return "Correlation only", ""
    return "Unknown", ""


def _history_event_details(
    event: player_registry.PlayerLogEventRecord,
    *,
    mode: str,
) -> tuple[PlayerHistoryField, ...]:
    fields: list[PlayerHistoryField] = []
    event_type = event.event_type

    if event_type in {
        player_log_events.EVENT_TYPE_PLAYER_AUTHENTICATED,
        player_log_events.EVENT_TYPE_PLAYER_UPDATE,
    }:
        _add_history_field(fields, _history_id_field("Player ID", event.player_id))
        _add_history_field(
            fields,
            _history_labels_field("Source", _history_source_labels(event.source)),
        )
    elif event_type == player_log_events.EVENT_TYPE_FACTION_JOIN:
        _add_history_field(fields, _history_id_field("Player ID", event.player_id))
        _add_history_field(fields, _history_text_field("Faction", event.player_faction))
        _add_history_field(
            fields,
            _history_labels_field("Source", _history_source_labels(event.source)),
        )
    elif event_type == player_log_events.EVENT_TYPE_SERVER_LIFECYCLE:
        pass
    elif event_type == player_log_events.EVENT_TYPE_PLAYER_DISCONNECTED:
        if mode == PLAYER_HISTORY_MODE_SESSION_EVIDENCE:
            _add_history_field(fields, _history_text_field("RPL identity", event.rpl_identity))
            _add_history_field(fields, _history_text_field("Connection ID", event.connection_id))
            _add_history_field(fields, _history_text_field("BE slot", event.be_slot))
    elif event_type in PLAYER_HISTORY_COMBAT_EVENT_TYPES:
        _add_history_field(fields, _history_text_field("Victim", event.victim_name))
        if event.instigator_name and event.instigator_name != event.victim_name:
            _add_history_field(
                fields,
                _history_text_field("Instigator", event.instigator_name),
            )
        if event.victim_faction and event.instigator_faction:
            if event.victim_faction == event.instigator_faction:
                _add_history_field(fields, _history_text_field("Faction", event.victim_faction))
            else:
                _add_history_field(
                    fields,
                    _history_text_field("Victim faction", event.victim_faction),
                )
                _add_history_field(
                    fields,
                    _history_text_field("Instigator faction", event.instigator_faction),
                )
        else:
            _add_history_field(
                fields,
                _history_text_field("Faction", event.victim_faction or event.instigator_faction),
            )
        _add_history_field(fields, _history_text_field("Damage", event.damage_type))
        _add_history_field(fields, _history_text_field("Hit", event.hit_zone))
        _add_history_field(fields, _history_distance_field(event.distance_m))
        _add_history_field(fields, _history_badges_field(event))
    else:
        _add_history_field(fields, _history_id_field("Player ID", event.player_id))
        _add_history_field(
            fields,
            _history_labels_field("Source", _history_source_labels(event.source)),
        )

    return tuple(fields)


def _history_field_signature(field: PlayerHistoryField) -> tuple[str, str, tuple[str, ...], str]:
    return (field.label, field.value, field.value_labels, field.kind)


def _history_event_diagnostics(
    event: player_registry.PlayerLogEventRecord,
    *,
    details: tuple[PlayerHistoryField, ...],
    mode: str,
) -> tuple[PlayerHistoryField, ...]:
    fields: list[PlayerHistoryField] = []
    shown = {_history_field_signature(field) for field in details}

    def add(field: PlayerHistoryField | None) -> None:
        if field is None:
            return
        signature = _history_field_signature(field)
        if signature in shown:
            return
        shown.add(signature)
        fields.append(field)

    if event.player_id and event.player_id not in {event.victim_id, event.instigator_id}:
        add(_history_id_field("Player ID", event.player_id))
    add(_history_id_field("Victim ID", event.victim_id))
    add(_history_id_field("Instigator ID", event.instigator_id))
    if mode == PLAYER_HISTORY_MODE_SESSION_EVIDENCE:
        add(_history_text_field("Session player ID", event.session_player_id))
        add(_history_text_field("Victim session ID", event.victim_session_player_id))
        add(_history_text_field("Instigator session ID", event.instigator_session_player_id))
        add(_history_text_field("RPL identity", event.rpl_identity))
        add(_history_text_field("Connection ID", event.connection_id))
        add(_history_text_field("BE slot", event.be_slot))
    add(_history_labels_field("Source", _history_source_labels(event.source)))
    add(
        _history_labels_field(
            "Confidence",
            _history_label_value(PLAYER_HISTORY_CONFIDENCE_LABELS, event.confidence),
        )
    )
    add(
        _history_labels_field(
            "Time source",
            _history_label_value(PLAYER_HISTORY_TIME_SOURCE_LABELS, event.time_source),
        )
    )
    add(
        _history_labels_field(
            "Time confidence",
            _history_label_value(
                PLAYER_HISTORY_TIME_CONFIDENCE_LABELS,
                event.time_confidence,
            ),
        )
    )
    add(_history_text_field("Log timestamp", event.log_timestamp, max_length=80))
    if event.collected_at and event.collected_at != event.event_time:
        add(_history_time_field("Collected", event.collected_at))
    add(_history_text_field("Reference", event.source_ref, max_length=120))
    return tuple(fields)


def _player_history_event_row(
    event: player_registry.PlayerLogEventRecord,
    *,
    mode: str,
) -> PlayerHistoryEventRow:
    details = _history_event_details(event, mode=mode)
    actor_label, actor_value = _history_actor(event)
    return PlayerHistoryEventRow(
        record=event,
        label=PLAYER_HISTORY_EVENT_TYPE_LABELS.get(
            event.event_type,
            _history_fallback_label(event.event_type),
        ),
        actor_label=actor_label,
        actor_value=actor_value,
        details=details,
        diagnostics=_history_event_diagnostics(event, details=details, mode=mode),
    )


def _player_log_ingest_freshness_indicator(
    freshness: player_registry.PlayerLogIngestFreshness,
) -> PlayerLogIngestFreshnessIndicator:
    status = safe_player_text(freshness.status, max_length=40)
    return PlayerLogIngestFreshnessIndicator(
        status=status,
        status_label=PLAYER_LOG_INGEST_STATUS_LABELS.get(
            status,
            "No ingest status yet",
        ),
        updated_at=safe_player_text(freshness.updated_at, max_length=80),
        last_success_at=safe_player_text(freshness.last_success_at, max_length=80),
        scanned_files=max(0, int(freshness.scanned_files)),
        parsed_events=max(0, int(freshness.parsed_events)),
        stored_events=max(0, int(freshness.stored_events)),
        skipped_files=max(0, int(freshness.skipped_files)),
        skipped_reasons=safe_player_text(freshness.skipped_reasons, max_length=240),
        checkpoint_updated=bool(freshness.checkpoint_updated),
    )


def load_player_history_page(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
    query: str = "",
    event_type: str = "",
    mode: str = PLAYER_HISTORY_MODE_PLAYER_EVENTS,
    reliable_id: str = "",
    limit: object = player_registry.DEFAULT_PLAYER_HISTORY_EVENT_LIMIT,
) -> PlayerHistoryPage:
    """Return stored player log events for the history page."""
    normalized_instance = instance or paths.DEFAULT_INSTANCE_NAME
    normalized_query = normalize_player_query(query)
    normalized_event_type = _normalize_history_event_type(event_type)
    normalized_mode = _normalize_history_mode(mode)
    normalized_reliable_id = safe_player_text(reliable_id, max_length=120)
    normalized_limit = _normalize_history_limit(limit)
    registry_path = player_registry.player_registry_db_path(
        normalized_instance,
        data_root=data_root,
    )
    return PlayerHistoryPage(
        instance=normalized_instance,
        query=normalized_query,
        event_type=normalized_event_type,
        mode=normalized_mode,
        reliable_id=normalized_reliable_id,
        limit=normalized_limit,
        events=tuple(
            _player_history_event_row(event, mode=normalized_mode)
            for event in player_registry.list_player_log_events(
                registry_path,
                limit=normalized_limit,
                event_type=normalized_event_type,
                reliable_id=normalized_reliable_id,
                query=normalized_query,
                player_events_only=normalized_mode == PLAYER_HISTORY_MODE_PLAYER_EVENTS,
                session_evidence_only=normalized_mode == PLAYER_HISTORY_MODE_SESSION_EVIDENCE,
            )
        ),
        event_type_options=PLAYER_HISTORY_EVENT_TYPES,
        event_type_labels=PLAYER_HISTORY_EVENT_TYPE_LABELS,
        mode_options=PLAYER_HISTORY_MODE_OPTIONS,
        ingest_freshness=_player_log_ingest_freshness_indicator(
            player_registry.get_player_log_ingest_freshness(
                registry_path,
                scope=player_log_jobs.PLAYER_LOG_COLLECTION_SCOPE,
            )
        ),
    )


def _player_session_job_indicator(
    job: job_models.JobRecord,
) -> PlayerSessionJobIndicator | None:
    if job.kind not in PLAYER_SESSION_OPERATOR_JOB_KINDS:
        return None
    if job.status not in PLAYER_SESSION_ACTIVE_JOB_STATUSES:
        return None
    return PlayerSessionJobIndicator(
        job_id=int(job.id),
        kind=job.kind,
        kind_label=PLAYER_SESSION_JOB_KIND_LABELS.get(job.kind, "Session job"),
        status=job.status,
        status_label=PLAYER_SESSION_JOB_STATUS_LABELS.get(job.status, "Active"),
    )


def _load_active_player_session_jobs(
    web_db_path: Path | None,
) -> tuple[PlayerSessionJobIndicator, ...]:
    if web_db_path is None:
        return ()
    try:
        active_jobs = job_store.list_active_jobs(web_db_path, limit=25)
    except job_store.JobStoreError:
        return ()
    indicators = (_player_session_job_indicator(job) for job in active_jobs)
    return tuple(indicator for indicator in indicators if indicator is not None)


def _normalize_session_list_limit(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return player_registry.DEFAULT_PLAYER_SESSION_LIST_LIMIT
    return max(1, min(parsed, player_registry.MAX_PLAYER_SESSION_LIST_LIMIT))


def _session_list_query_items(
    *,
    query: str,
    reliable_id: str,
    status: str,
    end_reason: str,
    source: str,
    from_time: str,
    to_time: str,
    limit: int,
    before_time: str = "",
    before_session_id: str = "",
) -> list[tuple[str, str]]:
    values = (
        ("q", query),
        ("player_id", reliable_id),
        ("status", status),
        ("end_reason", end_reason),
        ("source", source),
        ("from", from_time),
        ("to", to_time),
        ("limit", str(limit)),
        ("before_time", before_time),
        ("before_session_id", before_session_id),
    )
    return [(key, value) for key, value in values if value]


def _query_url(path: str, query_items: list[tuple[str, str]]) -> str:
    query_string = urlencode(query_items)
    return f"{path}?{query_string}" if query_string else path


def _safe_timeline_source_labels(source: str) -> tuple[str, ...]:
    labels: list[str] = []
    for part in (part for part in source.split("+") if part):
        label = PLAYER_HISTORY_SOURCE_LABELS.get(part, "Other evidence")
        if label not in labels:
            labels.append(label)
    return tuple(labels)


def _timeline_participants(
    event: player_session_details.PlayerSessionTimelineItem,
) -> tuple[PlayerSessionTimelineParticipant, ...]:
    participants: list[PlayerSessionTimelineParticipant] = []
    player_name = safe_player_text(event.player_name, max_length=160)
    victim_name = safe_player_text(event.victim_name, max_length=160)
    instigator_name = safe_player_text(event.instigator_name, max_length=160)
    if player_name:
        participants.append(
            PlayerSessionTimelineParticipant(label="Player", name=player_name)
        )
    if victim_name:
        participants.append(
            PlayerSessionTimelineParticipant(label="Victim", name=victim_name)
        )
    if instigator_name and not (
        event.suicide and instigator_name == victim_name
    ):
        participants.append(
            PlayerSessionTimelineParticipant(
                label="Instigator",
                name=instigator_name,
            )
        )
    return tuple(participants)


def _timeline_details(
    event: player_session_details.PlayerSessionTimelineItem,
) -> tuple[PlayerHistoryField, ...]:
    fields: list[PlayerHistoryField] = []

    def add(field: PlayerHistoryField | None) -> None:
        _add_history_field(fields, field)

    add(_history_text_field("Faction evidence", event.player_faction))
    if (
        event.victim_faction
        and event.instigator_faction
        and event.victim_faction == event.instigator_faction
    ):
        add(_history_text_field("Faction evidence", event.victim_faction))
    else:
        add(_history_text_field("Victim faction", event.victim_faction))
        add(_history_text_field("Instigator faction", event.instigator_faction))
    add(_history_text_field("Damage", event.damage_type))
    add(_history_text_field("Hit", event.hit_zone))
    add(_history_distance_field(event.distance_m))
    markers = tuple(
        label
        for enabled, label in (
            (event.teamkill, "TK"),
            (event.suicide, "Suicide"),
            (event.ai_instigator, "AI"),
        )
        if enabled
    )
    add(_history_labels_field("Markers", markers))
    return tuple(fields)


def _timeline_diagnostics(
    event: player_session_details.PlayerSessionTimelineItem,
) -> tuple[PlayerHistoryField, ...]:
    fields: list[PlayerHistoryField] = []
    _add_history_field(
        fields,
        _history_labels_field(
            "Source",
            _safe_timeline_source_labels(event.source),
        ),
    )
    confidence = PLAYER_HISTORY_CONFIDENCE_LABELS.get(
        event.confidence,
        "Unknown confidence",
    )
    if event.confidence:
        _add_history_field(
            fields,
            _history_labels_field("Confidence", (confidence,)),
        )
    return tuple(fields)


def _timeline_row(
    event: player_session_details.PlayerSessionTimelineItem,
) -> PlayerSessionTimelineRow:
    return PlayerSessionTimelineRow(
        event_time=safe_player_text(event.occurred_at, max_length=80),
        event_label=PLAYER_HISTORY_EVENT_TYPE_LABELS.get(
            event.event_type,
            "Other player event",
        ),
        participants=_timeline_participants(event),
        participant_fallback="Recorded player evidence",
        details=_timeline_details(event),
        diagnostics=_timeline_diagnostics(event),
    )


def load_player_sessions_page(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
    query: str = "",
    reliable_id: str = "",
    status: str = "",
    end_reason: str = "",
    source: str = "",
    from_time: str = "",
    to_time: str = "",
    before_time: str = "",
    before_session_id: str = "",
    limit: object = player_registry.DEFAULT_PLAYER_SESSION_LIST_LIMIT,
    web_db_path: Path | None = None,
) -> PlayerSessionsPage:
    """Return stored player sessions without mutating persistent state."""
    normalized_instance = instance or paths.DEFAULT_INSTANCE_NAME
    normalized_query = normalize_player_query(query)
    normalized_reliable_id = safe_player_text(reliable_id, max_length=120)
    normalized_status = safe_player_text(status, max_length=40)
    normalized_end_reason = safe_player_text(end_reason, max_length=80)
    normalized_source = safe_player_text(source, max_length=80)
    normalized_from_time = safe_player_text(from_time, max_length=80)
    normalized_to_time = safe_player_text(to_time, max_length=80)
    normalized_before_time = safe_player_text(before_time, max_length=80)
    normalized_before_session_id = safe_player_text(
        before_session_id,
        max_length=32,
    )
    normalized_limit = _normalize_session_list_limit(limit)
    registry_path = player_registry.player_registry_db_path(
        normalized_instance,
        data_root=data_root,
    )
    search_result = player_session_details.search_player_sessions(
        registry_path,
        limit=normalized_limit,
        reliable_id=normalized_reliable_id,
        query=normalized_query,
        status=normalized_status,
        end_reason=normalized_end_reason,
        source=normalized_source,
        before_time=normalized_before_time,
        before_session_id=normalized_before_session_id,
        from_time=normalized_from_time,
        to_time=normalized_to_time,
    )
    current_query_items = _session_list_query_items(
        query=normalized_query,
        reliable_id=normalized_reliable_id,
        status=normalized_status,
        end_reason=normalized_end_reason,
        source=normalized_source,
        from_time=normalized_from_time,
        to_time=normalized_to_time,
        limit=search_result.limit,
        before_time=normalized_before_time,
        before_session_id=normalized_before_session_id,
    )
    refresh_url = _query_url(
        "/players/sessions",
        _session_list_query_items(
            query=normalized_query,
            reliable_id=normalized_reliable_id,
            status=normalized_status,
            end_reason=normalized_end_reason,
            source=normalized_source,
            from_time=normalized_from_time,
            to_time=normalized_to_time,
            limit=search_result.limit,
        ),
    )
    next_page_url = ""
    if search_result.next_cursor is not None:
        next_page_url = _query_url(
            "/players/sessions",
            _session_list_query_items(
                query=normalized_query,
                reliable_id=normalized_reliable_id,
                status=normalized_status,
                end_reason=normalized_end_reason,
                source=normalized_source,
                from_time=normalized_from_time,
                to_time=normalized_to_time,
                limit=search_result.limit,
                before_time=search_result.next_cursor.evidence_time,
                before_session_id=str(search_result.next_cursor.session_id),
            ),
        )
    return PlayerSessionsPage(
        instance=normalized_instance,
        query=normalized_query,
        reliable_id=normalized_reliable_id,
        status=normalized_status,
        end_reason=normalized_end_reason,
        source=normalized_source,
        from_time=normalized_from_time,
        to_time=normalized_to_time,
        before_time=normalized_before_time,
        before_session_id=normalized_before_session_id,
        limit=search_result.limit,
        search_status=search_result.status,
        sessions=search_result.items,
        summary=player_registry.summarize_player_sessions(registry_path),
        active_jobs=_load_active_player_session_jobs(web_db_path),
        refresh_url=refresh_url,
        next_page_url=next_page_url,
        session_detail_urls={
            session.session_id: _query_url(
                f"/players/sessions/{session.session_id}",
                current_query_items,
            )
            for session in search_result.items
        },
        has_filters=any(
            (
                normalized_query,
                normalized_reliable_id,
                normalized_status,
                normalized_end_reason,
                normalized_source,
                normalized_from_time,
                normalized_to_time,
                normalized_before_time,
                normalized_before_session_id,
            )
        ),
        filters_active=any(
            (
                normalized_query,
                normalized_reliable_id,
                normalized_status,
                normalized_end_reason,
                normalized_source,
                normalized_from_time,
                normalized_to_time,
            )
        ),
        status_options=PLAYER_SESSION_STATUS_OPTIONS,
        status_labels=PLAYER_SESSION_STATUS_LABELS,
        end_reason_options=PLAYER_SESSION_END_REASON_OPTIONS,
        end_reason_labels=PLAYER_SESSION_END_REASON_LABELS,
        source_options=PLAYER_SESSION_SOURCE_OPTIONS,
        source_labels=PLAYER_SESSION_SOURCE_LABELS,
        confidence_labels=PLAYER_SESSION_CONFIDENCE_LABELS,
    )


def load_player_session_detail_page(
    session_id: object,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
    query: str = "",
    reliable_id: str = "",
    status: str = "",
    end_reason: str = "",
    source: str = "",
    from_time: str = "",
    to_time: str = "",
    limit: object = player_registry.DEFAULT_PLAYER_SESSION_LIST_LIMIT,
    before_time: str = "",
    before_session_id: str = "",
    timeline_limit: object = PLAYER_SESSION_TIMELINE_DEFAULT_LIMIT,
    timeline_before_time: str = "",
    timeline_before_event_id: str = "",
    now_at: str | None = None,
) -> PlayerSessionDetailPage:
    """Integrate one query-only detail and bounded timeline for templates."""
    normalized_instance = instance or paths.DEFAULT_INSTANCE_NAME
    normalized_query = normalize_player_query(query)
    normalized_reliable_id = safe_player_text(reliable_id, max_length=120)
    normalized_status = safe_player_text(status, max_length=40)
    normalized_end_reason = safe_player_text(end_reason, max_length=80)
    normalized_source = safe_player_text(source, max_length=80)
    normalized_from_time = safe_player_text(from_time, max_length=80)
    normalized_to_time = safe_player_text(to_time, max_length=80)
    normalized_before_time = safe_player_text(before_time, max_length=80)
    normalized_before_session_id = safe_player_text(
        before_session_id,
        max_length=32,
    )
    normalized_limit = _normalize_session_list_limit(limit)
    normalized_timeline_limit = (
        PLAYER_SESSION_TIMELINE_DEFAULT_LIMIT
        if timeline_limit in ("", None)
        else _normalize_history_limit(timeline_limit)
    )
    normalized_timeline_before_time = safe_player_text(
        timeline_before_time,
        max_length=80,
    )
    normalized_timeline_before_event_id = safe_player_text(
        timeline_before_event_id,
        max_length=32,
    )
    list_query_items = _session_list_query_items(
        query=normalized_query,
        reliable_id=normalized_reliable_id,
        status=normalized_status,
        end_reason=normalized_end_reason,
        source=normalized_source,
        from_time=normalized_from_time,
        to_time=normalized_to_time,
        limit=normalized_limit,
        before_time=normalized_before_time,
        before_session_id=normalized_before_session_id,
    )
    back_url = _query_url("/players/sessions", list_query_items)
    registry_path = player_registry.player_registry_db_path(
        normalized_instance,
        data_root=data_root,
    )
    detail_result = player_session_details.load_player_session_detail(
        registry_path,
        session_id,
        now_at=now_at,
    )
    timeline_status = player_session_details.PLAYER_SESSION_TIMELINE_STATUS_UNAVAILABLE
    timeline_rows: tuple[PlayerSessionTimelineRow, ...] = ()
    timeline_unavailable_reason = ""
    older_timeline_url = ""
    if (
        detail_result.status
        == player_session_details.PLAYER_SESSION_DETAIL_STATUS_OK
        and detail_result.detail is not None
    ):
        timeline_result = player_session_details.load_player_session_timeline(
            registry_path,
            session_id,
            limit=normalized_timeline_limit,
            before_time=normalized_timeline_before_time,
            before_event_id=normalized_timeline_before_event_id,
            now_at=now_at,
        )
        timeline_status = timeline_result.status
        timeline_rows = tuple(
            _timeline_row(event) for event in timeline_result.items
        )
        timeline_unavailable_reason = safe_player_text(
            timeline_result.unavailable_reason,
            max_length=240,
        )
        if timeline_result.next_cursor is not None:
            timeline_query_items = [
                *list_query_items,
                ("timeline_limit", str(timeline_result.limit)),
                (
                    "timeline_before_time",
                    timeline_result.next_cursor.event_time,
                ),
                (
                    "timeline_before_event_id",
                    str(timeline_result.next_cursor.event_id),
                ),
            ]
            older_timeline_url = _query_url(
                f"/players/sessions/{detail_result.detail.session_id}",
                timeline_query_items,
            )
    return PlayerSessionDetailPage(
        instance=normalized_instance,
        status=detail_result.status,
        detail=detail_result.detail,
        timeline_status=timeline_status,
        timeline_rows=timeline_rows,
        timeline_unavailable_reason=timeline_unavailable_reason,
        back_url=back_url,
        older_timeline_url=older_timeline_url,
        status_labels=PLAYER_SESSION_STATUS_LABELS,
        state_explanations=PLAYER_SESSION_STATE_EXPLANATIONS,
        end_reason_labels=PLAYER_SESSION_END_REASON_LABELS,
        source_labels=PLAYER_SESSION_SOURCE_LABELS,
        confidence_labels=PLAYER_SESSION_CONFIDENCE_LABELS,
        freshness_labels=PLAYER_SESSION_FRESHNESS_LABELS,
    )
