"""Player registry and moderation page DTO loaders."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from armactl import paths, player_log_events
from armactl.web.services import player_registry, player_sources
from armactl.web.services.player_identity import normalize_player_query, safe_player_text

PLAYER_HISTORY_EVENT_TYPE_LABELS = {
    player_log_events.EVENT_TYPE_PLAYER_AUTHENTICATED: "Authenticated",
    player_log_events.EVENT_TYPE_PLAYER_UPDATE: "Player update",
    player_log_events.EVENT_TYPE_FACTION_JOIN: "Faction join",
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
    total_count: int
    filtered_count: int
    players: tuple[CurrentPlayerTableRow, ...]


@dataclass(frozen=True)
class PlayerHistoryPage:
    """Read-only stored player event history page model."""

    instance: str
    query: str
    event_type: str
    reliable_id: str
    limit: int
    events: tuple[player_registry.PlayerLogEventRecord, ...]
    event_type_options: tuple[tuple[str, str], ...]
    event_type_labels: dict[str, str]


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
    player: player_sources.CurrentPlayer,
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
    try:
        roster = player_sources.load_current_player_roster(normalized_instance)
    except Exception as error:  # noqa: BLE001 - current-player UI degrades safely.
        return CurrentPlayersPage(
            instance=normalized_instance,
            query=normalized_query,
            available=False,
            source="unavailable",
            status="unavailable",
            error=safe_player_text(error),
            total_count=0,
            filtered_count=0,
            players=(),
        )

    rows = tuple(_current_player_row(player) for player in roster.players)
    filtered = tuple(row for row in rows if _matches_current_player(row, normalized_query))
    return CurrentPlayersPage(
        instance=normalized_instance,
        query=normalized_query,
        available=roster.available,
        source=safe_player_text(roster.source) or "unavailable",
        status=safe_player_text(roster.status) or "unknown",
        error=safe_player_text(roster.error),
        total_count=roster.total_count,
        filtered_count=len(filtered),
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


def load_player_history_page(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
    query: str = "",
    event_type: str = "",
    reliable_id: str = "",
    limit: object = player_registry.DEFAULT_PLAYER_HISTORY_EVENT_LIMIT,
) -> PlayerHistoryPage:
    """Return stored player log events for the history page."""
    normalized_instance = instance or paths.DEFAULT_INSTANCE_NAME
    normalized_query = normalize_player_query(query)
    normalized_event_type = _normalize_history_event_type(event_type)
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
        reliable_id=normalized_reliable_id,
        limit=normalized_limit,
        events=tuple(
            player_registry.list_player_log_events(
                registry_path,
                limit=normalized_limit,
                event_type=normalized_event_type,
                reliable_id=normalized_reliable_id,
                query=normalized_query,
            )
        ),
        event_type_options=PLAYER_HISTORY_EVENT_TYPES,
        event_type_labels=PLAYER_HISTORY_EVENT_TYPE_LABELS,
    )
