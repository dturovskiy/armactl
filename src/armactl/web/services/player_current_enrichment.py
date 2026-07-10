"""Read-only current-player roster enrichment guard from stored player evidence."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from armactl import paths
from armactl.web.services import player_registry
from armactl.web.services.player_identity import (
    normalize_reliable_player_id,
    safe_player_text,
)

CURRENT_STATS_UNAVAILABLE_REASON = (
    "Stats pending play-session contract; no proven session-scoped stats; "
    "automatic log freshness is not available yet."
)

_PLAYER_SESSIONS_REQUIRED_COLUMNS = frozenset(
    {
        "session_id",
        "reliable_id",
        "open_observed_at",
        "status",
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


def load_current_player_enrichment(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
    reliable_ids: tuple[str, ...] | list[str] = (),
    now_at: str | None = None,
) -> dict[str, CurrentPlayerEnrichment]:
    """Return read-only guarded enrichment keyed by reliable player ID."""
    _ = now_at
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
        return {
            reliable_id: enrichment
            for reliable_id in normalized_ids
            if (enrichment := _load_one_enrichment(connection, reliable_id)) is not None
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
    return _table_has_columns(
        connection,
        "player_sessions",
        _PLAYER_SESSIONS_REQUIRED_COLUMNS,
    )


def _table_has_columns(
    connection: sqlite3.Connection,
    table_name: str,
    required_columns: frozenset[str],
) -> bool:
    if table_name != "player_sessions":
        return False
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    columns = {str(row["name"]) for row in rows}
    return bool(columns) and required_columns.issubset(columns)


def _load_one_enrichment(
    connection: sqlite3.Connection,
    reliable_id: str,
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
        return None

    open_observed_at = safe_player_text(session["open_observed_at"], max_length=80)
    if not open_observed_at:
        return None

    return CurrentPlayerEnrichment(first_observed_at=open_observed_at)
