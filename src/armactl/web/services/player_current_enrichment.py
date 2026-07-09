"""Read-only current-player roster enrichment from stored player evidence."""

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

CURRENT_STATS_SOURCE_LABEL = "Stored current-session evidence"
CURRENT_SERVER_STATS_SOURCE_LABEL = "Stored current-server evidence"

_PLAYER_SESSIONS_REQUIRED_COLUMNS = frozenset(
    {
        "session_id",
        "reliable_id",
        "open_observed_at",
        "status",
    }
)
_PLAYER_LOG_EVENTS_REQUIRED_COLUMNS = frozenset(
    {
        "event_id",
        "event_type",
        "occurred_at",
        "time_source",
        "time_confidence",
        "player_id",
        "player_faction",
        "victim_id",
        "victim_faction",
        "instigator_id",
        "instigator_faction",
        "ai_instigator",
    }
)
_DEATH_EVENT_TYPES = (
    player_log_events.EVENT_TYPE_KILL,
    player_log_events.EVENT_TYPE_TEAMKILL,
    player_log_events.EVENT_TYPE_SUICIDE,
    player_log_events.EVENT_TYPE_OTHER_DEATH,
)
_COMBAT_INSTIGATOR_EVENT_TYPES = (
    player_log_events.EVENT_TYPE_KILL,
    player_log_events.EVENT_TYPE_TEAMKILL,
)
_DEATH_EVENT_PLACEHOLDERS = ", ".join("?" for _event_type in _DEATH_EVENT_TYPES)
_COMBAT_INSTIGATOR_EVENT_PLACEHOLDERS = ", ".join(
    "?" for _event_type in _COMBAT_INSTIGATOR_EVENT_TYPES
)


@dataclass(frozen=True)
class CurrentPlayerEnrichment:
    """Nullable current-roster enrichment derived from an open stored session."""

    kills: int | None = None
    deaths: int | None = None
    teamkills: int | None = None
    faction: str | None = None
    first_observed_at: str | None = None
    stats_available: bool = False
    stats_source_label: str = ""


def load_current_player_enrichment(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
    reliable_ids: tuple[str, ...] | list[str] = (),
    now_at: str | None = None,
) -> dict[str, CurrentPlayerEnrichment]:
    """Return read-only current-session enrichment keyed by reliable player ID."""
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
        return {}

    try:
        if not _schema_supports_current_enrichment(connection):
            return {}
        upper_bound = (
            now_at
            or _latest_valid_event_at(connection)
            or datetime.now(timezone.utc).isoformat()
        )
        return {
            reliable_id: enrichment
            for reliable_id in normalized_ids
            if (
                enrichment := _load_one_enrichment(
                    connection,
                    reliable_id,
                    upper_bound,
                )
            )
            is not None
        }
    except sqlite3.Error:
        return {}
    finally:
        connection.close()


def _connect_read_only(db_path: Path) -> sqlite3.Connection | None:
    if not db_path.is_file():
        return None
    uri = f"file:{quote(db_path.resolve().as_posix(), safe=':/')}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
    except sqlite3.Error:
        try:
            connection.close()
        except (NameError, sqlite3.Error):
            pass
        return None
    return connection


def _schema_supports_current_enrichment(connection: sqlite3.Connection) -> bool:
    return (
        _table_has_columns(
            connection,
            "player_sessions",
            _PLAYER_SESSIONS_REQUIRED_COLUMNS,
        )
        and _table_has_columns(
            connection,
            "player_log_events",
            _PLAYER_LOG_EVENTS_REQUIRED_COLUMNS,
        )
    )


def _table_has_columns(
    connection: sqlite3.Connection,
    table_name: str,
    required_columns: frozenset[str],
) -> bool:
    if table_name not in {"player_sessions", "player_log_events"}:
        return False
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    columns = {str(row["name"]) for row in rows}
    return bool(columns) and required_columns.issubset(columns)


