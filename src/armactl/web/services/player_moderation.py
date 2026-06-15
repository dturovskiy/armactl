"""Safe player/moderation DTOs for the web panel."""

from __future__ import annotations

from dataclasses import dataclass

from armactl import admins_manager, discovery, paths, player_view
from armactl.rcon import PlayerEntry
from armactl.redaction import redact_sensitive_text

PLAYER_QUERY_MAX_LENGTH = 120
PLAYER_TEXT_MAX_LENGTH = 160
PLAYER_ROSTER_TIMEOUT_SECONDS = 0.35
PLAYER_A2S_TIMEOUT_SECONDS = 0.35


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


def _safe_text(value: object, *, max_length: int = PLAYER_TEXT_MAX_LENGTH) -> str:
    text = redact_sensitive_text(value).replace("\r", " ").replace("\n", " ").strip()
    if len(text) > max_length:
        return f"{text[:max_length]}..."
    return text


def _normalize_query(value: object) -> str:
    return _safe_text(value, max_length=PLAYER_QUERY_MAX_LENGTH)


def _admin_reference(entry: PlayerEntry) -> str:
    candidate = str(entry.guid or "").strip()
    if not candidate:
        return ""
    if admins_manager.STEAM_ID64_RE.fullmatch(candidate):
        return candidate
    if admins_manager.IDENTITY_ID_RE.fullmatch(candidate):
        return candidate
    return ""


def _moderation_player(entry: PlayerEntry) -> ModerationPlayer:
    reference = _admin_reference(entry)
    display_name = _safe_text(entry.name) or "Unknown player"
    return ModerationPlayer(
        display_name=display_name,
        identity_id=reference,
        admin_reference=reference,
        source="rcon.guid" if reference else "rcon.roster",
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
    normalized_instance = instance or paths.DEFAULT_INSTANCE_NAME
    normalized_query = _normalize_query(query)
    try:
        state = discovery.discover(instance=normalized_instance, save=False)
        view = player_view.query_player_view(
            normalized_instance,
            timeout=PLAYER_A2S_TIMEOUT_SECONDS,
            roster_timeout=PLAYER_ROSTER_TIMEOUT_SECONDS,
            state=state,
            include_roster=True,
        )
    except Exception as error:
        return PlayerModerationPanel(
            available=False,
            query=normalized_query,
            players=(),
            total_count=0,
            filtered_count=0,
            source="unavailable",
            status="unavailable",
            error=_safe_text(error),
        )

    players = tuple(_moderation_player(entry) for entry in view.entries)
    filtered = tuple(player for player in players if _matches_query(player, normalized_query))
    source = "rcon.roster" if view.roster_available else view.count_source
    error = view.roster_error or view.a2s_error
    return PlayerModerationPanel(
        available=view.available,
        query=normalized_query,
        players=filtered,
        total_count=len(players),
        filtered_count=len(filtered),
        source=_safe_text(source),
        status="available" if view.available else "unavailable",
        error=_safe_text(error),
    )
