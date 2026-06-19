"""Instance-scoped persistent player registry for web moderation."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from armactl import paths
from armactl.web.services.player_identity import (
    normalize_reliable_player_id,
    safe_player_text,
)

PLAYER_REGISTRY_DB_NAME = "players.db"
PLAYER_REGISTRY_SCHEMA_VERSION = "1"
PRIVATE_PLAYER_REGISTRY_FILE_MODE = 0o600
DEFAULT_PLAYER_LIST_LIMIT = 100
_LEGACY_DEFAULT_TIMESTAMP = "1970-01-01T00:00:00+00:00"


@dataclass(frozen=True)
class PlayerObservation:
    """One reliable player observation ready for registry storage."""

    reliable_id: str
    display_name: str
    source: str


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


def player_registry_db_path(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
) -> Path:
    """Return the instance-scoped player registry path."""
    return paths.instance_root(instance, data_root) / PLAYER_REGISTRY_DB_NAME


def _ensure_private_db_file(db_path: Path) -> None:
    if db_path.exists():
        return

    fd = os.open(
        db_path,
        os.O_RDWR | os.O_CREAT | os.O_EXCL,
        PRIVATE_PLAYER_REGISTRY_FILE_MODE,
    )
    os.close(fd)


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = ?
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    if not _table_exists(connection, table_name):
        return set()
    rows = connection.execute(
        f"PRAGMA table_info({_quote_identifier(table_name)})"
    ).fetchall()
    return {str(row[1]) for row in rows}


def _ensure_columns(
    connection: sqlite3.Connection,
    table_name: str,
    columns: tuple[tuple[str, str], ...],
) -> None:
    existing_columns = _table_columns(connection, table_name)
    quoted_table = _quote_identifier(table_name)
    for column_name, column_ddl in columns:
        if column_name not in existing_columns:
            connection.execute(f"ALTER TABLE {quoted_table} ADD COLUMN {column_ddl}")


def _ensure_player_registry_meta_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS player_registry_schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )


def _ensure_players_schema(connection: sqlite3.Connection) -> None:
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
    _ensure_columns(
        connection,
        "players",
        (
            ("reliable_id", "reliable_id TEXT NOT NULL DEFAULT 'legacy'"),
            ("current_name", "current_name TEXT NOT NULL DEFAULT 'Unknown player'"),
            (
                "first_seen_at",
                f"first_seen_at TEXT NOT NULL DEFAULT '{_LEGACY_DEFAULT_TIMESTAMP}'",
            ),
            (
                "last_seen_at",
                f"last_seen_at TEXT NOT NULL DEFAULT '{_LEGACY_DEFAULT_TIMESTAMP}'",
            ),
            ("seen_count", "seen_count INTEGER NOT NULL DEFAULT 1"),
            ("last_source", "last_source TEXT NOT NULL DEFAULT 'unknown'"),
        ),
    )


def _ensure_player_names_schema(connection: sqlite3.Connection) -> None:
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
    _ensure_columns(
        connection,
        "player_names",
        (
            ("reliable_id", "reliable_id TEXT NOT NULL DEFAULT 'legacy'"),
            ("name", "name TEXT NOT NULL DEFAULT 'Unknown player'"),
            (
                "first_seen_at",
                f"first_seen_at TEXT NOT NULL DEFAULT '{_LEGACY_DEFAULT_TIMESTAMP}'",
            ),
            (
                "last_seen_at",
                f"last_seen_at TEXT NOT NULL DEFAULT '{_LEGACY_DEFAULT_TIMESTAMP}'",
            ),
            ("seen_count", "seen_count INTEGER NOT NULL DEFAULT 1"),
        ),
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_player_names_name
        ON player_names(name)
        """
    )


def _ensure_current_player_registry_schema(connection: sqlite3.Connection) -> None:
    _ensure_player_registry_meta_schema(connection)
    _ensure_players_schema(connection)
    _ensure_player_names_schema(connection)


def _read_player_registry_schema_version(connection: sqlite3.Connection) -> int:
    _ensure_player_registry_meta_schema(connection)
    row = connection.execute(
        """
        SELECT value
        FROM player_registry_schema_meta
        WHERE key = 'schema_version'
        """
    ).fetchone()
    if row is None:
        return 0
    try:
        return int(str(row[0]))
    except ValueError:
        return 0


def _write_player_registry_schema_version(
    connection: sqlite3.Connection,
    version: int,
) -> None:
    connection.execute(
        """
        INSERT INTO player_registry_schema_meta(key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        ("schema_version", str(version)),
    )


def _run_player_registry_migrations(connection: sqlite3.Connection) -> None:
    target_version = int(PLAYER_REGISTRY_SCHEMA_VERSION)
    current_version = _read_player_registry_schema_version(connection)
    if current_version > target_version:
        raise RuntimeError(
            f"players.db schema version {current_version} is newer than supported "
            f"version {target_version}."
        )
    if current_version < 1:
        _ensure_current_player_registry_schema(connection)
        _write_player_registry_schema_version(connection, 1)


def ensure_player_registry_db(db_path: Path) -> Path:
    """Create/open the player registry database and run schema migrations."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_private_db_file(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        _run_player_registry_migrations(connection)
    db_path.chmod(PRIVATE_PLAYER_REGISTRY_FILE_MODE)
    return db_path


def _connect_existing(db_path: Path) -> sqlite3.Connection | None:
    if not db_path.is_file():
        return None
    ensure_player_registry_db(db_path)
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
    observations: Iterable[PlayerObservation],
    *,
    observed_at: str | None = None,
) -> PlayerSnapshotResult:
    """Record reliable current players, ignoring slot-only/unreliable rows."""
    timestamp = observed_at or _utc_now()
    reliable_players: dict[str, PlayerObservation] = {}
    ignored_count = 0
    for observation in observations:
        reliable_id = normalize_reliable_player_id(observation.reliable_id)
        if not reliable_id:
            ignored_count += 1
            continue
        reliable_players[reliable_id] = observation

    ensure_player_registry_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        for reliable_id, observation in reliable_players.items():
            name = safe_player_text(observation.display_name) or "Unknown player"
            source = safe_player_text(observation.source) or "unknown"
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
    normalized_query = safe_player_text(query, max_length=120)
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
                (normalize_reliable_player_id(reliable_id),),
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
                (normalize_reliable_player_id(reliable_id),),
            ).fetchall()
    finally:
        connection.close()
    return [_name_from_row(row) for row in rows]
