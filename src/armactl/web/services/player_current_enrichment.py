"""Read-only current-player enrichment from proven play-session log evidence."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from armactl import paths, player_log_events
from armactl.web.services import player_registry
from armactl.web.services.player_identity import (
    normalize_reliable_player_id,
    safe_player_text,
)

CURRENT_STATS_INGEST_SCOPE = "instance_config_profile_console_logs"
CURRENT_STATS_FRESHNESS_MAX_AGE_SECONDS = 5 * 60
_CURRENT_STATS_FUTURE_SKEW_SECONDS = 60
CURRENT_STATS_SOURCE_LABEL = "Stored fresh play-session log evidence"
CURRENT_FACTION_EVIDENCE_TITLE = (
    "Last-known faction from fresh play-session log evidence; not guaranteed current."
)
CURRENT_STATS_UNAVAILABLE_REASON = (
    "Stats unavailable because proven play-session and fresh log coverage are not available."
)
CURRENT_STATS_NO_RELIABLE_ID_REASON = (
    "Stats unavailable because the current roster has no reliable player ID."
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
    "Stats unavailable because fresh log coverage does not span the current play session."
)

_FRESHNESS_STATUS_UNAVAILABLE = "unavailable"
_FRESHNESS_STATUS_INCOMPLETE = "incomplete"
_FRESHNESS_STATUS_STALE = "stale"
_FRESHNESS_STATUS_FRESH = "fresh"

_REQUIRED_TABLE_COLUMNS = {
    "player_sessions": frozenset(
        {
            "session_id",
            "play_session_id",
            "reliable_id",
            "open_observed_at",
            "close_observed_at",
            "status",
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
        {"scope", "status", "last_success_at"}
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
class CurrentPlayerEnrichment:
    """Nullable current-roster enrichment guarded by the play-session contract."""

    kills: int | None = None
    deaths: int | None = None
    teamkills: int | None = None
    faction: str | None = None
    first_observed_at: str | None = None
    stats_available: bool = False
    stats_source_label: str = ""
    stats_unavailable_reason: str = CURRENT_STATS_UNAVAILABLE_REASON
    stats_freshness_status: str = _FRESHNESS_STATUS_UNAVAILABLE
    stats_freshness_at: str | None = None
    stats_window_started_at: str | None = None
    stats_window_ended_at: str | None = None
    stats_reconnect_merged: bool = False


@dataclass(frozen=True)
class _FreshnessGate:
    available: bool = False
    status: str = _FRESHNESS_STATUS_UNAVAILABLE
    covered_through: str = ""
    reason: str = CURRENT_STATS_FRESHNESS_UNAVAILABLE_REASON


def load_current_player_enrichment(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
    reliable_ids: tuple[str, ...] | list[str] = (),
    now_at: str | None = None,
) -> dict[str, CurrentPlayerEnrichment]:
    """Return session-scoped, freshness-gated stats keyed by reliable player ID."""
    normalized_ids = tuple(
        dict.fromkeys(
            normalized
            for reliable_id in reliable_ids
            if (normalized := normalize_reliable_player_id(reliable_id))
        )
    )
    if not normalized_ids:
        return {}

    db_path = player_registry.player_registry_db_path(instance, data_root=data_root)
    connection = _connect_read_only(db_path)
    if connection is None:
        return _unavailable_map(normalized_ids, CURRENT_STATS_NO_DATABASE_REASON)

    try:
        if not _schema_supports_current_enrichment(connection):
            return _unavailable_map(normalized_ids, CURRENT_STATS_NO_DATABASE_REASON)
        freshness = _load_freshness_gate(connection, now_at=now_at)
        return {
            reliable_id: _load_one_enrichment(
                connection,
                reliable_id,
                freshness=freshness,
            )
            for reliable_id in normalized_ids
        }
    except sqlite3.Error:
        return _unavailable_map(normalized_ids, CURRENT_STATS_NO_DATABASE_REASON)
    finally:
        connection.close()


def _unavailable_map(
    reliable_ids: tuple[str, ...],
    reason: str,
) -> dict[str, CurrentPlayerEnrichment]:
    return {
        reliable_id: CurrentPlayerEnrichment(stats_unavailable_reason=reason)
        for reliable_id in reliable_ids
    }


def _connect_read_only(db_path: Path) -> sqlite3.Connection | None:
    if not db_path.is_file():
        return None
    uri = f"file:{quote(db_path.resolve().as_posix(), safe=':/')}?mode=ro"
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
    except sqlite3.Error:
        if connection is not None:
            connection.close()
        return None
    return connection


def _schema_supports_current_enrichment(connection: sqlite3.Connection) -> bool:
    return all(
        _table_has_columns(connection, table_name, required_columns)
        for table_name, required_columns in _REQUIRED_TABLE_COLUMNS.items()
    )


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
        SELECT status, last_success_at
        FROM player_log_ingest_freshness
        WHERE scope = ?
        """,
        (CURRENT_STATS_INGEST_SCOPE,),
    ).fetchone()
    if row is None:
        return _FreshnessGate()

    ingest_status = safe_player_text(row["status"], max_length=40).lower()
    covered_through = safe_player_text(row["last_success_at"], max_length=80)
    if ingest_status != player_registry.PLAYER_LOG_INGEST_STATUS_FRESH:
        return _FreshnessGate(
            status=_FRESHNESS_STATUS_INCOMPLETE,
            covered_through=covered_through,
            reason=CURRENT_STATS_FRESHNESS_INCOMPLETE_REASON,
        )

    covered_at = _parse_timestamp(covered_through)
    if covered_at is None:
        return _FreshnessGate()
    age_seconds = (_resolved_now(now_at) - covered_at).total_seconds()
    if (
        age_seconds > CURRENT_STATS_FRESHNESS_MAX_AGE_SECONDS
        or age_seconds < -_CURRENT_STATS_FUTURE_SKEW_SECONDS
    ):
        return _FreshnessGate(
            status=_FRESHNESS_STATUS_STALE,
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
            covered_through=covered_through,
            reason=CURRENT_STATS_CHECKPOINT_UNAVAILABLE_REASON,
        )

    return _FreshnessGate(
        available=True,
        status=_FRESHNESS_STATUS_FRESH,
        covered_through=covered_through,
        reason="",
    )


