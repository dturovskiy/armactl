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
LIVE_SESSION_SCANNER_ABSENCE_SOURCE_REF = "live-current-roster-absence"
LIVE_SESSION_ABSENCE_CONFIRMATION_SCANS = (
    player_registry.DEFAULT_PLAYER_SESSION_ABSENCE_CONFIRMATION_SCANS
)


@dataclass(frozen=True)
class LivePlayerSessionScanSummary:
    """Counts-only summary for one explicit live session scan."""

    observed_count: int = 0
    roster_rows_seen: int = 0
    reliable_rows_seen: int = 0
    unreliable_rows_ignored: int = 0
    duplicate_rows_ignored: int = 0
    scans_considered: int = 0
    observations_considered: int = 0
    observations_applied: int = 0
    observations_skipped: int = 0
    observations_ignored: int = 0
    absent_sessions_considered: int = 0
    absent_sessions_confirmed: int = 0
    sessions_created: int = 0
    sessions_updated: int = 0
    sessions_closed: int = 0
    sessions_skipped: int = 0
    source_failures: int = 0
    roster_unavailable: int = 0
    reliable_evidence: bool = False
    reliability_error_code: str = ""
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
    return _safe_count(roster.observed_count, fallback=fallback)


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


def _reliable_roster_evidence(
    roster: player_sources.CurrentPlayerRoster,
    reliable_rows: _ReliableRosterRows,
    *,
    observed_count: int,
) -> tuple[bool, str]:
    if not roster.available or not roster.roster_available:
        return False, "roster_unavailable"
    legacy_rcon = (
        str(roster.source or "").casefold() == "rcon.roster"
        and str(roster.count_source or "").casefold() == "rcon"
    )
    rcon_status = str(roster.rcon_status or "").casefold()
    if rcon_status != "ok" and not (rcon_status == "unavailable" and legacy_rcon):
        return False, "rcon_failed"
    roster_source = str(roster.roster_source or "").casefold()
    if roster_source != "rcon" and not (roster_source == "unknown" and legacy_rcon):
        return False, "non_rcon_roster"
    query_attempt_count = roster.query_attempt_count or int(legacy_rcon)
    if roster.duplicate_query_attempts or query_attempt_count != 1:
        return False, "duplicate_query_attempt"
    if roster.count_mismatch or observed_count != len(roster.players):
        return False, "count_mismatch"
    if any(
        not str(player.source or "").casefold().startswith("rcon.")
        for player in roster.players
    ):
        return False, "mixed_roster_source"
    if reliable_rows.unreliable_rows_ignored:
        return False, "unreliable_roster_row"
    if reliable_rows.duplicate_rows_ignored:
        return False, "duplicate_reliable_id"
    if reliable_rows.reliable_rows_seen != len(roster.players):
        return False, "unreliable_roster_row"
    if len(reliable_rows.players_by_id) != observed_count:
        return False, "count_mismatch"
    return True, ""


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
        reliable_evidence=False,
        reliability_error_code=(
            "source_failed" if source_failed else "roster_unavailable"
        ),
        success=False,
    )


