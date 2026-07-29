"""Current-player collection sources for web moderation workflows."""

from __future__ import annotations

from dataclasses import dataclass

from armactl import discovery, paths, player_view
from armactl.rcon import PlayerEntry
from armactl.web.services.player_identity import (
    normalize_admin_reference,
    normalize_reliable_player_id,
    safe_player_text,
)

PLAYER_ROSTER_TIMEOUT_SECONDS = 1.5
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
    observed_count: int | None = None
    count_source: str = "unknown"
    roster_available: bool = False
    roster_configured: bool = False
    rcon_status: str = "unavailable"
    roster_source: str = "unknown"
    query_attempt_count: int = 0
    duplicate_query_attempts: bool = False
    count_mismatch: bool = False


def _safe_count(value: object, *, fallback: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = fallback
    return max(0, parsed)


def _current_player(entry: PlayerEntry) -> CurrentPlayer:
    reliable_id = normalize_reliable_player_id(entry.guid)
    admin_reference = normalize_admin_reference(entry.guid)
    return CurrentPlayer(
        display_name=safe_player_text(entry.name) or "Unknown player",
        reliable_id=reliable_id,
        admin_reference=admin_reference,
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
    observed_count = _safe_count(view.current, fallback=len(players))
    count_source = safe_player_text(view.count_source, max_length=80) or "unknown"
    source = "rcon.roster" if view.roster_available else view.count_source
    error = view.roster_error or view.a2s_error
    return CurrentPlayerRoster(
        available=view.available,
        players=players,
        total_count=observed_count,
        source=safe_player_text(source, max_length=80),
        status="available" if view.available else "unavailable",
        error=safe_player_text(error),
        observed_count=observed_count,
        count_source=count_source,
        roster_available=bool(view.roster_available),
        roster_configured=bool(view.roster_configured),
        rcon_status="ok" if view.roster_available else "unavailable",
        roster_source="rcon" if view.roster_available else "unknown",
        query_attempt_count=1,
        duplicate_query_attempts=False,
        count_mismatch=bool(view.warning),
    )
