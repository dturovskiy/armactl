"""Player registry and moderation page DTO loaders."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from armactl import paths, player_log_events
from armactl.web.jobs import models as job_models
from armactl.web.jobs import player_sessions as player_session_jobs
from armactl.web.jobs import store as job_store
from armactl.web.services import player_current_cache, player_registry, player_sources
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

PLAYER_SESSION_STATUS_LABELS = {
    player_registry.PLAYER_SESSION_STATUS_OPEN: "Stored open",
    player_registry.PLAYER_SESSION_STATUS_CLOSED: "Stored closed",
}
PLAYER_SESSION_STATUS_OPTIONS = (
    ("", "All statuses"),
    *PLAYER_SESSION_STATUS_LABELS.items(),
)
PLAYER_SESSION_STATUS_VALUES = frozenset(
    status for status, _label in PLAYER_SESSION_STATUS_OPTIONS if status
)
PLAYER_SESSION_END_REASON_LABELS = {
    player_registry.PLAYER_SESSION_END_REASON_DISCONNECT: "Disconnect",
    player_registry.PLAYER_SESSION_END_REASON_SERVER_BOUNDARY: "Server boundary",
    player_registry.PLAYER_SESSION_END_REASON_STALE_TIMEOUT: "Stale timeout",
    player_registry.PLAYER_SESSION_END_REASON_STALE_ABSENCE: "Stale absence",
    player_registry.PLAYER_SESSION_END_REASON_SCANNER_CHECKPOINT: "Scanner checkpoint",
    player_registry.PLAYER_SESSION_END_REASON_IMPORT_WINDOW: "Import window",
    player_registry.PLAYER_SESSION_END_REASON_UNKNOWN: "Unknown",
}
PLAYER_SESSION_END_REASON_OPTIONS = (
    ("", "All end reasons"),
    *PLAYER_SESSION_END_REASON_LABELS.items(),
)
PLAYER_SESSION_END_REASON_VALUES = frozenset(
    reason for reason, _label in PLAYER_SESSION_END_REASON_OPTIONS if reason
)
PLAYER_SESSION_SOURCE_LABELS = {
    player_registry.PLAYER_SESSION_SOURCE_BACKEND_AUTH: "Backend auth",
    player_registry.PLAYER_SESSION_SOURCE_NETWORK_PLAYER_UPDATE: "Network player update",
    player_registry.PLAYER_SESSION_SOURCE_RCON_ROSTER: "RCON roster",
    player_registry.PLAYER_SESSION_SOURCE_SCRIPT_FACTION_JOIN: "Faction event",
    player_registry.PLAYER_SESSION_SOURCE_SCRIPT_KILL: "Combat event",
    player_registry.PLAYER_SESSION_SOURCE_SERVER_ADMIN_TOOLS_KILL: "ServerAdminTools event",
    player_registry.PLAYER_SESSION_SOURCE_SERVICE_LIFECYCLE: "Service lifecycle",
    player_registry.PLAYER_SESSION_SOURCE_SCANNER_CHECKPOINT: "Scanner checkpoint",
    player_registry.PLAYER_SESSION_SOURCE_MANUAL_IMPORT: "Manual import",
}
PLAYER_SESSION_SOURCE_OPTIONS = (
    ("", "All sources"),
    *PLAYER_SESSION_SOURCE_LABELS.items(),
)
PLAYER_SESSION_SOURCE_VALUES = frozenset(
    source for source, _label in PLAYER_SESSION_SOURCE_OPTIONS if source
)
PLAYER_SESSION_CONFIDENCE_LABELS = {
    player_registry.PLAYER_SESSION_CONFIDENCE_HIGH: "High",
    player_registry.PLAYER_SESSION_CONFIDENCE_MEDIUM: "Medium",
    player_registry.PLAYER_SESSION_CONFIDENCE_LOW: "Low",
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
    """One live current-roster row without session-derived claims."""

    display_name: str
    reliable_id: str
    source: str


@dataclass(frozen=True)
class CurrentPlayersPage:
    """Read-only current-player roster page model."""

    instance: str
    query: str
    available: bool
    source: str
    status: str
    error: str
    collected_at: str
    updated_at: str
    age_seconds: int | None
    is_stale: bool
    cache_status: str
    observed_count: int
    total_count: int
    filtered_count: int
    count_source: str
    roster_available: bool
    roster_configured: bool
    players: tuple[CurrentPlayerTableRow, ...]


@dataclass(frozen=True)
class PlayerHistoryPage:
    """Read-only stored player event history page model."""

    instance: str
    query: str
    event_type: str
    mode: str
    reliable_id: str
    limit: int
    events: tuple[player_registry.PlayerLogEventRecord, ...]
    event_type_options: tuple[tuple[str, str], ...]
    event_type_labels: dict[str, str]
    mode_options: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class PlayerSessionJobIndicator:
    """Safe active job indicator for player-session operator UX."""

    job_id: int
    kind: str
    status: str
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
    limit: int
    sessions: tuple[player_registry.PlayerSessionRecord, ...]
    summary: player_registry.PlayerSessionSummary
    active_jobs: tuple[PlayerSessionJobIndicator, ...]
    status_options: tuple[tuple[str, str], ...]
    status_labels: dict[str, str]
    end_reason_options: tuple[tuple[str, str], ...]
    end_reason_labels: dict[str, str]
    source_options: tuple[tuple[str, str], ...]
    source_labels: dict[str, str]
    confidence_labels: dict[str, str]


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
) -> CurrentPlayerTableRow:
    return CurrentPlayerTableRow(
        display_name=safe_player_text(player.display_name) or "Unknown player",
        reliable_id=safe_player_text(player.reliable_id),
        source=safe_player_text(player.source) or "unknown",
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

    rows = tuple(_current_player_row(player) for player in snapshot.players)
    filtered = tuple(row for row in rows if _matches_current_player(row, normalized_query))
    return CurrentPlayersPage(
        instance=normalized_instance,
        query=normalized_query,
        available=snapshot.available,
        source=safe_player_text(snapshot.source) or "unavailable",
        status=safe_player_text(snapshot.status) or "unknown",
        error=safe_player_text(result.refresh_error or snapshot.error),
        collected_at=snapshot.collected_at,
        updated_at=snapshot.updated_at,
        age_seconds=result.age_seconds,
        is_stale=result.is_stale,
        cache_status=safe_player_text(result.cache_status, max_length=40),
        observed_count=snapshot.total_count,
        total_count=snapshot.total_count,
        filtered_count=len(filtered),
        count_source=safe_player_text(snapshot.count_source, max_length=80) or "unknown",
        roster_available=bool(snapshot.roster_available),
        roster_configured=bool(snapshot.roster_configured),
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


def _normalize_session_status(value: object) -> str:
    candidate = safe_player_text(value, max_length=40)
    if candidate in PLAYER_SESSION_STATUS_VALUES:
        return candidate
    return ""


def _normalize_session_end_reason(value: object) -> str:
    candidate = safe_player_text(value, max_length=80)
    if candidate in PLAYER_SESSION_END_REASON_VALUES:
        return candidate
    return ""


def _normalize_session_source(value: object) -> str:
    candidate = safe_player_text(value, max_length=80)
    if candidate in PLAYER_SESSION_SOURCE_VALUES:
        return candidate
    return ""


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
            player_registry.list_player_log_events(
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
        status=job.status,
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


def load_player_sessions_page(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
    query: str = "",
    reliable_id: str = "",
    status: str = "",
    end_reason: str = "",
    source: str = "",
    limit: object = player_registry.DEFAULT_PLAYER_SESSION_LIST_LIMIT,
    web_db_path: Path | None = None,
) -> PlayerSessionsPage:
    """Return stored player sessions without mutating persistent state."""
    normalized_instance = instance or paths.DEFAULT_INSTANCE_NAME
    normalized_query = normalize_player_query(query)
    normalized_reliable_id = safe_player_text(reliable_id, max_length=120)
    normalized_status = _normalize_session_status(status)
    normalized_end_reason = _normalize_session_end_reason(end_reason)
    normalized_source = _normalize_session_source(source)
    try:
        normalized_limit = int(limit)
    except (TypeError, ValueError):
        normalized_limit = player_registry.DEFAULT_PLAYER_SESSION_LIST_LIMIT
    normalized_limit = max(
        1,
        min(normalized_limit, player_registry.MAX_PLAYER_SESSION_LIST_LIMIT),
    )
    registry_path = player_registry.player_registry_db_path(
        normalized_instance,
        data_root=data_root,
    )
    return PlayerSessionsPage(
        instance=normalized_instance,
        query=normalized_query,
        reliable_id=normalized_reliable_id,
        status=normalized_status,
        end_reason=normalized_end_reason,
        source=normalized_source,
        limit=normalized_limit,
        sessions=tuple(
            player_registry.list_player_sessions(
                registry_path,
                limit=normalized_limit,
                reliable_id=normalized_reliable_id,
                query=normalized_query,
                status=normalized_status,
                end_reason=normalized_end_reason,
                source=normalized_source,
            )
        ),
        summary=player_registry.summarize_player_sessions(registry_path),
        active_jobs=_load_active_player_session_jobs(web_db_path),
        status_options=PLAYER_SESSION_STATUS_OPTIONS,
        status_labels=PLAYER_SESSION_STATUS_LABELS,
        end_reason_options=PLAYER_SESSION_END_REASON_OPTIONS,
        end_reason_labels=PLAYER_SESSION_END_REASON_LABELS,
        source_options=PLAYER_SESSION_SOURCE_OPTIONS,
        source_labels=PLAYER_SESSION_SOURCE_LABELS,
        confidence_labels=PLAYER_SESSION_CONFIDENCE_LABELS,
    )
