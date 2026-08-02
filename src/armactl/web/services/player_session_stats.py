"""Shared read-only evaluator for proven open and closed player-session stats."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from armactl import player_log_events
from armactl.web.services import player_registry
from armactl.web.services.player_identity import (
    normalize_reliable_player_id,
    safe_player_text,
)

CURRENT_STATS_INGEST_SCOPE = "instance_config_profile_console_logs"
CURRENT_STATS_FRESHNESS_MAX_AGE_SECONDS = 5 * 60
_CURRENT_STATS_FUTURE_SKEW_SECONDS = 60
CURRENT_STATS_SOURCE_LABEL = "Stored fresh play-session log evidence"
CURRENT_STATS_UNAVAILABLE_REASON = (
    "Stats unavailable because proven play-session and fresh log coverage are not available."
)
CURRENT_STATS_NO_DATABASE_REASON = (
    "Stats unavailable because stored player session and log evidence are not available."
)
CURRENT_STATS_NO_SESSION_REASON = (
    "Stats unavailable because no proven open play session is available."
)
CURRENT_STATS_SESSION_UNPROVEN_REASON = (
    "Stats unavailable because the play-session window is not proven."
)
CURRENT_STATS_FRESHNESS_UNAVAILABLE_REASON = (
    "Stats unavailable because player log ingest freshness is unavailable."
)
CURRENT_STATS_FRESHNESS_INCOMPLETE_REASON = (
    "Stats unavailable because player log ingest is not fully fresh."
)
CURRENT_STATS_FRESHNESS_STALE_REASON = (
    "Stats unavailable because player log ingest is stale."
)
CURRENT_STATS_CHECKPOINT_UNAVAILABLE_REASON = (
    "Stats unavailable because player log ingest checkpoints are unavailable."
)
CURRENT_STATS_SESSION_COVERAGE_REASON = (
    "Stats unavailable because fresh log coverage does not span the player session."
)

FRESHNESS_STATUS_UNAVAILABLE = "unavailable"
FRESHNESS_STATUS_INCOMPLETE = "incomplete"
FRESHNESS_STATUS_STALE = "stale"
FRESHNESS_STATUS_FRESH = "fresh"

_REQUIRED_TABLE_COLUMNS = {
    "player_sessions": frozenset(
        {
            "session_id",
            "play_session_id",
            "reliable_id",
            "open_observed_at",
            "close_observed_at",
            "status",
            "end_reason",
            "server_run_key",
            "reconnect_merge_count",
        }
    ),
    "player_log_events": frozenset(
        {
            "event_id",
            "event_type",
            "occurred_at",
            "time_confidence",
            "player_id",
            "player_faction",
            "victim_id",
            "victim_faction",
            "instigator_id",
            "instigator_faction",
            "teamkill",
            "suicide",
            "ai_instigator",
        }
    ),
    "player_log_ingest_freshness": frozenset(
        {"scope", "status", "last_success_at", "coverage_started_at"}
    ),
    "player_log_ingest_checkpoints": frozenset(
        {"scope", "status", "last_scanned_at"}
    ),
    "player_session_lifecycle_boundaries": frozenset(
        {"boundary_id", "boundary_at"}
    ),
}
_STABLE_TIME_CONFIDENCES = (
    player_log_events.EVENT_TIME_CONFIDENCE_EXACT,
    player_log_events.EVENT_TIME_CONFIDENCE_DERIVED,
)
_DEATH_EVENT_TYPES = frozenset(
    {
        player_log_events.EVENT_TYPE_KILL,
        player_log_events.EVENT_TYPE_TEAMKILL,
        player_log_events.EVENT_TYPE_SUICIDE,
        player_log_events.EVENT_TYPE_OTHER_DEATH,
    }
)


@dataclass(frozen=True)
class PlayerSessionStats:
    """Sanitized nullable stats and proof metadata for one stored session window."""

    stats_available: bool = False
    stats_unavailable_reason: str | None = CURRENT_STATS_UNAVAILABLE_REASON
    kills: int | None = None
    deaths: int | None = None
    teamkills: int | None = None
    faction: str | None = None
    covered_from: str | None = None
    covered_through: str | None = None
    window_started_at: str | None = None
    window_ended_at: str | None = None
    reconnect_merged: bool = False
    source_label: str = ""
    freshness_status: str = FRESHNESS_STATUS_UNAVAILABLE


@dataclass(frozen=True)
class _FreshnessGate:
    available: bool = False
    status: str = FRESHNESS_STATUS_UNAVAILABLE
    covered_from: str = ""
    covered_through: str = ""
    reason: str = CURRENT_STATS_FRESHNESS_UNAVAILABLE_REASON


@dataclass(frozen=True)
class _SessionWindow:
    reliable_id: str
    opened_at: str
    closed_at: str
    status: str
    end_reason: str
    reconnect_merged: bool
    play_session_id: int = field(repr=False)
    server_run_key: str = field(repr=False)


def _table_has_columns(
    connection: sqlite3.Connection,
    table_name: str,
    required_columns: frozenset[str],
) -> bool:
    if table_name not in _REQUIRED_TABLE_COLUMNS:
        return False
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    columns = {str(row["name"]) for row in rows}
    return bool(columns) and required_columns.issubset(columns)


def _schema_supports_session_stats(connection: sqlite3.Connection) -> bool:
    return all(
        _table_has_columns(connection, table_name, required_columns)
        for table_name, required_columns in _REQUIRED_TABLE_COLUMNS.items()
    )


def _parse_timestamp(value: object) -> datetime | None:
    text = safe_player_text(value, max_length=80)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _resolved_now(now_at: str | None) -> datetime:
    parsed = _parse_timestamp(now_at)
    return parsed or datetime.now(timezone.utc)


def _load_freshness_gate(
    connection: sqlite3.Connection,
    *,
    now_at: str | None,
) -> _FreshnessGate:
    row = connection.execute(
        """
        SELECT status, last_success_at, coverage_started_at
        FROM player_log_ingest_freshness
        WHERE scope = ?
        """,
        (CURRENT_STATS_INGEST_SCOPE,),
    ).fetchone()
    if row is None:
        return _FreshnessGate()

    ingest_status = safe_player_text(row["status"], max_length=40).lower()
    covered_through = safe_player_text(row["last_success_at"], max_length=80)
    covered_from = safe_player_text(row["coverage_started_at"], max_length=80)
    if ingest_status != player_registry.PLAYER_LOG_INGEST_STATUS_FRESH:
        return _FreshnessGate(
            status=FRESHNESS_STATUS_INCOMPLETE,
            covered_from=covered_from,
            covered_through=covered_through,
            reason=CURRENT_STATS_FRESHNESS_INCOMPLETE_REASON,
        )

    covered_at = _parse_timestamp(covered_through)
    coverage_started = _parse_timestamp(covered_from)
    if covered_at is None or coverage_started is None or coverage_started > covered_at:
        return _FreshnessGate()
    age_seconds = (_resolved_now(now_at) - covered_at).total_seconds()
    if (
        age_seconds > CURRENT_STATS_FRESHNESS_MAX_AGE_SECONDS
        or age_seconds < -_CURRENT_STATS_FUTURE_SKEW_SECONDS
    ):
        return _FreshnessGate(
            status=FRESHNESS_STATUS_STALE,
            covered_from=covered_from,
            covered_through=covered_through,
            reason=CURRENT_STATS_FRESHNESS_STALE_REASON,
        )

    checkpoint = connection.execute(
        """
        SELECT COUNT(*) AS checkpoint_count
        FROM player_log_ingest_checkpoints
        WHERE scope = ?
          AND status = 'scanned'
          AND last_scanned_at != ''
        """,
        (CURRENT_STATS_INGEST_SCOPE,),
    ).fetchone()
    if checkpoint is None or int(checkpoint["checkpoint_count"] or 0) < 1:
        return _FreshnessGate(
            covered_from=covered_from,
            covered_through=covered_through,
            reason=CURRENT_STATS_CHECKPOINT_UNAVAILABLE_REASON,
        )

    return _FreshnessGate(
        available=True,
        status=FRESHNESS_STATUS_FRESH,
        covered_from=covered_from,
        covered_through=covered_through,
        reason="",
    )


def _window_from_row(row: sqlite3.Row, reliable_id: str) -> _SessionWindow:
    return _SessionWindow(
        reliable_id=reliable_id,
        opened_at=safe_player_text(row["open_observed_at"], max_length=80),
        closed_at=safe_player_text(row["close_observed_at"], max_length=80),
        status=safe_player_text(row["status"], max_length=40),
        end_reason=safe_player_text(row["end_reason"], max_length=80),
        reconnect_merged=int(row["reconnect_merge_count"] or 0) > 0,
        play_session_id=int(row["play_session_id"] or 0),
        server_run_key=safe_player_text(row["server_run_key"], max_length=120),
    )


def _window_from_record(
    session: player_registry.PlayerSessionRecord,
) -> _SessionWindow | None:
    reliable_id = normalize_reliable_player_id(session.reliable_id)
    if not reliable_id:
        return None
    return _SessionWindow(
        reliable_id=reliable_id,
        opened_at=safe_player_text(session.open_observed_at, max_length=80),
        closed_at=safe_player_text(session.close_observed_at, max_length=80),
        status=safe_player_text(session.status, max_length=40),
        end_reason=safe_player_text(session.end_reason, max_length=80),
        reconnect_merged=max(0, int(session.reconnect_merge_count)) > 0,
        play_session_id=max(0, int(session.play_session_id)),
        server_run_key=safe_player_text(session.server_run_key, max_length=120),
    )


def _unavailable_stats(
    *,
    reason: str,
    window: _SessionWindow | None = None,
    freshness: _FreshnessGate | None = None,
) -> PlayerSessionStats:
    freshness = freshness or _FreshnessGate()
    return PlayerSessionStats(
        stats_unavailable_reason=reason,
        covered_from=freshness.covered_from or None,
        covered_through=freshness.covered_through or None,
        window_started_at=window.opened_at or None if window is not None else None,
        reconnect_merged=window.reconnect_merged if window is not None else False,
        freshness_status=freshness.status,
    )


def _proven_window_end(
    connection: sqlite3.Connection,
    window: _SessionWindow,
    *,
    freshness: _FreshnessGate,
) -> tuple[str, str]:
    opened = _parse_timestamp(window.opened_at)
    coverage_start = _parse_timestamp(freshness.covered_from)
    covered = _parse_timestamp(freshness.covered_through)
    if (
        window.play_session_id < 1
        or window.status not in player_registry.PLAYER_SESSION_STATUSES
        or opened is None
        or coverage_start is None
        or covered is None
    ):
        return "", CURRENT_STATS_SESSION_UNPROVEN_REASON

    if window.status == player_registry.PLAYER_SESSION_STATUS_OPEN:
        if window.closed_at:
            return "", CURRENT_STATS_SESSION_UNPROVEN_REASON
        ended_at = freshness.covered_through
        ended = covered
    else:
        ended_at = window.closed_at
        ended = _parse_timestamp(ended_at)
        if ended is None:
            return "", CURRENT_STATS_SESSION_UNPROVEN_REASON

    if ended < opened:
        return "", CURRENT_STATS_SESSION_UNPROVEN_REASON
    if coverage_start > opened or covered < ended:
        return "", CURRENT_STATS_SESSION_COVERAGE_REASON

    boundary = connection.execute(
        """
        SELECT boundary_id
        FROM player_session_lifecycle_boundaries
        WHERE boundary_at <= ?
        ORDER BY boundary_at DESC, boundary_id DESC
        LIMIT 1
        """,
        (window.opened_at,),
    ).fetchone()
    expected_server_run_key = (
        f"boundary:{int(boundary['boundary_id'])}" if boundary is not None else ""
    )
    if window.server_run_key != expected_server_run_key:
        return "", CURRENT_STATS_SESSION_UNPROVEN_REASON

    later_boundary = connection.execute(
        """
        SELECT boundary_at
        FROM player_session_lifecycle_boundaries
        WHERE boundary_at > ?
          AND boundary_at <= ?
        ORDER BY boundary_at ASC, boundary_id ASC
        LIMIT 1
        """,
        (window.opened_at, ended_at),
    ).fetchone()
    if later_boundary is not None:
        boundary_at = _parse_timestamp(later_boundary["boundary_at"])
        terminal_server_boundary = (
            window.status == player_registry.PLAYER_SESSION_STATUS_CLOSED
            and window.end_reason
            == player_registry.PLAYER_SESSION_END_REASON_SERVER_BOUNDARY
            and boundary_at == ended
        )
        if not terminal_server_boundary:
            return "", CURRENT_STATS_SESSION_UNPROVEN_REASON
    return ended_at, ""


def _stable_death_event(row: sqlite3.Row) -> bool:
    event_type = safe_player_text(row["event_type"], max_length=80)
    if event_type == player_log_events.EVENT_TYPE_KILL:
        return not bool(row["teamkill"])
    if event_type == player_log_events.EVENT_TYPE_TEAMKILL:
        return bool(row["teamkill"])
    if event_type == player_log_events.EVENT_TYPE_SUICIDE:
        return bool(row["suicide"])
    return event_type == player_log_events.EVENT_TYPE_OTHER_DEATH


def _aggregate_session_events(
    connection: sqlite3.Connection,
    reliable_id: str,
    *,
    opened_at: str,
    ended_at: str,
) -> tuple[int, int, int, str | None]:
    rows = connection.execute(
        """
        SELECT
            event_id,
            event_type,
            occurred_at,
            player_id,
            player_faction,
            victim_id,
            victim_faction,
            instigator_id,
            instigator_faction,
            teamkill,
            suicide,
            ai_instigator
        FROM player_log_events
        WHERE occurred_at >= ?
          AND occurred_at <= ?
          AND time_confidence IN (?, ?)
          AND (
              player_id = ?
              OR victim_id = ?
              OR instigator_id = ?
          )
        ORDER BY occurred_at ASC, event_id ASC
        """,
        (
            opened_at,
            ended_at,
            *_STABLE_TIME_CONFIDENCES,
            reliable_id,
            reliable_id,
            reliable_id,
        ),
    ).fetchall()

    kills = 0
    deaths = 0
    teamkills = 0
    faction: str | None = None
    for row in rows:
        event_type = safe_player_text(row["event_type"], max_length=80)
        player_id = normalize_reliable_player_id(row["player_id"])
        victim_id = normalize_reliable_player_id(row["victim_id"])
        instigator_id = normalize_reliable_player_id(row["instigator_id"])
        ai_instigator = bool(row["ai_instigator"])
        stable_death = event_type in _DEATH_EVENT_TYPES and _stable_death_event(row)

        if (
            event_type == player_log_events.EVENT_TYPE_KILL
            and not bool(row["teamkill"])
            and not ai_instigator
            and instigator_id == reliable_id
        ):
            kills += 1
        if (
            event_type == player_log_events.EVENT_TYPE_TEAMKILL
            and bool(row["teamkill"])
            and not ai_instigator
            and instigator_id == reliable_id
        ):
            teamkills += 1
        if stable_death and victim_id == reliable_id:
            deaths += 1

        faction_evidence = ""
        if (
            event_type == player_log_events.EVENT_TYPE_FACTION_JOIN
            and player_id == reliable_id
        ):
            faction_evidence = safe_player_text(row["player_faction"], max_length=80)
        elif stable_death and victim_id == reliable_id:
            faction_evidence = safe_player_text(row["victim_faction"], max_length=80)
        elif stable_death and not ai_instigator and instigator_id == reliable_id:
            faction_evidence = safe_player_text(
                row["instigator_faction"],
                max_length=80,
            )
        if faction_evidence:
            faction = faction_evidence

    return kills, deaths, teamkills, faction


def _evaluate_session_window(
    connection: sqlite3.Connection,
    window: _SessionWindow,
    *,
    freshness: _FreshnessGate,
) -> PlayerSessionStats:
    if not freshness.available:
        return _unavailable_stats(
            reason=freshness.reason,
            window=window,
            freshness=freshness,
        )

    ended_at, reason = _proven_window_end(
        connection,
        window,
        freshness=freshness,
    )
    if reason:
        return _unavailable_stats(
            reason=reason,
            window=window,
            freshness=freshness,
        )

    kills, deaths, teamkills, faction = _aggregate_session_events(
        connection,
        window.reliable_id,
        opened_at=window.opened_at,
        ended_at=ended_at,
    )
    return PlayerSessionStats(
        stats_available=True,
        stats_unavailable_reason=None,
        kills=kills,
        deaths=deaths,
        teamkills=teamkills,
        faction=faction,
        covered_from=freshness.covered_from,
        covered_through=freshness.covered_through,
        window_started_at=window.opened_at,
        window_ended_at=ended_at,
        reconnect_merged=window.reconnect_merged,
        source_label=CURRENT_STATS_SOURCE_LABEL,
        freshness_status=freshness.status,
    )


def _unavailable_map(
    reliable_ids: tuple[str, ...],
    reason: str,
) -> dict[str, PlayerSessionStats]:
    return {
        reliable_id: _unavailable_stats(reason=reason)
        for reliable_id in reliable_ids
    }


def load_current_session_stats(
    db_path: Path,
    *,
    reliable_ids: tuple[str, ...] | list[str],
    now_at: str | None = None,
) -> dict[str, PlayerSessionStats]:
    """Evaluate the one open stored session for each reliable current-player ID."""
    normalized_ids = tuple(
        dict.fromkeys(
            normalized
            for reliable_id in reliable_ids
            if (normalized := normalize_reliable_player_id(reliable_id))
        )
    )
    if not normalized_ids:
        return {}

    connection = player_registry._connect_existing_readonly(db_path)
    if connection is None:
        return _unavailable_map(normalized_ids, CURRENT_STATS_NO_DATABASE_REASON)
    try:
        if not _schema_supports_session_stats(connection):
            return _unavailable_map(normalized_ids, CURRENT_STATS_NO_DATABASE_REASON)
        freshness = _load_freshness_gate(connection, now_at=now_at)
        results: dict[str, PlayerSessionStats] = {}
        for reliable_id in normalized_ids:
            row = connection.execute(
                """
                SELECT
                    play_session_id,
                    open_observed_at,
                    close_observed_at,
                    status,
                    end_reason,
                    server_run_key,
                    reconnect_merge_count
                FROM player_sessions
                WHERE reliable_id = ?
                  AND status = ?
                ORDER BY session_id DESC
                LIMIT 1
                """,
                (reliable_id, player_registry.PLAYER_SESSION_STATUS_OPEN),
            ).fetchone()
            if row is None:
                results[reliable_id] = _unavailable_stats(
                    reason=CURRENT_STATS_NO_SESSION_REASON,
                    freshness=freshness,
                )
                continue
            results[reliable_id] = _evaluate_session_window(
                connection,
                _window_from_row(row, reliable_id),
                freshness=freshness,
            )
        return results
    except (IndexError, KeyError, TypeError, ValueError, sqlite3.Error):
        return _unavailable_map(normalized_ids, CURRENT_STATS_NO_DATABASE_REASON)
    finally:
        connection.close()


def load_stored_session_stats(
    db_path: Path,
    session: player_registry.PlayerSessionRecord,
    *,
    now_at: str | None = None,
) -> PlayerSessionStats:
    """Evaluate one already-selected stored open or closed session query-only."""
    try:
        window = _window_from_record(session)
    except (TypeError, ValueError):
        window = None
    if window is None:
        return _unavailable_stats(reason=CURRENT_STATS_SESSION_UNPROVEN_REASON)

    connection = player_registry._connect_existing_readonly(db_path)
    if connection is None:
        return _unavailable_stats(
            reason=CURRENT_STATS_NO_DATABASE_REASON,
            window=window,
        )
    try:
        if not _schema_supports_session_stats(connection):
            return _unavailable_stats(
                reason=CURRENT_STATS_NO_DATABASE_REASON,
                window=window,
            )
        freshness = _load_freshness_gate(connection, now_at=now_at)
        return _evaluate_session_window(
            connection,
            window,
            freshness=freshness,
        )
    except (IndexError, KeyError, TypeError, ValueError, sqlite3.Error):
        return _unavailable_stats(
            reason=CURRENT_STATS_NO_DATABASE_REASON,
            window=window,
        )
    finally:
        connection.close()
