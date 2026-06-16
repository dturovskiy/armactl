"""Instance-scoped persistent player registry for web moderation."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from armactl import admins_manager, paths
from armactl.redaction import redact_sensitive_text
from armactl.web.services.player_moderation import ModerationPlayer

PLAYER_REGISTRY_DB_NAME = "players.db"
MAX_PLAYER_TEXT_LENGTH = 160
DEFAULT_PLAYER_LIST_LIMIT = 100


@dataclass(frozen=True)
class KnownPlayer:
    """One persisted player identity/profile row."""

    reliable_id: str
    current_name: str
    first_seen_at: str
    last_seen_at: str
    seen_count: int
    last_source: str


@dataclass(frozen=True)
class KnownPlayerName:
    """One persisted historical nickname for a reliable player ID."""

    reliable_id: str
    name: str
    first_seen_at: str
    last_seen_at: str
    seen_count: int


@dataclass(frozen=True)
class PlayerSnapshotResult:
    """Summary of recording one current-player snapshot."""

    stored_count: int
    ignored_count: int


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_text(value: object, *, max_length: int = MAX_PLAYER_TEXT_LENGTH) -> str:
    text = redact_sensitive_text(value).replace("\r", " ").replace("\n", " ").strip()
    if len(text) > max_length:
        return f"{text[:max_length]}..."
    return text


def _reliable_id(value: object) -> str:
    candidate = str(value or "").strip()
    if admins_manager.STEAM_ID64_RE.fullmatch(candidate):
        return candidate
    if admins_manager.IDENTITY_ID_RE.fullmatch(candidate):
        return candidate
    return ""


def player_registry_db_path(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
) -> Path:
    """Return the instance-scoped player registry path."""
    return paths.instance_root(instance, data_root) / PLAYER_REGISTRY_DB_NAME


def ensure_player_registry_db(db_path: Path) -> Path:
    """Create the player registry database if needed."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS players (
                reliable_id TEXT PRIMARY KEY,
                current_name TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                seen_count INTEGER NOT NULL,
                last_source TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS player_names (
                reliable_id TEXT NOT NULL,
                name TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                seen_count INTEGER NOT NULL,
                PRIMARY KEY (reliable_id, name),
                FOREIGN KEY (reliable_id) REFERENCES players(reliable_id)
                    ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_player_names_name
            ON player_names(name)
            """
        )
    return db_path


def _connect_existing(db_path: Path) -> sqlite3.Connection | None:
    if not db_path.is_file():
        return None
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _player_from_row(row: sqlite3.Row) -> KnownPlayer:
    return KnownPlayer(
        reliable_id=str(row["reliable_id"]),
        current_name=str(row["current_name"]),
        first_seen_at=str(row["first_seen_at"]),
        last_seen_at=str(row["last_seen_at"]),
        seen_count=int(row["seen_count"]),
        last_source=str(row["last_source"]),
    )


def _name_from_row(row: sqlite3.Row) -> KnownPlayerName:
    return KnownPlayerName(
        reliable_id=str(row["reliable_id"]),
        name=str(row["name"]),
        first_seen_at=str(row["first_seen_at"]),
        last_seen_at=str(row["last_seen_at"]),
        seen_count=int(row["seen_count"]),
    )


def record_current_players_snapshot(
    db_path: Path,
    players: Iterable[ModerationPlayer],
    *,
    observed_at: str | None = None,
) -> PlayerSnapshotResult:
    """Record reliable current players, ignoring slot-only/unreliable rows."""
    timestamp = observed_at or _utc_now()
    reliable_players: dict[str, ModerationPlayer] = {}
    ignored_count = 0
    for player in players:
        reliable_id = _reliable_id(player.admin_reference or player.identity_id)
        if not reliable_id:
            ignored_count += 1
            continue
        reliable_players[reliable_id] = player

    ensure_player_registry_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        for reliable_id, player in reliable_players.items():
            name = _safe_text(player.display_name) or "Unknown player"
            source = _safe_text(player.source) or "unknown"
            connection.execute(
                """
                INSERT INTO players (
                    reliable_id,
                    current_name,
                    first_seen_at,
                    last_seen_at,
                    seen_count,
                    last_source
                )
                VALUES (?, ?, ?, ?, 1, ?)
                ON CONFLICT(reliable_id) DO UPDATE SET
                    current_name = excluded.current_name,
                    last_seen_at = excluded.last_seen_at,
                    seen_count = players.seen_count + 1,
                    last_source = excluded.last_source
                """,
                (reliable_id, name, timestamp, timestamp, source),
            )
            connection.execute(
                """
                INSERT INTO player_names (
                    reliable_id,
                    name,
                    first_seen_at,
                    last_seen_at,
                    seen_count
                )
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(reliable_id, name) DO UPDATE SET
                    last_seen_at = excluded.last_seen_at,
                    seen_count = player_names.seen_count + 1
                """,
                (reliable_id, name, timestamp, timestamp),
            )

    return PlayerSnapshotResult(
        stored_count=len(reliable_players),
        ignored_count=ignored_count,
    )


def list_known_players(
    db_path: Path,
    *,
    query: str = "",
    limit: int = DEFAULT_PLAYER_LIST_LIMIT,
) -> list[KnownPlayer]:
    """List known reliable players, optionally filtered by nickname or ID."""
    connection = _connect_existing(db_path)
    if connection is None:
        return []
    normalized_limit = max(1, min(int(limit), DEFAULT_PLAYER_LIST_LIMIT))
    normalized_query = _safe_text(query, max_length=120)
    try:
        with connection:
            if normalized_query:
                pattern = f"%{normalized_query}%"
                rows = connection.execute(
                    """
                    SELECT DISTINCT p.*
                    FROM players AS p
                    LEFT JOIN player_names AS n
                      ON n.reliable_id = p.reliable_id
                    WHERE p.reliable_id LIKE ?
                       OR p.current_name LIKE ?
                       OR n.name LIKE ?
                    ORDER BY p.last_seen_at DESC, p.current_name COLLATE NOCASE ASC
                    LIMIT ?
                    """,
                    (pattern, pattern, pattern, normalized_limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT *
                    FROM players
                    ORDER BY last_seen_at DESC, current_name COLLATE NOCASE ASC
                    LIMIT ?
                    """,
                    (normalized_limit,),
                ).fetchall()
    finally:
        connection.close()
    return [_player_from_row(row) for row in rows]


def get_known_player(db_path: Path, reliable_id: str) -> KnownPlayer | None:
    """Return one known player by reliable ID."""
    connection = _connect_existing(db_path)
    if connection is None:
        return None
    try:
        with connection:
            row = connection.execute(
                """
                SELECT *
                FROM players
                WHERE reliable_id = ?
                """,
                (_reliable_id(reliable_id),),
            ).fetchone()
    finally:
        connection.close()
    return _player_from_row(row) if row is not None else None


def list_player_names(db_path: Path, reliable_id: str) -> list[KnownPlayerName]:
    """List nickname history for one reliable player ID."""
    connection = _connect_existing(db_path)
    if connection is None:
        return []
    try:
        with connection:
            rows = connection.execute(
                """
                SELECT *
                FROM player_names
                WHERE reliable_id = ?
                ORDER BY last_seen_at DESC, name COLLATE NOCASE ASC
                """,
                (_reliable_id(reliable_id),),
            ).fetchall()
    finally:
        connection.close()
    return [_name_from_row(row) for row in rows]
