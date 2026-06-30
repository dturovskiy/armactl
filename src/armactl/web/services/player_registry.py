"""Instance-scoped persistent player registry for web moderation."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from armactl import paths
from armactl.player_log_events import (
    CONFIDENCE_HIGH,
    CONFIDENCE_MEDIUM,
    EVENT_TYPE_KILL,
    EVENT_TYPE_OTHER_DEATH,
    EVENT_TYPE_SUICIDE,
    EVENT_TYPE_TEAMKILL,
    SOURCE_BACKEND_AUTH,
    SOURCE_NETWORK_PLAYER_UPDATE,
    SOURCE_SCRIPT_FACTION_JOIN,
    SOURCE_SCRIPT_KILL,
    SOURCE_SERVER_ADMIN_TOOLS_KILL,
    PlayerLogEvent,
)
from armactl.web.services.player_identity import (
    normalize_reliable_player_id,
    safe_player_text,
)

PLAYER_REGISTRY_DB_NAME = "players.db"
PLAYER_REGISTRY_SCHEMA_VERSION = "3"
PRIVATE_PLAYER_REGISTRY_FILE_MODE = 0o600
DEFAULT_PLAYER_HISTORY_EVENT_LIMIT = 100
MAX_PLAYER_HISTORY_EVENT_LIMIT = 250
DEFAULT_PLAYER_LIST_LIMIT = 100
_LEGACY_DEFAULT_TIMESTAMP = "1970-01-01T00:00:00+00:00"
PLAYER_LOG_EVENT_TEXT_MAX_LENGTH = 240
PLAYER_LOG_EVENT_REF_MAX_LENGTH = 240
PLAYER_LOG_EVENT_KEY_VERSION = "v1"
_IPV4_ADDRESS_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?\b")
_BRACKETED_IPV6_ADDRESS_RE = re.compile(r"\[[0-9A-Fa-f:.]{2,}\](?::\d{1,5})?")
_PLAYER_LOG_EVENT_INSERT_COLUMNS = (
    "event_key",
    "event_type",
    "source",
    "source_ref",
    "confidence",
    "observed_at",
    "log_timestamp",
    "player_id",
    "player_name",
    "session_player_id",
    "rpl_identity",
    "player_faction",
    "faction_resource",
    "victim_id",
    "victim_name",
    "victim_session_player_id",
    "victim_faction",
    "instigator_id",
    "instigator_name",
    "instigator_session_player_id",
    "instigator_faction",
    "teamkill",
    "suicide",
    "ai_instigator",
    "damage_type",
    "hit_zone",
    "distance_m",
    "created_at",
)
_PLAYER_LOG_EVENT_DEDUPE_COLUMNS = tuple(
    column
    for column in _PLAYER_LOG_EVENT_INSERT_COLUMNS
    if column not in {"event_key", "created_at"}
)
PLAYER_SESSION_STATUS_OPEN = "open"
PLAYER_SESSION_STATUS_CLOSED = "closed"
PLAYER_SESSION_STATUSES = (
    PLAYER_SESSION_STATUS_OPEN,
    PLAYER_SESSION_STATUS_CLOSED,
)
PLAYER_SESSION_CONFIDENCE_HIGH = CONFIDENCE_HIGH
PLAYER_SESSION_CONFIDENCE_MEDIUM = CONFIDENCE_MEDIUM
PLAYER_SESSION_CONFIDENCE_LOW = "low"
PLAYER_SESSION_CONFIDENCES = (
    PLAYER_SESSION_CONFIDENCE_HIGH,
    PLAYER_SESSION_CONFIDENCE_MEDIUM,
    PLAYER_SESSION_CONFIDENCE_LOW,
)
PLAYER_SESSION_SOURCE_BACKEND_AUTH = SOURCE_BACKEND_AUTH
PLAYER_SESSION_SOURCE_NETWORK_PLAYER_UPDATE = SOURCE_NETWORK_PLAYER_UPDATE
PLAYER_SESSION_SOURCE_RCON_ROSTER = "rcon.roster"
PLAYER_SESSION_SOURCE_SCRIPT_FACTION_JOIN = SOURCE_SCRIPT_FACTION_JOIN
PLAYER_SESSION_SOURCE_SCRIPT_KILL = SOURCE_SCRIPT_KILL
PLAYER_SESSION_SOURCE_SERVER_ADMIN_TOOLS_KILL = SOURCE_SERVER_ADMIN_TOOLS_KILL
PLAYER_SESSION_SOURCE_SERVICE_LIFECYCLE = "service.lifecycle"
PLAYER_SESSION_SOURCE_SCANNER_CHECKPOINT = "scanner.checkpoint"
PLAYER_SESSION_SOURCE_MANUAL_IMPORT = "manual_import"
PLAYER_SESSION_SOURCES = (
    PLAYER_SESSION_SOURCE_BACKEND_AUTH,
    PLAYER_SESSION_SOURCE_NETWORK_PLAYER_UPDATE,
    PLAYER_SESSION_SOURCE_RCON_ROSTER,
    PLAYER_SESSION_SOURCE_SCRIPT_FACTION_JOIN,
    PLAYER_SESSION_SOURCE_SCRIPT_KILL,
    PLAYER_SESSION_SOURCE_SERVER_ADMIN_TOOLS_KILL,
    PLAYER_SESSION_SOURCE_SERVICE_LIFECYCLE,
    PLAYER_SESSION_SOURCE_SCANNER_CHECKPOINT,
    PLAYER_SESSION_SOURCE_MANUAL_IMPORT,
)
PLAYER_SESSION_END_REASON_DISCONNECT = "disconnect"
PLAYER_SESSION_END_REASON_SERVER_BOUNDARY = "server_boundary"
PLAYER_SESSION_END_REASON_STALE_ABSENCE = "stale_absence"
PLAYER_SESSION_END_REASON_SCANNER_CHECKPOINT = "scanner_checkpoint"
PLAYER_SESSION_END_REASON_IMPORT_WINDOW = "import_window"
PLAYER_SESSION_END_REASON_UNKNOWN = "unknown"
PLAYER_SESSION_END_REASONS = (
    PLAYER_SESSION_END_REASON_DISCONNECT,
    PLAYER_SESSION_END_REASON_SERVER_BOUNDARY,
    PLAYER_SESSION_END_REASON_STALE_ABSENCE,
    PLAYER_SESSION_END_REASON_SCANNER_CHECKPOINT,
    PLAYER_SESSION_END_REASON_IMPORT_WINDOW,
    PLAYER_SESSION_END_REASON_UNKNOWN,
)


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
class PlayerSummary:
    """One known player with lightweight event-derived counters."""

    reliable_id: str
    current_name: str
    first_seen_at: str
    last_seen_at: str
    seen_count: int
    last_source: str
    faction: str
    event_count: int
    kill_count: int
    death_count: int
    teamkill_count: int
    suicide_count: int


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


@dataclass(frozen=True)
class PlayerLogEventIngestResult:
    """Summary of recording parsed player log events."""

    stored_count: int
    duplicate_count: int


@dataclass(frozen=True)
class PlayerLogEventRecord:
    """One sanitized persisted player log event row for read-only views."""

    event_id: int
    event_type: str
    source: str
    source_ref: str
    confidence: str
    observed_at: str
    log_timestamp: str
    player_id: str
    player_name: str
    session_player_id: str
    rpl_identity: str
    player_faction: str
    faction_resource: str
    victim_id: str
    victim_name: str
    victim_session_player_id: str
    victim_faction: str
    instigator_id: str
    instigator_name: str
    instigator_session_player_id: str
    instigator_faction: str
    teamkill: bool | None
    suicide: bool | None
    ai_instigator: bool | None
    damage_type: str
    hit_zone: str
    distance_m: float | None
    created_at: str

    @property
    def event_time(self) -> str:
        return self.observed_at or self.created_at


@dataclass(frozen=True)
class PlayerSessionRecord:
    """One sanitized persisted player session row."""

    session_id: int
    reliable_id: str
    name_at_open: str
    name_last: str
    open_observed_at: str
    last_seen_at: str
    close_observed_at: str
    status: str
    open_source: str
    open_source_ref: str
    last_seen_source: str
    last_seen_source_ref: str
    close_source: str
    close_source_ref: str
    open_confidence: str
    last_seen_confidence: str
    close_confidence: str
    end_reason: str
    rpl_identity: str
    connection_id: str
    session_player_id: str
    be_slot: str
    faction: str
    side: str
    scanner_checkpoint_source: str
    scanner_checkpoint_ref: str
    scanner_checkpoint_at: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class PlayerSessionWriteResult:
    """Summary of one controlled player session write attempt."""

    written: bool
    created: bool = False
    updated: bool = False
    closed: bool = False
    ignored_count: int = 0
    session: PlayerSessionRecord | None = None


def _bounded_player_history_limit(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return DEFAULT_PLAYER_HISTORY_EVENT_LIMIT
    return max(1, min(parsed, MAX_PLAYER_HISTORY_EVENT_LIMIT))


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


def _sql_text_values(values: tuple[str, ...]) -> str:
    return ", ".join("'" + value.replace("'", "''") + "'" for value in values)


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
    rows = connection.execute(f"PRAGMA table_info({_quote_identifier(table_name)})").fetchall()
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


def _ensure_player_log_events_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS player_log_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_key TEXT NOT NULL UNIQUE,
            event_type TEXT NOT NULL,
            source TEXT NOT NULL,
            source_ref TEXT,
            confidence TEXT NOT NULL,
            observed_at TEXT,
            log_timestamp TEXT,
            player_id TEXT,
            player_name TEXT,
            session_player_id TEXT,
            rpl_identity TEXT,
            player_faction TEXT,
            faction_resource TEXT,
            victim_id TEXT,
            victim_name TEXT,
            victim_session_player_id TEXT,
            victim_faction TEXT,
            instigator_id TEXT,
            instigator_name TEXT,
            instigator_session_player_id TEXT,
            instigator_faction TEXT,
            teamkill INTEGER,
            suicide INTEGER,
            ai_instigator INTEGER,
            damage_type TEXT,
            hit_zone TEXT,
            distance_m REAL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_player_log_events_observed_at
        ON player_log_events(observed_at)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_player_log_events_type
        ON player_log_events(event_type)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_player_log_events_player
        ON player_log_events(player_id)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_player_log_events_victim
        ON player_log_events(victim_id)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_player_log_events_instigator
        ON player_log_events(instigator_id)
        """
    )


