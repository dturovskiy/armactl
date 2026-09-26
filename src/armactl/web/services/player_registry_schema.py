"""SQLite schema primitives and migration orchestration for the player registry."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

MigrationStep = Callable[[sqlite3.Connection], None]


@dataclass(frozen=True)
class PlayerRegistryMigrationSteps:
    """Ordered player-registry migration operations supplied by the facade."""

    ensure_current_registry_schema: MigrationStep
    ensure_player_log_events_schema: MigrationStep
    ensure_player_sessions_schema: MigrationStep
    rebuild_player_sessions_schema_for_v6: MigrationStep
    rebuild_player_sessions_schema_for_v7: MigrationStep
    ensure_player_session_live_scan_windows_schema: MigrationStep
    drop_player_log_event_history_indexes: MigrationStep
    ensure_player_log_ingest_metadata_schema: MigrationStep
    ensure_player_session_lifecycle_boundaries_schema: MigrationStep
    ensure_player_session_pipeline_state_schema: MigrationStep
    backfill_player_log_event_sort_keys: MigrationStep


def ensure_private_db_file(db_path: Path, *, file_mode: int) -> None:
    """Create a private database file without widening an existing file's mode."""
    if db_path.exists():
        return

    fd = os.open(
        db_path,
        os.O_RDWR | os.O_CREAT | os.O_EXCL,
        file_mode,
    )
    os.close(fd)


def quote_identifier(identifier: str) -> str:
    """Return one safely quoted SQLite identifier."""
    return '"' + identifier.replace('"', '""') + '"'


def sql_text_values(values: tuple[str, ...]) -> str:
    """Return a quoted SQL literal list for trusted enum-like values."""
    return ", ".join("'" + value.replace("'", "''") + "'" for value in values)


def table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    """Return whether an SQLite table exists in the connected database."""
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


def table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    """Return column names for an existing SQLite table."""
    if not table_exists(connection, table_name):
        return set()
    rows = connection.execute(f"PRAGMA table_info({quote_identifier(table_name)})").fetchall()
    return {str(row[1]) for row in rows}


def ensure_columns(
    connection: sqlite3.Connection,
    table_name: str,
    columns: tuple[tuple[str, str], ...],
) -> None:
    """Add each missing column while preserving existing rows and values."""
    existing_columns = table_columns(connection, table_name)
    quoted_table = quote_identifier(table_name)
    for column_name, column_ddl in columns:
        if column_name not in existing_columns:
            connection.execute(f"ALTER TABLE {quoted_table} ADD COLUMN {column_ddl}")


def event_time_utc_microseconds(value: object) -> int | None:
    """Normalize one ISO timestamp into a deterministic UTC ordering key."""
    text = "" if value is None else str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = parsed - epoch
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


def ensure_player_registry_meta_schema(connection: sqlite3.Connection) -> None:
    """Create the schema-version metadata table when absent."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS player_registry_schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )


def read_player_registry_schema_version(connection: sqlite3.Connection) -> int:
    """Read the durable schema version, treating absent/invalid metadata as v0."""
    ensure_player_registry_meta_schema(connection)
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


def write_player_registry_schema_version(
    connection: sqlite3.Connection,
    version: int,
) -> None:
    """Persist a successfully completed player-registry schema version."""
    connection.execute(
        """
        INSERT INTO player_registry_schema_meta(key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        ("schema_version", str(version)),
    )


def run_player_registry_migrations(
    connection: sqlite3.Connection,
    *,
    target_version: int,
    steps: PlayerRegistryMigrationSteps,
) -> None:
    """Run the historical player-registry migrations in their original order."""
    current_version = read_player_registry_schema_version(connection)
    if current_version > target_version:
        raise RuntimeError(
            f"players.db schema version {current_version} is newer than supported "
            f"version {target_version}."
        )
    if current_version < 1:
        steps.ensure_current_registry_schema(connection)
        write_player_registry_schema_version(connection, 1)
        current_version = 1
    if current_version < 2:
        steps.ensure_player_log_events_schema(connection)
        write_player_registry_schema_version(connection, 2)
        current_version = 2
    if current_version < 3:
        steps.ensure_player_sessions_schema(connection)
        write_player_registry_schema_version(connection, 3)
        current_version = 3
    if current_version < 4:
        steps.ensure_player_log_events_schema(connection)
        write_player_registry_schema_version(connection, 4)
        current_version = 4
    if current_version < 5:
        steps.ensure_player_log_events_schema(connection)
        write_player_registry_schema_version(connection, 5)
        current_version = 5
    if current_version < 6:
        steps.rebuild_player_sessions_schema_for_v6(connection)
        write_player_registry_schema_version(connection, 6)
        current_version = 6
    if current_version < 7:
        steps.rebuild_player_sessions_schema_for_v7(connection)
        steps.ensure_player_session_live_scan_windows_schema(connection)
        write_player_registry_schema_version(connection, 7)
        current_version = 7
    if current_version < 8:
        steps.drop_player_log_event_history_indexes(connection)
        steps.ensure_player_log_events_schema(connection)
        write_player_registry_schema_version(connection, 8)
        current_version = 8
    if current_version < 9:
        steps.ensure_player_log_ingest_metadata_schema(connection)
        write_player_registry_schema_version(connection, 9)
        current_version = 9
    if current_version < 10:
        steps.ensure_player_sessions_schema(connection)
        steps.ensure_player_session_lifecycle_boundaries_schema(connection)
        write_player_registry_schema_version(connection, 10)
        current_version = 10
    if current_version < 11:
        steps.ensure_player_log_ingest_metadata_schema(connection)
        write_player_registry_schema_version(connection, 11)
        current_version = 11
    if current_version < 12:
        steps.ensure_player_log_ingest_metadata_schema(connection)
        steps.ensure_player_session_pipeline_state_schema(connection)
        write_player_registry_schema_version(connection, 12)
        current_version = 12
    if current_version < 13:
        steps.ensure_player_session_pipeline_state_schema(connection)
        write_player_registry_schema_version(connection, 13)
        current_version = 13
    if current_version < 14:
        steps.drop_player_log_event_history_indexes(connection)
        steps.ensure_player_log_events_schema(connection)
        steps.backfill_player_log_event_sort_keys(connection)
        write_player_registry_schema_version(connection, 14)


def ensure_player_registry_db(
    db_path: Path,
    *,
    file_mode: int,
    run_migrations: MigrationStep,
) -> Path:
    """Create/open the registry database and atomically run pending migrations."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    ensure_private_db_file(db_path, file_mode=file_mode)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        run_migrations(connection)
    db_path.chmod(file_mode)
    return db_path


def connect_existing(
    db_path: Path,
    *,
    ensure_database: Callable[[Path], Path],
) -> sqlite3.Connection | None:
    """Open an existing registry for writes after applying pending migrations."""
    if not db_path.is_file():
        return None
    ensure_database(db_path)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def connect_existing_readonly(db_path: Path) -> sqlite3.Connection | None:
    """Open an existing registry in query-only mode without migration or writes."""
    if not db_path.is_file():
        return None
    quoted_path = quote(db_path.resolve().as_posix(), safe="/:")
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{quoted_path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
    except (OSError, sqlite3.Error):
        if connection is not None:
            connection.close()
        return None
    return connection