def _latest_valid_event_at(connection: sqlite3.Connection) -> str:
    row = connection.execute(
        f"""
        SELECT occurred_at
        FROM player_log_events
        WHERE 1 = 1
        {_valid_current_event_window_sql(include_bounds=False)}
        ORDER BY occurred_at DESC, event_id DESC
        LIMIT 1
        """,
        _valid_event_params(),
    ).fetchone()
    if row is None:
        return ""
    return safe_player_text(row["occurred_at"], max_length=80)


def _latest_server_boundary_at(connection: sqlite3.Connection) -> str:
    row = connection.execute(
        f"""
        SELECT occurred_at
        FROM player_log_events
        WHERE event_type = ?
        {_valid_current_event_window_sql(include_bounds=False)}
        ORDER BY occurred_at DESC, event_id DESC
        LIMIT 1
        """,
        (player_log_events.EVENT_TYPE_SERVER_LIFECYCLE, *_valid_event_params()),
    ).fetchone()
    if row is None:
        return ""
    return safe_player_text(row["occurred_at"], max_length=80)


def _load_one_enrichment(
    connection: sqlite3.Connection,
    reliable_id: str,
    upper_bound: str,
) -> CurrentPlayerEnrichment | None:
    session = connection.execute(
        """
        SELECT session_id, open_observed_at
        FROM player_sessions
        WHERE reliable_id = ?
          AND status = ?
        ORDER BY session_id DESC
        LIMIT 1
        """,
        (reliable_id, player_registry.PLAYER_SESSION_STATUS_OPEN),
    ).fetchone()
    if session is None:
        lower_bound = _latest_server_boundary_at(connection)
        first_observed_at = None
        source_label = CURRENT_SERVER_STATS_SOURCE_LABEL
    else:
        lower_bound = safe_player_text(session["open_observed_at"], max_length=80)
        first_observed_at = lower_bound
        source_label = CURRENT_STATS_SOURCE_LABEL
    if not lower_bound:
        return None

    has_combat_evidence = _has_combat_counter_evidence(
        connection, reliable_id, lower_bound, upper_bound
    )
    return CurrentPlayerEnrichment(
        kills=_count_kills(connection, reliable_id, lower_bound, upper_bound)
        if has_combat_evidence
        else None,
        deaths=_count_deaths(connection, reliable_id, lower_bound, upper_bound)
        if has_combat_evidence
        else None,
        teamkills=_count_teamkills(
            connection,
            reliable_id,
            lower_bound,
            upper_bound,
        )
        if has_combat_evidence
        else None,
        faction=_last_known_faction(
            connection,
            reliable_id,
            lower_bound,
            upper_bound,
        ),
        first_observed_at=first_observed_at,
        stats_available=has_combat_evidence,
        stats_source_label=source_label if has_combat_evidence else "",
    )


def _has_combat_counter_evidence(
    connection: sqlite3.Connection,
    reliable_id: str,
    lower_bound: str,
    upper_bound: str,
) -> bool:
    row = connection.execute(
        f"""
        SELECT 1
        FROM player_log_events
        WHERE (
            (
              event_type IN ({_COMBAT_INSTIGATOR_EVENT_PLACEHOLDERS})
              AND instigator_id = ?
              AND COALESCE(ai_instigator, 0) != 1
            )
            OR (
              event_type IN ({_DEATH_EVENT_PLACEHOLDERS})
              AND victim_id = ?
            )
        )
        {_valid_current_event_window_sql()}
        LIMIT 1
        """,
        (
            *_COMBAT_INSTIGATOR_EVENT_TYPES,
            reliable_id,
            *_DEATH_EVENT_TYPES,
            reliable_id,
            *_window_params(lower_bound, upper_bound),
        ),
    ).fetchone()
    return row is not None


def _valid_current_event_window_sql(*, include_bounds: bool = True) -> str:
    bounds = """
      AND occurred_at >= ?
      AND occurred_at <= ?
    """ if include_bounds else ""
    return f"""
      AND occurred_at IS NOT NULL
      AND occurred_at != ''
      {bounds}
      AND COALESCE(time_source, '') != ?
    """


def _valid_event_params() -> tuple[str]:
    return (player_log_events.EVENT_TIME_SOURCE_UNAVAILABLE,)