def _ensure_player_sessions_schema(connection: sqlite3.Connection) -> None:
    status_values = _sql_text_values(PLAYER_SESSION_STATUSES)
    confidence_values = _sql_text_values(PLAYER_SESSION_CONFIDENCES)
    end_reason_values = _sql_text_values(PLAYER_SESSION_END_REASONS)
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS player_sessions (
            session_id INTEGER PRIMARY KEY AUTOINCREMENT,
            reliable_id TEXT NOT NULL,
            name_at_open TEXT NOT NULL,
            name_last TEXT NOT NULL,
            open_observed_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            close_observed_at TEXT,
            status TEXT NOT NULL CHECK(status IN ({status_values})),
            open_source TEXT NOT NULL,
            open_source_ref TEXT,
            last_seen_source TEXT NOT NULL,
            last_seen_source_ref TEXT,
            close_source TEXT,
            close_source_ref TEXT,
            open_confidence TEXT NOT NULL CHECK(open_confidence IN ({confidence_values})),
            last_seen_confidence TEXT NOT NULL CHECK(
                last_seen_confidence IN ({confidence_values})
            ),
            close_confidence TEXT CHECK(
                close_confidence IS NULL OR close_confidence IN ({confidence_values})
            ),
            end_reason TEXT CHECK(end_reason IS NULL OR end_reason IN ({end_reason_values})),
            rpl_identity TEXT,
            connection_id TEXT,
            session_player_id TEXT,
            be_slot TEXT,
            faction TEXT,
            side TEXT,
            scanner_checkpoint_source TEXT,
            scanner_checkpoint_ref TEXT,
            scanner_checkpoint_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK(length(reliable_id) > 0),
            CHECK(length(open_observed_at) > 0),
            CHECK(length(last_seen_at) > 0),
            FOREIGN KEY (reliable_id) REFERENCES players(reliable_id)
                ON DELETE CASCADE
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_player_sessions_reliable_id
        ON player_sessions(reliable_id)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_player_sessions_status
        ON player_sessions(status)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_player_sessions_open_observed_at
        ON player_sessions(open_observed_at)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_player_sessions_last_seen_at
        ON player_sessions(last_seen_at)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_player_sessions_close_observed_at
        ON player_sessions(close_observed_at)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_player_sessions_open_source
        ON player_sessions(open_source)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_player_sessions_last_seen_source
        ON player_sessions(last_seen_source)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_player_sessions_close_source
        ON player_sessions(close_source)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_player_sessions_scanner_checkpoint
        ON player_sessions(scanner_checkpoint_source, scanner_checkpoint_ref)
        """
    )
    connection.execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_player_sessions_one_open_per_reliable_id
        ON player_sessions(reliable_id)
        WHERE status = '{PLAYER_SESSION_STATUS_OPEN}'
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
        current_version = 1
    if current_version < 2:
        _ensure_player_log_events_schema(connection)
        _write_player_registry_schema_version(connection, 2)
        current_version = 2
    if current_version < 3:
        _ensure_player_sessions_schema(connection)
        _write_player_registry_schema_version(connection, 3)


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


def _player_log_event_record_from_row(row: sqlite3.Row) -> PlayerLogEventRecord:
    return PlayerLogEventRecord(
        event_id=int(row["event_id"]),
        event_type=_safe_event_text(row["event_type"]) or "unknown",
        source=_safe_event_text(row["source"]) or "unknown",
        source_ref=_safe_event_source_ref(row["source_ref"]) or "",
        confidence=_safe_event_text(row["confidence"]) or "unknown",
        observed_at=_safe_event_text(row["observed_at"]) or "",
        log_timestamp=_safe_event_text(row["log_timestamp"]) or "",
        player_id=_safe_event_player_id(row["player_id"]) or "",
        player_name=_safe_event_text(row["player_name"]) or "",
        session_player_id=_safe_event_text(row["session_player_id"]) or "",
        rpl_identity=_safe_event_text(row["rpl_identity"]) or "",
        player_faction=_safe_event_text(row["player_faction"]) or "",
        faction_resource=_safe_event_text(row["faction_resource"]) or "",
        victim_id=_safe_event_player_id(row["victim_id"]) or "",
        victim_name=_safe_event_text(row["victim_name"]) or "",
        victim_session_player_id=_safe_event_text(row["victim_session_player_id"]) or "",
        victim_faction=_safe_event_text(row["victim_faction"]) or "",
        instigator_id=_safe_event_player_id(row["instigator_id"]) or "",
        instigator_name=_safe_event_text(row["instigator_name"]) or "",
        instigator_session_player_id=_safe_event_text(
            row["instigator_session_player_id"],
        )
        or "",
        instigator_faction=_safe_event_text(row["instigator_faction"]) or "",
        teamkill=_event_bool_from_row(row["teamkill"]),
        suicide=_event_bool_from_row(row["suicide"]),
        ai_instigator=_event_bool_from_row(row["ai_instigator"]),
        damage_type=_safe_event_text(row["damage_type"]) or "",
        hit_zone=_safe_event_text(row["hit_zone"]) or "",
        distance_m=_event_distance(row["distance_m"]),
        created_at=_safe_event_text(row["created_at"]) or "",
    )


def _player_session_record_from_row(row: sqlite3.Row) -> PlayerSessionRecord:
    return PlayerSessionRecord(
        session_id=int(row["session_id"]),
        reliable_id=normalize_reliable_player_id(row["reliable_id"]),
        name_at_open=_safe_session_text(row["name_at_open"]) or "Unknown player",
        name_last=_safe_session_text(row["name_last"]) or "Unknown player",
        open_observed_at=_safe_session_text(row["open_observed_at"], max_length=80),
        last_seen_at=_safe_session_text(row["last_seen_at"], max_length=80),
        close_observed_at=_safe_session_text(row["close_observed_at"], max_length=80),
        status=_safe_session_text(row["status"], max_length=40),
        open_source=_safe_session_text(row["open_source"], max_length=80),
        open_source_ref=_safe_session_source_ref(row["open_source_ref"]),
        last_seen_source=_safe_session_text(row["last_seen_source"], max_length=80),
        last_seen_source_ref=_safe_session_source_ref(row["last_seen_source_ref"]),
        close_source=_safe_session_text(row["close_source"], max_length=80),
        close_source_ref=_safe_session_source_ref(row["close_source_ref"]),
        open_confidence=_safe_session_text(row["open_confidence"], max_length=40),
        last_seen_confidence=_safe_session_text(row["last_seen_confidence"], max_length=40),
        close_confidence=_safe_session_text(row["close_confidence"], max_length=40),
        end_reason=_safe_session_text(row["end_reason"], max_length=80),
        rpl_identity=_safe_session_correlation(row["rpl_identity"]),
        connection_id=_safe_session_correlation(row["connection_id"]),
        session_player_id=_safe_session_correlation(row["session_player_id"]),
        be_slot=_safe_session_correlation(row["be_slot"]),
        faction=_safe_session_text(row["faction"], max_length=80),
        side=_safe_session_text(row["side"], max_length=80),
        scanner_checkpoint_source=_safe_session_source(
            row["scanner_checkpoint_source"],
            default="",
        ),
        scanner_checkpoint_ref=_safe_session_source_ref(row["scanner_checkpoint_ref"]),
        scanner_checkpoint_at=_safe_session_text(
            row["scanner_checkpoint_at"],
            max_length=80,
        ),
        created_at=_safe_session_text(row["created_at"], max_length=80),
        updated_at=_safe_session_text(row["updated_at"], max_length=80),
    )


def _fetch_player_session_by_id(
    connection: sqlite3.Connection,
    session_id: object,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT *
        FROM player_sessions
        WHERE session_id = ?
        """,
        (session_id,),
    ).fetchone()