def scan_live_player_sessions_once(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
    observed_at: str | None = None,
    absence_confirmation_scans: int = LIVE_SESSION_ABSENCE_CONFIRMATION_SCANS,
) -> LivePlayerSessionScanSummary:
    """Open/update sessions from one explicit reliable live roster observation.

    This helper reads the existing safe current-roster source once and writes
    session observations only through ``player_registry.observe_player_session``.
    Repeated successful reliable RCON absence can close sessions only through
    ``player_registry.close_player_session``. A2S count-only state, unreliable
    roster rows, source failures, and roster unavailability never create
    synthetic sessions, close sessions, or advance absence windows.
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
    reliable_evidence, reliability_error_code = _reliable_roster_evidence(
        roster,
        reliable_rows,
        observed_count=observed_count,
    )
    if not reliable_evidence:
        return LivePlayerSessionScanSummary(
            observed_count=observed_count,
            roster_rows_seen=len(roster_rows),
            reliable_rows_seen=reliable_rows.reliable_rows_seen,
            unreliable_rows_ignored=reliable_rows.unreliable_rows_ignored,
            duplicate_rows_ignored=reliable_rows.duplicate_rows_ignored,
            source_failures=int(reliability_error_code == "rcon_failed"),
            roster_unavailable=int(reliability_error_code == "roster_unavailable"),
            reliable_evidence=False,
            reliability_error_code=reliability_error_code,
            success=False,
        )
    absence_scan = True
    scans_considered = 1

    db_path = player_registry.player_registry_db_path(
        normalized_instance,
        data_root=data_root,
    )
    observations_applied = 0
    observations_skipped = 0
    observations_ignored = 0
    sessions_created = 0
    sessions_updated = 0
    absent_sessions_considered = 0
    absent_sessions_confirmed = 0
    sessions_closed = 0
    sessions_skipped = 0

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

    if reliable_rows.players_by_id:
        player_registry.clear_player_session_live_absence_windows(
            db_path,
            reliable_ids=reliable_rows.players_by_id,
            source=player_registry.PLAYER_SESSION_SOURCE_SCANNER_CHECKPOINT,
            source_ref=LIVE_SESSION_SCANNER_ABSENCE_SOURCE_REF,
        )

    if absence_scan:
        present_ids = set(reliable_rows.players_by_id)
        for session in player_registry.list_open_player_sessions(db_path):
            if session.reliable_id in present_ids:
                continue
            absent_sessions_considered += 1
            absence = player_registry.record_player_session_live_absence(
                db_path,
                session=session,
                observed_at=timestamp,
                source=player_registry.PLAYER_SESSION_SOURCE_SCANNER_CHECKPOINT,
                source_ref=LIVE_SESSION_SCANNER_ABSENCE_SOURCE_REF,
                confirmation_scans=absence_confirmation_scans,
            )
            if absence.ignored_count:
                sessions_skipped += absence.ignored_count
                continue
            if not absence.confirmation_reached:
                sessions_skipped += 1
                continue

            absent_sessions_confirmed += 1
            close_result = player_registry.close_player_session(
                db_path,
                reliable_id=session.reliable_id,
                close_observed_at=timestamp,
                source=player_registry.PLAYER_SESSION_SOURCE_SCANNER_CHECKPOINT,
                source_ref=LIVE_SESSION_SCANNER_ABSENCE_SOURCE_REF,
                confidence=player_registry.PLAYER_SESSION_CONFIDENCE_LOW,
                end_reason=player_registry.PLAYER_SESSION_END_REASON_STALE_ABSENCE,
            )
            if close_result.closed:
                sessions_closed += 1
                player_registry.confirm_player_session_live_absence_window(
                    db_path,
                    session_id=session.session_id,
                    confirmed_at=timestamp,
                    source=player_registry.PLAYER_SESSION_SOURCE_SCANNER_CHECKPOINT,
                    source_ref=LIVE_SESSION_SCANNER_ABSENCE_SOURCE_REF,
                )
            else:
                sessions_skipped += 1

    return LivePlayerSessionScanSummary(
        observed_count=observed_count,
        roster_rows_seen=len(roster_rows),
        reliable_rows_seen=reliable_rows.reliable_rows_seen,
        unreliable_rows_ignored=reliable_rows.unreliable_rows_ignored,
        duplicate_rows_ignored=reliable_rows.duplicate_rows_ignored,
        observations_considered=observations_considered,
        scans_considered=scans_considered,
        observations_applied=observations_applied,
        observations_skipped=observations_skipped,
        observations_ignored=observations_ignored,
        absent_sessions_considered=absent_sessions_considered,
        absent_sessions_confirmed=absent_sessions_confirmed,
        sessions_created=sessions_created,
        sessions_updated=sessions_updated,
        sessions_closed=sessions_closed,
        sessions_skipped=sessions_skipped,
        reliable_evidence=True,
        success=True,
    )