def _window_params(lower_bound: str, upper_bound: str) -> tuple[str, str, str]:
    return (
        lower_bound,
        upper_bound,
        *_valid_event_params(),
    )


def _count_kills(
    connection: sqlite3.Connection,
    reliable_id: str,
    open_observed_at: str,
    upper_bound: str,
) -> int:
    row = connection.execute(
        f"""
        SELECT COUNT(*) AS count
        FROM player_log_events
        WHERE event_type = ?
          AND instigator_id = ?
          AND COALESCE(ai_instigator, 0) != 1
        {_valid_current_event_window_sql()}
        """,
        (
            player_log_events.EVENT_TYPE_KILL,
            reliable_id,
            *_window_params(open_observed_at, upper_bound),
        ),
    ).fetchone()
    return int(row["count"] if row is not None else 0)


def _count_deaths(
    connection: sqlite3.Connection,
    reliable_id: str,
    open_observed_at: str,
    upper_bound: str,
) -> int:
    row = connection.execute(
        f"""
        SELECT COUNT(*) AS count
        FROM player_log_events
        WHERE event_type IN ({_DEATH_EVENT_PLACEHOLDERS})
          AND victim_id = ?
        {_valid_current_event_window_sql()}
        """,
        (
            *_DEATH_EVENT_TYPES,
            reliable_id,
            *_window_params(open_observed_at, upper_bound),
        ),
    ).fetchone()
    return int(row["count"] if row is not None else 0)


def _count_teamkills(
    connection: sqlite3.Connection,
    reliable_id: str,
    open_observed_at: str,
    upper_bound: str,
) -> int:
    row = connection.execute(
        f"""
        SELECT COUNT(*) AS count
        FROM player_log_events
        WHERE event_type = ?
          AND instigator_id = ?
          AND COALESCE(ai_instigator, 0) != 1
        {_valid_current_event_window_sql()}
        """,
        (
            player_log_events.EVENT_TYPE_TEAMKILL,
            reliable_id,
            *_window_params(open_observed_at, upper_bound),
        ),
    ).fetchone()
    return int(row["count"] if row is not None else 0)


def _last_known_faction(
    connection: sqlite3.Connection,
    reliable_id: str,
    open_observed_at: str,
    upper_bound: str,
) -> str | None:
    row = connection.execute(
        f"""
        SELECT faction
        FROM (
            SELECT player_faction AS faction, occurred_at, event_id
            FROM player_log_events
            WHERE event_type = ?
              AND player_id = ?
              AND player_faction IS NOT NULL
              AND player_faction != ''
            {_valid_current_event_window_sql()}
            UNION ALL
            SELECT victim_faction AS faction, occurred_at, event_id
            FROM player_log_events
            WHERE event_type IN ({_DEATH_EVENT_PLACEHOLDERS})
              AND victim_id = ?
              AND victim_faction IS NOT NULL
              AND victim_faction != ''
            {_valid_current_event_window_sql()}
            UNION ALL
            SELECT instigator_faction AS faction, occurred_at, event_id
            FROM player_log_events
            WHERE event_type IN ({_COMBAT_INSTIGATOR_EVENT_PLACEHOLDERS})
              AND instigator_id = ?
              AND COALESCE(ai_instigator, 0) != 1
              AND instigator_faction IS NOT NULL
              AND instigator_faction != ''
            {_valid_current_event_window_sql()}
        )
        ORDER BY occurred_at DESC, event_id DESC
        LIMIT 1
        """,
        (
            player_log_events.EVENT_TYPE_FACTION_JOIN,
            reliable_id,
            *_window_params(open_observed_at, upper_bound),
            *_DEATH_EVENT_TYPES,
            reliable_id,
            *_window_params(open_observed_at, upper_bound),
            *_COMBAT_INSTIGATOR_EVENT_TYPES,
            reliable_id,
            *_window_params(open_observed_at, upper_bound),
        ),
    ).fetchone()
    if row is None:
        return None
    return safe_player_text(row["faction"], max_length=80) or None