def _fetch_open_player_session(
    connection: sqlite3.Connection,
    reliable_id: str,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT *
        FROM player_sessions
        WHERE reliable_id = ?
          AND status = ?
        ORDER BY session_id DESC
        LIMIT 1
        """,
        (reliable_id, PLAYER_SESSION_STATUS_OPEN),
    ).fetchone()


def _current_player_name_for_session(
    connection: sqlite3.Connection,
    reliable_id: str,
) -> str:
    row = connection.execute(
        """
        SELECT current_name
        FROM players
        WHERE reliable_id = ?
        """,
        (reliable_id,),
    ).fetchone()
    if row is None:
        return ""
    return _safe_session_text(row["current_name"]) or ""


def _safe_session_text(
    value: object,
    *,
    max_length: int = PLAYER_LOG_EVENT_TEXT_MAX_LENGTH,
) -> str:
    text = safe_player_text(value, max_length=max_length)
    text = _IPV4_ADDRESS_RE.sub("***", text)
    text = _BRACKETED_IPV6_ADDRESS_RE.sub("***", text).strip()
    return text


def _safe_session_source(value: object, *, default: str = "unknown") -> str:
    raw_text = "" if value is None else str(value).replace("\\", "/")
    if "/" in raw_text:
        raw_text = raw_text.rsplit("/", 1)[-1]
    return _safe_session_text(raw_text, max_length=80) or default


def _safe_session_source_ref(value: object) -> str:
    raw_text = "" if value is None else str(value).replace("\\", "/")
    if "/" in raw_text:
        raw_text = raw_text.rsplit("/", 1)[-1]
    return _safe_session_text(raw_text, max_length=PLAYER_LOG_EVENT_REF_MAX_LENGTH)


def _safe_session_timestamp(value: object, *, fallback: str) -> str:
    return _safe_session_text(value, max_length=80) or fallback


def _safe_session_confidence(value: object, *, default: str) -> str:
    confidence = _safe_session_text(value, max_length=40)
    if confidence in PLAYER_SESSION_CONFIDENCES:
        return confidence
    return default


def _safe_session_end_reason(value: object) -> str:
    reason = _safe_session_text(value, max_length=80)
    if reason in PLAYER_SESSION_END_REASONS:
        return reason
    return PLAYER_SESSION_END_REASON_UNKNOWN


def _safe_session_correlation(value: object) -> str:
    raw_text = "" if value is None else str(value).strip()
    if "/" in raw_text or "\\" in raw_text:
        return ""
    text = _safe_session_text(raw_text, max_length=80)
    if text == "***":
        return ""
    return text


def _nullable(value: str) -> str | None:
    return value or None


def _session_update_value(row: sqlite3.Row, column: str, value: str) -> str | None:
    if value:
        return value
    existing = row[column]
    return str(existing) if existing not in (None, "") else None


def _record_reliable_player_observation(
    connection: sqlite3.Connection,
    *,
    reliable_id: object,
    display_name: object,
    source: object,
    observed_at: str,
) -> bool:
    normalized_id = normalize_reliable_player_id(reliable_id)
    if not normalized_id:
        return False

    name = safe_player_text(display_name) or "Unknown player"
    safe_source = safe_player_text(source) or "unknown"
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
        (normalized_id, name, observed_at, observed_at, safe_source),
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
        (normalized_id, name, observed_at, observed_at),
    )
    return True


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
            _record_reliable_player_observation(
                connection,
                reliable_id=reliable_id,
                display_name=observation.display_name,
                source=observation.source,
                observed_at=timestamp,
            )

    return PlayerSnapshotResult(
        stored_count=len(reliable_players),
        ignored_count=ignored_count,
    )


def observe_player_session(
    db_path: Path,
    *,
    reliable_id: object,
    display_name: object = "",
    source: object = PLAYER_SESSION_SOURCE_MANUAL_IMPORT,
    observed_at: str | None = None,
    source_ref: object = None,
    confidence: object = PLAYER_SESSION_CONFIDENCE_MEDIUM,
    rpl_identity: object = None,
    connection_id: object = None,
    session_player_id: object = None,
    be_slot: object = None,
    faction: object = None,
    side: object = None,
    scanner_checkpoint_source: object = None,
    scanner_checkpoint_ref: object = None,
    scanner_checkpoint_at: object = None,
) -> PlayerSessionWriteResult:
    """Open or update one reliable player's currently open session."""
    normalized_id = normalize_reliable_player_id(reliable_id)
    if not normalized_id:
        return PlayerSessionWriteResult(written=False, ignored_count=1)

    timestamp = _safe_session_timestamp(observed_at, fallback=_utc_now())
    safe_name = _safe_session_text(display_name)
    safe_source = _safe_session_source(source)
    safe_source_ref = _safe_session_source_ref(source_ref)
    safe_confidence = _safe_session_confidence(
        confidence,
        default=PLAYER_SESSION_CONFIDENCE_MEDIUM,
    )
    safe_rpl_identity = _safe_session_correlation(rpl_identity)
    safe_connection_id = _safe_session_correlation(connection_id)
    safe_session_player_id = _safe_session_correlation(session_player_id)
    safe_be_slot = _safe_session_correlation(be_slot)
    safe_faction = _safe_session_text(faction, max_length=80)
    safe_side = _safe_session_text(side, max_length=80)
    safe_checkpoint_source = _safe_session_source(scanner_checkpoint_source, default="")
    safe_checkpoint_ref = _safe_session_source_ref(scanner_checkpoint_ref)
    safe_checkpoint_at = _safe_session_text(scanner_checkpoint_at, max_length=80)

    ensure_player_registry_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        existing_name = _current_player_name_for_session(connection, normalized_id)
        name_for_observation = safe_name or existing_name or "Unknown player"
        _record_reliable_player_observation(
            connection,
            reliable_id=normalized_id,
            display_name=name_for_observation,
            source=safe_source,
            observed_at=timestamp,
        )
        open_row = _fetch_open_player_session(connection, normalized_id)
        if open_row is None:
            cursor = connection.execute(
                """
                INSERT INTO player_sessions(
                    reliable_id,
                    name_at_open,
                    name_last,
                    open_observed_at,
                    last_seen_at,
                    status,
                    open_source,
                    open_source_ref,
                    last_seen_source,
                    last_seen_source_ref,
                    open_confidence,
                    last_seen_confidence,
                    rpl_identity,
                    connection_id,
                    session_player_id,
                    be_slot,
                    faction,
                    side,
                    scanner_checkpoint_source,
                    scanner_checkpoint_ref,
                    scanner_checkpoint_at,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_id,
                    name_for_observation,
                    name_for_observation,
                    timestamp,
                    timestamp,
                    PLAYER_SESSION_STATUS_OPEN,
                    safe_source,
                    _nullable(safe_source_ref),
                    safe_source,
                    _nullable(safe_source_ref),
                    safe_confidence,
                    safe_confidence,
                    _nullable(safe_rpl_identity),
                    _nullable(safe_connection_id),
                    _nullable(safe_session_player_id),
                    _nullable(safe_be_slot),
                    _nullable(safe_faction),
                    _nullable(safe_side),
                    _nullable(safe_checkpoint_source),
                    _nullable(safe_checkpoint_ref),
                    _nullable(safe_checkpoint_at),
                    timestamp,
                    timestamp,
                ),
            )
            row = _fetch_player_session_by_id(connection, cursor.lastrowid)
            return PlayerSessionWriteResult(
                written=True,
                created=True,
                session=_player_session_record_from_row(row),
            )

        session_id = int(open_row["session_id"])
        name_last = safe_name or str(open_row["name_last"] or "Unknown player")
        connection.execute(
            """
            UPDATE player_sessions
            SET name_last = ?,
                last_seen_at = ?,
                last_seen_source = ?,
                last_seen_source_ref = ?,
                last_seen_confidence = ?,
                rpl_identity = ?,
                connection_id = ?,
                session_player_id = ?,
                be_slot = ?,
                faction = ?,
                side = ?,
                scanner_checkpoint_source = ?,
                scanner_checkpoint_ref = ?,
                scanner_checkpoint_at = ?,
                updated_at = ?
            WHERE session_id = ?
            """,
            (
                name_last,
                timestamp,
                safe_source,
                _nullable(safe_source_ref),
                safe_confidence,
                _session_update_value(open_row, "rpl_identity", safe_rpl_identity),
                _session_update_value(open_row, "connection_id", safe_connection_id),
                _session_update_value(
                    open_row,
                    "session_player_id",
                    safe_session_player_id,
                ),
                _session_update_value(open_row, "be_slot", safe_be_slot),
                _session_update_value(open_row, "faction", safe_faction),
                _session_update_value(open_row, "side", safe_side),
                _session_update_value(
                    open_row,
                    "scanner_checkpoint_source",
                    safe_checkpoint_source,
                ),
                _session_update_value(open_row, "scanner_checkpoint_ref", safe_checkpoint_ref),
                _session_update_value(open_row, "scanner_checkpoint_at", safe_checkpoint_at),
                timestamp,
                session_id,
            ),
        )
        row = _fetch_player_session_by_id(connection, session_id)
        return PlayerSessionWriteResult(
            written=True,
            updated=True,
            session=_player_session_record_from_row(row),
        )


def close_player_session(
    db_path: Path,
    *,
    reliable_id: object,
    close_observed_at: str | None = None,
    source: object = PLAYER_SESSION_SOURCE_MANUAL_IMPORT,
    source_ref: object = None,
    confidence: object = PLAYER_SESSION_CONFIDENCE_LOW,
    end_reason: object = PLAYER_SESSION_END_REASON_UNKNOWN,
) -> PlayerSessionWriteResult:
    """Close one reliable player's currently open session when it exists."""
    normalized_id = normalize_reliable_player_id(reliable_id)
    if not normalized_id:
        return PlayerSessionWriteResult(written=False, ignored_count=1)

    connection = _connect_existing(db_path)
    if connection is None:
        return PlayerSessionWriteResult(written=False)

    close_timestamp = _safe_session_timestamp(close_observed_at, fallback=_utc_now())
    safe_source = _safe_session_source(source)
    safe_source_ref = _safe_session_source_ref(source_ref)
    safe_confidence = _safe_session_confidence(
        confidence,
        default=PLAYER_SESSION_CONFIDENCE_LOW,
    )
    safe_end_reason = _safe_session_end_reason(end_reason)
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            open_row = _fetch_open_player_session(connection, normalized_id)
            if open_row is None:
                return PlayerSessionWriteResult(written=False)
            session_id = int(open_row["session_id"])
            connection.execute(
                """
                UPDATE player_sessions
                SET close_observed_at = ?,
                    status = ?,
                    close_source = ?,
                    close_source_ref = ?,
                    close_confidence = ?,
                    end_reason = ?,
                    updated_at = ?
                WHERE session_id = ?
                """,
                (
                    close_timestamp,
                    PLAYER_SESSION_STATUS_CLOSED,
                    safe_source,
                    _nullable(safe_source_ref),
                    safe_confidence,
                    safe_end_reason,
                    close_timestamp,
                    session_id,
                ),
            )
            row = _fetch_player_session_by_id(connection, session_id)
            return PlayerSessionWriteResult(
                written=True,
                updated=True,
                closed=True,
                session=_player_session_record_from_row(row),
            )
    finally:
        connection.close()


def get_player_session(db_path: Path, session_id: int) -> PlayerSessionRecord | None:
    """Return one sanitized player session row by session ID."""
    connection = _connect_existing(db_path)
    if connection is None:
        return None
    try:
        row = _fetch_player_session_by_id(connection, session_id)
    finally:
        connection.close()
    return _player_session_record_from_row(row) if row is not None else None


def get_open_player_session(
    db_path: Path,
    reliable_id: object,
) -> PlayerSessionRecord | None:
    """Return one sanitized open player session for a reliable ID."""
    normalized_id = normalize_reliable_player_id(reliable_id)
    if not normalized_id:
        return None
    connection = _connect_existing(db_path)
    if connection is None:
        return None
    try:
        row = _fetch_open_player_session(connection, normalized_id)
    finally:
        connection.close()
    return _player_session_record_from_row(row) if row is not None else None


def ingest_player_log_events(
    db_path: Path,
    events: Iterable[PlayerLogEvent],
    *,
    ingested_at: str | None = None,
) -> PlayerLogEventIngestResult:
    """Persist sanitized parsed player log events into the registry database."""
    timestamp = ingested_at or _utc_now()
    rows = tuple(_player_log_event_row(event, created_at=timestamp) for event in events)

    ensure_player_registry_db(db_path)
    stored_count = 0
    duplicate_count = 0
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        for row in rows:
            if _player_log_event_exists(connection, row["event_key"]):
                duplicate_count += 1
                continue
            observed_at = str(row.get("observed_at") or row["created_at"])
            _record_player_log_event_observations(connection, row, observed_at=observed_at)
            cursor = _insert_player_log_event(connection, row)
            if cursor.rowcount == 1:
                stored_count += 1
            else:
                duplicate_count += 1

    return PlayerLogEventIngestResult(
        stored_count=stored_count,
        duplicate_count=duplicate_count,
    )


def _player_log_event_exists(connection: sqlite3.Connection, event_key: object) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM player_log_events
        WHERE event_key = ?
        """,
        (event_key,),
    ).fetchone()
    return row is not None


def _insert_player_log_event(
    connection: sqlite3.Connection,
    row: dict[str, object],
) -> sqlite3.Cursor:
    columns = ", ".join(_PLAYER_LOG_EVENT_INSERT_COLUMNS)
    placeholders = ", ".join("?" for _ in _PLAYER_LOG_EVENT_INSERT_COLUMNS)
    return connection.execute(
        f"""
        INSERT INTO player_log_events ({columns})
        VALUES ({placeholders})
        ON CONFLICT(event_key) DO NOTHING
        """,
        tuple(row[column] for column in _PLAYER_LOG_EVENT_INSERT_COLUMNS),
    )


def _record_player_log_event_observations(
    connection: sqlite3.Connection,
    row: dict[str, object],
    *,
    observed_at: str,
) -> None:
    seen_ids: set[str] = set()
    source = row.get("source") or "player_log_event"
    candidates = (
        (row.get("player_id"), row.get("player_name")),
        (row.get("victim_id"), row.get("victim_name")),
        (row.get("instigator_id"), row.get("instigator_name")),
    )
    for reliable_id, display_name in candidates:
        normalized_id = normalize_reliable_player_id(reliable_id)
        if not normalized_id or normalized_id in seen_ids:
            continue
        _record_reliable_player_observation(
            connection,
            reliable_id=normalized_id,
            display_name=display_name or "Unknown player",
            source=source,
            observed_at=observed_at,
        )
        seen_ids.add(normalized_id)


def _player_log_event_row(
    event: PlayerLogEvent,
    *,
    created_at: str,
) -> dict[str, object]:
    row: dict[str, object] = {
        "event_key": "",
        "event_type": _safe_event_text(event.event_type) or "unknown",
        "source": _safe_event_text(event.source) or "unknown",
        "source_ref": _safe_event_source_ref(event.raw_source_ref),
        "confidence": _safe_event_text(event.confidence) or "unknown",
        "observed_at": _safe_event_text(event.observed_at),
        "log_timestamp": _safe_event_text(event.raw_timestamp),
        "player_id": _safe_event_player_id(event.player_id),
        "player_name": _safe_event_text(event.player_name),
        "session_player_id": _safe_event_text(event.session_player_id),
        "rpl_identity": _safe_event_text(event.rpl_identity),
        "player_faction": _safe_event_text(event.player_faction),
        "faction_resource": _safe_event_text(event.faction_resource),
        "victim_id": _safe_event_player_id(event.victim_id),
        "victim_name": _safe_event_text(event.victim_name),
        "victim_session_player_id": _safe_event_text(event.victim_session_player_id),
        "victim_faction": _safe_event_text(event.victim_faction),
        "instigator_id": _safe_event_player_id(event.instigator_id),
        "instigator_name": _safe_event_text(event.instigator_name),
        "instigator_session_player_id": _safe_event_text(
            event.instigator_session_player_id,
        ),
        "instigator_faction": _safe_event_text(event.instigator_faction),
        "teamkill": _event_bool(event.teamkill),
        "suicide": _event_bool(event.suicide),
        "ai_instigator": _event_bool(event.ai_instigator),
        "damage_type": _safe_event_text(event.damage_type),
        "hit_zone": _safe_event_text(event.hit_zone),
        "distance_m": _event_distance(event.distance_m),
        "created_at": created_at,
    }
    row["event_key"] = _player_log_event_key(row)
    return row


def _player_log_event_key(row: dict[str, object]) -> str:
    payload = {column: row.get(column) for column in _PLAYER_LOG_EVENT_DEDUPE_COLUMNS}
    encoded = json.dumps(
        {"schema": PLAYER_LOG_EVENT_KEY_VERSION, "event": payload},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _safe_event_source_ref(value: object) -> str | None:
    raw_text = "" if value is None else str(value).replace("\\", "/")
    if "/" in raw_text:
        raw_text = raw_text.rsplit("/", 1)[-1]
    return _safe_event_text(
        raw_text,
        max_length=PLAYER_LOG_EVENT_REF_MAX_LENGTH,
    )


def _safe_event_player_id(value: object) -> str | None:
    return normalize_reliable_player_id(value) or None


def _safe_event_text(
    value: object,
    *,
    max_length: int = PLAYER_LOG_EVENT_TEXT_MAX_LENGTH,
) -> str | None:
    text = safe_player_text(value, max_length=max_length)
    text = _IPV4_ADDRESS_RE.sub("***", text)
    text = _BRACKETED_IPV6_ADDRESS_RE.sub("***", text).strip()
    return text or None


def _event_bool(value: bool | None) -> int | None:
    if value is None:
        return None
    return 1 if value else 0


def _event_distance(value: float | None) -> float | None:
    if value is None:
        return None
    try:
        distance = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(distance):
        return None
    return distance


def _event_bool_from_row(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    text = str(value).strip().casefold()
    if text in {"1", "true", "yes"}:
        return True
    if text in {"0", "false", "no"}:
        return False
    return None


def list_player_log_events(
    db_path: Path,
    *,
    limit: int = DEFAULT_PLAYER_HISTORY_EVENT_LIMIT,
    event_type: str = "",
    reliable_id: str = "",
    query: str = "",
) -> list[PlayerLogEventRecord]:
    """List sanitized stored player log events, newest first."""
    connection = _connect_existing(db_path)
    if connection is None:
        return []

    normalized_limit = _bounded_player_history_limit(limit)
    normalized_event_type = _safe_event_text(event_type, max_length=80) or ""
    raw_reliable_id = safe_player_text(reliable_id, max_length=120)
    normalized_reliable_id = normalize_reliable_player_id(raw_reliable_id)
    if raw_reliable_id and not normalized_reliable_id:
        connection.close()
        return []
    normalized_query = safe_player_text(query, max_length=120)

    where_clauses: list[str] = []
    params: list[object] = []
    if normalized_event_type:
        where_clauses.append("event_type = ?")
        params.append(normalized_event_type)
    if normalized_reliable_id:
        where_clauses.append("(player_id = ? OR victim_id = ? OR instigator_id = ?)")
        params.extend((normalized_reliable_id, normalized_reliable_id, normalized_reliable_id))
    if normalized_query:
        search_columns = (
            "event_type",
            "source",
            "source_ref",
            "confidence",
            "player_id",
            "player_name",
            "player_faction",
            "faction_resource",
            "victim_id",
            "victim_name",
            "victim_faction",
            "instigator_id",
            "instigator_name",
            "instigator_faction",
            "damage_type",
            "hit_zone",
        )
        where_clauses.append(
            "(" + " OR ".join(f"{column} LIKE ?" for column in search_columns) + ")"
        )
        params.extend([f"%{normalized_query}%"] * len(search_columns))

    sql = "SELECT * FROM player_log_events"
    if where_clauses:
        sql += " WHERE " + " AND ".join(where_clauses)
    sql += " ORDER BY COALESCE(observed_at, created_at) DESC, event_id DESC LIMIT ?"
    params.append(normalized_limit)

    try:
        with connection:
            rows = connection.execute(sql, tuple(params)).fetchall()
    finally:
        connection.close()
    return [_player_log_event_record_from_row(row) for row in rows]


def _empty_player_summary(player: KnownPlayer) -> PlayerSummary:
    return PlayerSummary(
        reliable_id=player.reliable_id,
        current_name=player.current_name,
        first_seen_at=player.first_seen_at,
        last_seen_at=player.last_seen_at,
        seen_count=player.seen_count,
        last_source=player.last_source,
        faction="",
        event_count=0,
        kill_count=0,
        death_count=0,
        teamkill_count=0,
        suicide_count=0,
    )


def _event_role_ids(row: sqlite3.Row, known_ids: set[str]) -> set[str]:
    return {
        str(row[column])
        for column in ("player_id", "victim_id", "instigator_id")
        if row[column] and str(row[column]) in known_ids
    }


def _event_role_factions(row: sqlite3.Row) -> tuple[tuple[str, str], ...]:
    return (
        (str(row["player_id"] or ""), _safe_event_text(row["player_faction"]) or ""),
        (str(row["victim_id"] or ""), _safe_event_text(row["victim_faction"]) or ""),
        (
            str(row["instigator_id"] or ""),
            _safe_event_text(row["instigator_faction"]) or "",
        ),
    )


def _increment_summary_counter(
    summaries: dict[str, dict[str, object]],
    reliable_id: str,
    key: str,
) -> None:
    summaries[reliable_id][key] = int(summaries[reliable_id][key]) + 1


def _player_summaries_for_known_players(
    db_path: Path,
    players: Iterable[KnownPlayer],
) -> list[PlayerSummary]:
    player_list = list(players)
    if not player_list:
        return []

    summaries: dict[str, dict[str, object]] = {
        player.reliable_id: {
            "faction": "",
            "event_count": 0,
            "kill_count": 0,
            "death_count": 0,
            "teamkill_count": 0,
            "suicide_count": 0,
        }
        for player in player_list
    }
    known_ids = set(summaries)
    placeholders = ",".join("?" for _ in known_ids)
    if not placeholders:
        return [_empty_player_summary(player) for player in player_list]

    connection = _connect_existing(db_path)
    if connection is None:
        return [_empty_player_summary(player) for player in player_list]
    try:
        rows = connection.execute(
            f"""
            SELECT
                event_type,
                COALESCE(observed_at, created_at) AS event_time,
                event_id,
                player_id,
                player_faction,
                victim_id,
                victim_faction,
                instigator_id,
                instigator_faction,
                teamkill,
                suicide
            FROM player_log_events
            WHERE player_id IN ({placeholders})
               OR victim_id IN ({placeholders})
               OR instigator_id IN ({placeholders})
            ORDER BY event_time DESC, event_id DESC
            """,
            tuple(known_ids) * 3,
        ).fetchall()
    finally:
        connection.close()

    death_events = {
        EVENT_TYPE_KILL,
        EVENT_TYPE_TEAMKILL,
        EVENT_TYPE_SUICIDE,
        EVENT_TYPE_OTHER_DEATH,
    }
    for row in rows:
        event_type = str(row["event_type"] or "")
        touched_ids = _event_role_ids(row, known_ids)
        for reliable_id in touched_ids:
            _increment_summary_counter(summaries, reliable_id, "event_count")

        instigator_id = str(row["instigator_id"] or "")
        victim_id = str(row["victim_id"] or "")
        is_teamkill = event_type == EVENT_TYPE_TEAMKILL or bool(
            _event_bool_from_row(row["teamkill"])
        )
        is_suicide = event_type == EVENT_TYPE_SUICIDE or bool(
            _event_bool_from_row(row["suicide"])
        )

        if event_type in {EVENT_TYPE_KILL, EVENT_TYPE_TEAMKILL} and instigator_id in summaries:
            _increment_summary_counter(summaries, instigator_id, "kill_count")
        if is_teamkill and instigator_id in summaries:
            _increment_summary_counter(summaries, instigator_id, "teamkill_count")
        if event_type in death_events and victim_id in summaries:
            _increment_summary_counter(summaries, victim_id, "death_count")
        if is_suicide and victim_id in summaries:
            _increment_summary_counter(summaries, victim_id, "suicide_count")

        for reliable_id, faction in _event_role_factions(row):
            if reliable_id in summaries and faction and not summaries[reliable_id]["faction"]:
                summaries[reliable_id]["faction"] = faction

    return [
        PlayerSummary(
            reliable_id=player.reliable_id,
            current_name=player.current_name,
            first_seen_at=player.first_seen_at,
            last_seen_at=player.last_seen_at,
            seen_count=player.seen_count,
            last_source=player.last_source,
            faction=str(summaries[player.reliable_id]["faction"]),
            event_count=int(summaries[player.reliable_id]["event_count"]),
            kill_count=int(summaries[player.reliable_id]["kill_count"]),
            death_count=int(summaries[player.reliable_id]["death_count"]),
            teamkill_count=int(summaries[player.reliable_id]["teamkill_count"]),
            suicide_count=int(summaries[player.reliable_id]["suicide_count"]),
        )
        for player in player_list
    ]


def list_player_summaries(
    db_path: Path,
    *,
    query: str = "",
    limit: int = DEFAULT_PLAYER_LIST_LIMIT,
) -> list[PlayerSummary]:
    """List known players with compact counters derived from stored events."""
    return _player_summaries_for_known_players(
        db_path,
        list_known_players(db_path, query=query, limit=limit),
    )


def list_player_summaries_by_ids(
    db_path: Path,
    reliable_ids: Iterable[str],
) -> dict[str, PlayerSummary]:
    """Return compact summaries for exact reliable player IDs."""
    normalized_ids = tuple(
        dict.fromkeys(
            normalized
            for reliable_id in reliable_ids
            if (normalized := normalize_reliable_player_id(reliable_id))
        )
    )
    if not normalized_ids:
        return {}

    connection = _connect_existing(db_path)
    if connection is None:
        return {}
    placeholders = ",".join("?" for _ in normalized_ids)
    try:
        rows = connection.execute(
            f"""
            SELECT *
            FROM players
            WHERE reliable_id IN ({placeholders})
            """,
            normalized_ids,
        ).fetchall()
    finally:
        connection.close()

    players = [_player_from_row(row) for row in rows]
    players_by_id = {player.reliable_id: player for player in players}
    ordered_players = [
        players_by_id[reliable_id]
        for reliable_id in normalized_ids
        if reliable_id in players_by_id
    ]
    return {
        summary.reliable_id: summary
        for summary in _player_summaries_for_known_players(db_path, ordered_players)
    }

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
