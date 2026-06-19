"""Current-player collection sources for web moderation workflows."""

from __future__ import annotations

from dataclasses import dataclass

from armactl import discovery, paths, player_view
from armactl.rcon import PlayerEntry
from armactl.web.services.player_identity import (
    normalize_reliable_player_id,
    safe_player_text,
)

PLAYER_ROSTER_TIMEOUT_SECONDS = 0.35
PLAYER_A2S_TIMEOUT_SECONDS = 0.35


@dataclass(frozen=True)
class CurrentPlayer:
    """One sanitized current-player observation from a live source."""

    display_name: str
    reliable_id: str
    admin_reference: str
    source: str


@dataclass(frozen=True)
class CurrentPlayerRoster:
    """Sanitized current-player roster collected from the active server."""

    available: bool
    players: tuple[CurrentPlayer, ...]
    total_count: int
    source: str
    status: str
    error: str = ""


def _current_player(entry: PlayerEntry) -> CurrentPlayer:
    reliable_id = normalize_reliable_player_id(entry.guid)
    return CurrentPlayer(
        display_name=safe_player_text(entry.name) or "Unknown player",
        reliable_id=reliable_id,
        admin_reference=reliable_id,
        source="rcon.guid" if reliable_id else "rcon.roster",
    )


def load_current_player_roster(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> CurrentPlayerRoster:
    """Collect current players from player_view/RCON without persistence or UI DTOs."""
    normalized_instance = instance or paths.DEFAULT_INSTANCE_NAME
    state = discovery.discover(instance=normalized_instance, save=False)
    view = player_view.query_player_view(
        normalized_instance,
        timeout=PLAYER_A2S_TIMEOUT_SECONDS,
        roster_timeout=PLAYER_ROSTER_TIMEOUT_SECONDS,
        state=state,
        include_roster=True,
    )

    players = tuple(_current_player(entry) for entry in view.entries)
    source = "rcon.roster" if view.roster_available else view.count_source
    error = view.roster_error or view.a2s_error
    return CurrentPlayerRoster(
        available=view.available,
        players=players,
        total_count=len(players),
        source=safe_player_text(source),
        status="available" if view.available else "unavailable",
        error=safe_player_text(error),
    )
