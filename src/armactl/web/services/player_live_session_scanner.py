"""Explicit one-shot live current-roster session scanner."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from armactl import paths
from armactl.web.services import player_registry, player_sources
from armactl.web.services.player_identity import normalize_reliable_player_id

LIVE_SESSION_SCANNER_CHECKPOINT_SOURCE = "live_current_roster"
LIVE_SESSION_SCANNER_SOURCE_REF = "live-current-roster"


@dataclass(frozen=True)
class LivePlayerSessionScanSummary:
    """Counts-only summary for one explicit live session scan."""

    observed_count: int = 0
    roster_rows_seen: int = 0
    reliable_rows_seen: int = 0
    unreliable_rows_ignored: int = 0
    duplicate_rows_ignored: int = 0
    observations_considered: int = 0
    observations_applied: int = 0
    observations_skipped: int = 0
    observations_ignored: int = 0
    sessions_created: int = 0
    sessions_updated: int = 0
    source_failures: int = 0
    roster_unavailable: int = 0
    success: bool = True


@dataclass(frozen=True)
class _ReliableRosterRows:
    players_by_id: dict[str, player_sources.CurrentPlayer]
    reliable_rows_seen: int
    unreliable_rows_ignored: int
    duplicate_rows_ignored: int


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_count(value: object, *, fallback: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = fallback
    return max(0, parsed)


def _observed_count(roster: player_sources.CurrentPlayerRoster) -> int:
    row_count = len(roster.players)
    fallback = _safe_count(roster.total_count, fallback=row_count)
    observed = _safe_count(roster.observed_count, fallback=fallback)
    return max(observed, row_count)


def _normalized_player_id(player: player_sources.CurrentPlayer) -> str:
    return normalize_reliable_player_id(player.reliable_id or player.admin_reference)


def _reliable_roster_rows(
    players: tuple[player_sources.CurrentPlayer, ...],
) -> _ReliableRosterRows:
    players_by_id: dict[str, player_sources.CurrentPlayer] = {}
    reliable_rows_seen = 0
    unreliable_rows_ignored = 0
    duplicate_rows_ignored = 0
    for player in players:
        reliable_id = _normalized_player_id(player)
        if not reliable_id:
            unreliable_rows_ignored += 1
            continue
        reliable_rows_seen += 1
        if reliable_id in players_by_id:
            duplicate_rows_ignored += 1
        players_by_id[reliable_id] = player
    return _ReliableRosterRows(
        players_by_id=players_by_id,
        reliable_rows_seen=reliable_rows_seen,
        unreliable_rows_ignored=unreliable_rows_ignored,
        duplicate_rows_ignored=duplicate_rows_ignored,
    )


def _unavailable_summary(
    *,
    roster: player_sources.CurrentPlayerRoster | None = None,
    source_failed: bool = False,
) -> LivePlayerSessionScanSummary:
    return LivePlayerSessionScanSummary(
        observed_count=_observed_count(roster) if roster is not None else 0,
        roster_rows_seen=len(roster.players) if roster is not None else 0,
        source_failures=1 if source_failed else 0,
        roster_unavailable=0 if source_failed else 1,
        success=False,
    )


def scan_live_player_sessions_once(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
    observed_at: str | None = None,
) -> LivePlayerSessionScanSummary:
    """Open/update sessions from one explicit reliable live roster observation.

    This helper reads the existing safe current-roster source once and writes
    session observations only through ``player_registry.observe_player_session``.
    A2S count-only state, unreliable roster rows, source failures, and roster
    unavailability never create synthetic sessions or close existing sessions.
    """
    normalized_instance = instance or paths.DEFAULT_INSTANCE_NAME
    timestamp = observed_at or _utc_now_text()
    try:
        roster = player_sources.load_current_player_roster(normalized_instance)
    except Exception:  # noqa: BLE001 - scanner callers need counts-only failure state.
        return _unavailable_summary(source_failed=True)

    if not roster.available:
        return _unavailable_summary(roster=roster)

    roster_rows = tuple(roster.players)
    observed_count = _observed_count(roster)
    reliable_rows = _reliable_roster_rows(roster_rows)
    observations_considered = len(reliable_rows.players_by_id)
    if observations_considered == 0:
        return LivePlayerSessionScanSummary(
            observed_count=observed_count,
            roster_rows_seen=len(roster_rows),
            reliable_rows_seen=reliable_rows.reliable_rows_seen,
            unreliable_rows_ignored=reliable_rows.unreliable_rows_ignored,
            duplicate_rows_ignored=reliable_rows.duplicate_rows_ignored,
            observations_considered=0,
        )

    db_path = player_registry.player_registry_db_path(
        normalized_instance,
        data_root=data_root,
    )
    observations_applied = 0
    observations_skipped = 0
    observations_ignored = 0
    sessions_created = 0
    sessions_updated = 0

    for reliable_id, player in reliable_rows.players_by_id.items():
        result = player_registry.observe_player_session(
            db_path,
            reliable_id=reliable_id,
            display_name=player.display_name,
            source=player_registry.PLAYER_SESSION_SOURCE_RCON_ROSTER,
            observed_at=timestamp,
            source_ref=LIVE_SESSION_SCANNER_SOURCE_REF,
            confidence=player_registry.PLAYER_SESSION_CONFIDENCE_MEDIUM,
            scanner_checkpoint_source=LIVE_SESSION_SCANNER_CHECKPOINT_SOURCE,
            scanner_checkpoint_ref=LIVE_SESSION_SCANNER_SOURCE_REF,
            scanner_checkpoint_at=timestamp,
        )
        if result.ignored_count:
            observations_ignored += result.ignored_count
            continue
        if not result.written:
            observations_skipped += 1
            continue
        observations_applied += 1
        if result.created:
            sessions_created += 1
        elif result.updated:
            sessions_updated += 1

    return LivePlayerSessionScanSummary(
        observed_count=observed_count,
        roster_rows_seen=len(roster_rows),
        reliable_rows_seen=reliable_rows.reliable_rows_seen,
        unreliable_rows_ignored=reliable_rows.unreliable_rows_ignored,
        duplicate_rows_ignored=reliable_rows.duplicate_rows_ignored,
        observations_considered=observations_considered,
        observations_applied=observations_applied,
        observations_skipped=observations_skipped,
        observations_ignored=observations_ignored,
        sessions_created=sessions_created,
        sessions_updated=sessions_updated,
    )