def _load_one_enrichment(
    connection: sqlite3.Connection,
    reliable_id: str,
    *,
    freshness: _FreshnessGate,
) -> CurrentPlayerEnrichment:
    session = connection.execute(
        """
        SELECT
            session_id,
            play_session_id,
            open_observed_at,
            close_observed_at,
            status,
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
    if session is None:
        return CurrentPlayerEnrichment(
            stats_unavailable_reason=CURRENT_STATS_NO_SESSION_REASON,
            stats_freshness_status=freshness.status,
            stats_freshness_at=freshness.covered_through or None,
        )

    opened_at = safe_player_text(session["open_observed_at"], max_length=80)
    reconnect_merged = int(session["reconnect_merge_count"] or 0) > 0
    unavailable_base = {
        "first_observed_at": opened_at or None,
        "stats_freshness_status": freshness.status,
        "stats_freshness_at": freshness.covered_through or None,
        "stats_window_started_at": opened_at or None,
        "stats_reconnect_merged": reconnect_merged,
    }
    if not freshness.available:
        return CurrentPlayerEnrichment(
            stats_unavailable_reason=freshness.reason,
            **unavailable_base,
        )

    if not _session_window_is_proven(
        connection,
        session,
        covered_through=freshness.covered_through,
    ):
        opened = _parse_timestamp(opened_at)
        covered = _parse_timestamp(freshness.covered_through)
        reason = (
            CURRENT_STATS_SESSION_COVERAGE_REASON
            if opened is not None and covered is not None and covered < opened
            else CURRENT_STATS_SESSION_UNPROVEN_REASON
        )
        return CurrentPlayerEnrichment(
            stats_unavailable_reason=reason,
            **unavailable_base,
        )

    kills, deaths, teamkills, faction = _aggregate_session_events(
        connection,
        reliable_id,
        opened_at=opened_at,
        covered_through=freshness.covered_through,
    )
    return CurrentPlayerEnrichment(
        kills=kills,
        deaths=deaths,
        teamkills=teamkills,
        faction=faction,
        first_observed_at=opened_at,
        stats_available=True,
        stats_source_label=CURRENT_STATS_SOURCE_LABEL,
        stats_unavailable_reason="",
        stats_freshness_status=freshness.status,
        stats_freshness_at=freshness.covered_through,
        stats_window_started_at=opened_at,
        stats_window_ended_at=freshness.covered_through,
        stats_reconnect_merged=reconnect_merged,
    )


def _session_window_is_proven(
    connection: sqlite3.Connection,
    session: sqlite3.Row,
    *,
    covered_through: str,
) -> bool:
    try:
        play_session_id = int(session["play_session_id"] or 0)
    except (TypeError, ValueError):
        return False
    opened_at = safe_player_text(session["open_observed_at"], max_length=80)
    opened = _parse_timestamp(opened_at)
    covered = _parse_timestamp(covered_through)
    if (
        play_session_id < 1
        or safe_player_text(session["status"], max_length=40)
        != player_registry.PLAYER_SESSION_STATUS_OPEN
        or safe_player_text(session["close_observed_at"], max_length=80)
        or opened is None
        or covered is None
    ):
        return False
    if covered < opened:
        return False

    boundary = connection.execute(
        """
        SELECT boundary_id
        FROM player_session_lifecycle_boundaries
        WHERE boundary_at <= ?
        ORDER BY boundary_at DESC, boundary_id DESC
        LIMIT 1
        """,
        (opened_at,),
    ).fetchone()
    expected_server_run_key = (
        f"boundary:{int(boundary['boundary_id'])}" if boundary is not None else ""
    )
    server_run_key = safe_player_text(session["server_run_key"], max_length=120)
    if server_run_key != expected_server_run_key:
        return False

    later_boundary = connection.execute(
        """
        SELECT 1
        FROM player_session_lifecycle_boundaries
        WHERE boundary_at > ?
          AND boundary_at <= ?
        LIMIT 1
        """,
        (opened_at, covered_through),
    ).fetchone()
    return later_boundary is None


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
    covered_through: str,
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
            covered_through,
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
