"""Unified player status view shared by Telegram and TUI."""

from __future__ import annotations

from dataclasses import dataclass

from armactl.a2s import query_player_status
from armactl.rcon import PlayerEntry, query_player_roster
from armactl.state import ServerState


@dataclass
class PlayerView:
    """Resolved player state from A2S count plus optional RCON roster details."""

    available: bool
    current: int | None
    max_players: int | None
    entries: tuple[PlayerEntry, ...] = ()
    count_source: str = "a2s"
    a2s_available: bool = False
    a2s_count: int | None = None
    a2s_error: str = ""
    roster_available: bool = False
    roster_configured: bool = False
    roster_error: str = ""
    warning: str = ""

    @property
    def player_lines(self) -> list[str]:
        """Return player names for simple renderers."""
        return [entry.name for entry in self.entries]


def query_player_view(
    instance: str,
    *,
    timeout: float = 1.5,
    roster_timeout: float = 1.5,
    state: ServerState | None = None,
    include_roster: bool = True,
) -> PlayerView:
    """Return one player view to avoid Telegram/TUI mixing inconsistent sources.

    A2S is still used for maxPlayers and as a fallback count. When local RCON is
    available, the RCON roster is the source of truth for the current player
    count, because it contains concrete player rows and avoids stale A2S ghosts.
    """
    player_status = query_player_status(instance, timeout=timeout, state=state)
    current = player_status.player_count
    max_players = player_status.max_players
    entries: tuple[PlayerEntry, ...] = ()
    count_source = "a2s"
    roster_available = False
    roster_configured = False
    roster_error = ""
    warning = ""

    resolved_state = state
    server_running = True if resolved_state is None else resolved_state.server_running

    if include_roster and server_running:
        roster = query_player_roster(instance, timeout=roster_timeout)
        roster_available = roster.available
        roster_configured = roster.configured
        roster_error = roster.error

        if roster.available:
            entries = tuple(roster.entries)
            rcon_count = len(entries)
            if current is not None and current != rcon_count:
                warning = (
                    f"A2S reports {current} player(s), "
                    f"but RCON roster has {rcon_count}."
                )
            current = rcon_count
            count_source = "rcon"

    return PlayerView(
        available=player_status.available or roster_available,
        current=current,
        max_players=max_players,
        entries=entries,
        count_source=count_source,
        a2s_available=player_status.available,
        a2s_count=player_status.player_count,
        a2s_error=player_status.error,
        roster_available=roster_available,
        roster_configured=roster_configured,
        roster_error=roster_error,
        warning=warning,
    )
