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
from urllib.parse import quote

from armactl import paths
from armactl.player_log_events import (
    CONFIDENCE_HIGH,
    CONFIDENCE_MEDIUM,
    EVENT_TIME_CONFIDENCE_AMBIGUOUS,
    EVENT_TIME_CONFIDENCE_EXACT,
    EVENT_TIME_SOURCE_CALLER_OBSERVED_AT,
    EVENT_TIME_SOURCE_CALLER_OCCURRED_AT,
    EVENT_TIME_SOURCE_LOG_PREFIX_WITHOUT_DATE,
    EVENT_TIME_SOURCE_UNAVAILABLE,
    EVENT_TYPE_COMBAT_HINT,
    EVENT_TYPE_KILL,
    EVENT_TYPE_OTHER_DEATH,
    EVENT_TYPE_PLAYER_DISCONNECTED,
    EVENT_TYPE_SERVER_LIFECYCLE,
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
PLAYER_REGISTRY_SCHEMA_VERSION = "10"
PRIVATE_PLAYER_REGISTRY_FILE_MODE = 0o600
DEFAULT_PLAYER_HISTORY_EVENT_LIMIT = 100
MAX_PLAYER_HISTORY_EVENT_LIMIT = 250
DEFAULT_PLAYER_LIST_LIMIT = 100
DEFAULT_PLAYER_SESSION_LIST_LIMIT = 100
MAX_PLAYER_SESSION_LIST_LIMIT = 250
DEFAULT_PLAYER_SESSION_MAINTENANCE_BATCH_LIMIT = 500
MAX_PLAYER_SESSION_MAINTENANCE_BATCH_LIMIT = 5000
DEFAULT_PLAYER_SESSION_ABSENCE_CONFIRMATION_SCANS = 2
MAX_PLAYER_SESSION_ABSENCE_CONFIRMATION_SCANS = 20
DEFAULT_PLAYER_SESSION_RECONNECT_GRACE_SECONDS = 10 * 60
_LEGACY_DEFAULT_TIMESTAMP = "1970-01-01T00:00:00+00:00"
PLAYER_LOG_EVENT_TEXT_MAX_LENGTH = 240
PLAYER_LOG_EVENT_REF_MAX_LENGTH = 240
PLAYER_LOG_EVENT_KEY_VERSION = "v1"
_IPV4_ADDRESS_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?\b")
_BRACKETED_IPV6_ADDRESS_RE = re.compile(r"\[[0-9A-Fa-f:.]{2,}\](?::\d{1,5})?")
PLAYER_LOG_INGEST_STATUS_UNAVAILABLE = "unavailable"
PLAYER_LOG_INGEST_STATUS_FRESH = "fresh"
PLAYER_LOG_INGEST_STATUS_NO_LOGS = "no_logs"
PLAYER_LOG_INGEST_STATUS_PARTIAL = "partial"
PLAYER_LOG_INGEST_STATUS_FAILED = "failed"
PLAYER_LOG_INGEST_STATUSES = (
    PLAYER_LOG_INGEST_STATUS_UNAVAILABLE,
    PLAYER_LOG_INGEST_STATUS_FRESH,
    PLAYER_LOG_INGEST_STATUS_NO_LOGS,
    PLAYER_LOG_INGEST_STATUS_PARTIAL,
    PLAYER_LOG_INGEST_STATUS_FAILED,
)
_PLAYER_LOG_EVENT_INSERT_COLUMNS = (
    "event_key",
    "event_type",
    "source",
    "source_ref",
    "confidence",
    "occurred_at",
    "observed_at",
    "log_timestamp",
    "time_source",
    "time_confidence",
    "player_id",
    "player_name",
    "session_player_id",
    "rpl_identity",
    "connection_id",
    "be_slot",
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
    "collected_at",
    "created_at",
)
_PLAYER_LOG_EVENT_DEDUPE_COLUMNS = tuple(
    column
    for column in _PLAYER_LOG_EVENT_INSERT_COLUMNS
    if column not in {
        "event_key",
        "collected_at",
        "created_at",
        "connection_id",
        "be_slot",
    }
)
_PLAYER_LOG_EVENT_LEGACY_V7_DEDUPE_COLUMNS = tuple(
    column
    for column in _PLAYER_LOG_EVENT_INSERT_COLUMNS
    if column not in {
        "event_key",
        "occurred_at",
        "time_source",
        "time_confidence",
        "collected_at",
        "created_at",
        "connection_id",
        "be_slot",
    }
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
PLAYER_SESSION_GAMEPLAY_SOURCES = (
    PLAYER_SESSION_SOURCE_SCRIPT_FACTION_JOIN,
    PLAYER_SESSION_SOURCE_SCRIPT_KILL,
)
PLAYER_SESSION_END_REASON_DISCONNECT = "disconnect"
PLAYER_SESSION_END_REASON_SERVER_BOUNDARY = "server_boundary"
PLAYER_SESSION_END_REASON_STALE_TIMEOUT = "stale_timeout"
PLAYER_SESSION_END_REASON_STALE_ABSENCE = "stale_absence"
PLAYER_SESSION_END_REASON_SCANNER_CHECKPOINT = "scanner_checkpoint"
PLAYER_SESSION_END_REASON_IMPORT_WINDOW = "import_window"
PLAYER_SESSION_END_REASON_UNKNOWN = "unknown"
PLAYER_SESSION_END_REASONS = (
    PLAYER_SESSION_END_REASON_DISCONNECT,
    PLAYER_SESSION_END_REASON_SERVER_BOUNDARY,
    PLAYER_SESSION_END_REASON_STALE_TIMEOUT,
    PLAYER_SESSION_END_REASON_STALE_ABSENCE,
    PLAYER_SESSION_END_REASON_SCANNER_CHECKPOINT,
    PLAYER_SESSION_END_REASON_IMPORT_WINDOW,
    PLAYER_SESSION_END_REASON_UNKNOWN,
)
PLAYER_SESSION_RECONNECT_COMPATIBLE_END_REASONS = (
    PLAYER_SESSION_END_REASON_DISCONNECT,
    PLAYER_SESSION_END_REASON_STALE_ABSENCE,
    PLAYER_SESSION_END_REASON_STALE_TIMEOUT,
)
PLAYER_SESSION_V10_COLUMNS = (
    ("play_session_id",
        "play_session_id INTEGER NOT NULL DEFAULT 0 CHECK(play_session_id >= 0)",
    ),
    ("server_run_key", "server_run_key TEXT NOT NULL DEFAULT ''"),
    (
        "reconnect_merge_count",
        "reconnect_merge_count INTEGER NOT NULL DEFAULT 0 CHECK(reconnect_merge_count >= 0)",
    ),
    ("last_reconnect_at", "last_reconnect_at TEXT"),
    ("last_reconnect_close_observed_at", "last_reconnect_close_observed_at TEXT"),
    ("last_reconnect_close_reason", "last_reconnect_close_reason TEXT"),
    ("last_gameplay_evidence_at", "last_gameplay_evidence_at TEXT"),
    ("last_gameplay_source", "last_gameplay_source TEXT"),
    ("last_gameplay_source_ref", "last_gameplay_source_ref TEXT"),
    ("last_gameplay_confidence", "last_gameplay_confidence TEXT"),
)
PLAYER_SESSION_INFERRED_OR_STALE_END_REASONS = tuple(
    reason for reason in PLAYER_SESSION_END_REASONS
    if reason != PLAYER_SESSION_END_REASON_DISCONNECT
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
    occurred_at: str
    observed_at: str
    log_timestamp: str
    time_source: str
    time_confidence: str
    player_id: str
    player_name: str
    session_player_id: str
    rpl_identity: str
    connection_id: str
    be_slot: str
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
    collected_at: str
    created_at: str

    @property
    def event_time(self) -> str:
        return self.occurred_at or self.observed_at or self.collected_at or self.created_at

    @property
    def event_time_label(self) -> str:
        if self.occurred_at:
            return "Event time"
        if self.observed_at:
            return "Observed"
        if self.collected_at:
            return "Collected"
        return "Recorded"

    @property
    def event_time_detail(self) -> str:
        if self.occurred_at:
            return self.time_source or "event occurrence"
        if self.observed_at:
            return self.time_source or "source observation"
        return self.time_source or "storage record"


@dataclass(frozen=True)
class PlayerSessionRecord:
    """One sanitized persisted player session row."""

    session_id: int
    play_session_id: int
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
    server_run_key: str
    reconnect_merge_count: int
    last_reconnect_at: str
    last_reconnect_close_observed_at: str
    last_reconnect_close_reason: str
    last_gameplay_evidence_at: str
    last_gameplay_source: str
    last_gameplay_source_ref: str
    last_gameplay_confidence: str
    scanner_checkpoint_source: str
    scanner_checkpoint_ref: str
    scanner_checkpoint_at: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class PlayerSessionSummary:
    """Counts-only read model for stored player sessions."""

    open_sessions: int = 0
    closed_sessions: int = 0
    inferred_or_stale_closes: int = 0
    latest_observed_at: str = ""


@dataclass(frozen=True)
class PlayerSessionWriteResult:
    """Summary of one controlled player session write attempt."""

    written: bool
    created: bool = False
    updated: bool = False
    closed: bool = False
    reconnected: bool = False
    ignored_count: int = 0
    session: PlayerSessionRecord | None = None


@dataclass(frozen=True)
class PlayerSessionStaleCloseResult:
    """Counts-only summary for explicit stale open-session closing."""

    open_sessions_scanned: int = 0
    sessions_overdue: int = 0
    sessions_closed: int = 0
    sessions_skipped: int = 0


@dataclass(frozen=True)
class PlayerSessionRetentionCleanupResult:
    """Counts-only summary for explicit player-session retention cleanup."""

    sessions_scanned: int = 0
    sessions_deleted: int = 0
    sessions_skipped: int = 0


@dataclass(frozen=True)
class PlayerLogIngestCheckpoint:
    """Safe checkpoint metadata for one allowlisted player-log source."""

    scope: str
    source_key: str
    source_label: str
    size_bytes: int
    mtime_ns: int
    fingerprint: str
    status: str
    last_scanned_at: str
    updated_at: str


@dataclass(frozen=True)
class PlayerLogIngestFreshness:
    """Counts-only freshness status for player-log ingest."""

    scope: str
    status: str = PLAYER_LOG_INGEST_STATUS_UNAVAILABLE
    last_run_at: str = ""
    last_success_at: str = ""
    updated_at: str = ""
    scanned_files: int = 0
    parsed_events: int = 0
    stored_events: int = 0
    skipped_files: int = 0
    skipped_reasons: str = ""
    checkpoint_updated: bool = False


@dataclass(frozen=True)
class PlayerSessionAbsenceWindowResult:
    """Counts-only result for one live absence-window update."""

    advanced: bool = False
    confirmation_reached: bool = False
    ignored_count: int = 0
    absent_scan_count: int = 0


def _bounded_player_history_limit(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return DEFAULT_PLAYER_HISTORY_EVENT_LIMIT
    return max(1, min(parsed, MAX_PLAYER_HISTORY_EVENT_LIMIT))


def _bounded_player_session_maintenance_limit(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return DEFAULT_PLAYER_SESSION_MAINTENANCE_BATCH_LIMIT
    return max(1, min(parsed, MAX_PLAYER_SESSION_MAINTENANCE_BATCH_LIMIT))


def _bounded_player_session_list_limit(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return DEFAULT_PLAYER_SESSION_LIST_LIMIT
    return max(1, min(parsed, MAX_PLAYER_SESSION_LIST_LIMIT))


def _bounded_player_session_absence_confirmation_scans(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return DEFAULT_PLAYER_SESSION_ABSENCE_CONFIRMATION_SCANS
    return max(
        2,
        min(parsed, MAX_PLAYER_SESSION_ABSENCE_CONFIRMATION_SCANS),
    )


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
            occurred_at TEXT,
            observed_at TEXT,
            log_timestamp TEXT,
            time_source TEXT NOT NULL DEFAULT 'unavailable',
            time_confidence TEXT NOT NULL DEFAULT 'ambiguous',
            player_id TEXT,
            player_name TEXT,
            session_player_id TEXT,
            rpl_identity TEXT,
            connection_id TEXT,
            be_slot TEXT,
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
            collected_at TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    _ensure_columns(
        connection,
        "player_log_events",
        (
            ("connection_id", "connection_id TEXT"),
            ("be_slot", "be_slot TEXT"),
            ("occurred_at", "occurred_at TEXT"),
            ("time_source", "time_source TEXT NOT NULL DEFAULT 'unavailable'"),
            (
                "time_confidence",
                "time_confidence TEXT NOT NULL DEFAULT 'ambiguous'",
            ),
            ("collected_at", "collected_at TEXT"),
        ),
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_player_log_events_occurred_at
        ON player_log_events(occurred_at)
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
        CREATE INDEX IF NOT EXISTS idx_player_log_events_collected_at
        ON player_log_events(collected_at)
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
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_player_log_events_history_order
        ON player_log_events(
            COALESCE(occurred_at, observed_at, collected_at, created_at) DESC,
            event_id DESC
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_player_log_events_type_history_order
        ON player_log_events(
            event_type,
            COALESCE(occurred_at, observed_at, collected_at, created_at) DESC,
            event_id DESC
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_player_log_events_player_history_order
        ON player_log_events(
            player_id,
            COALESCE(occurred_at, observed_at, collected_at, created_at) DESC,
            event_id DESC
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_player_log_events_victim_history_order
        ON player_log_events(
            victim_id,
            COALESCE(occurred_at, observed_at, collected_at, created_at) DESC,
            event_id DESC
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_player_log_events_instigator_history_order
        ON player_log_events(
            instigator_id,
            COALESCE(occurred_at, observed_at, collected_at, created_at) DESC,
            event_id DESC
        )
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
            play_session_id INTEGER NOT NULL DEFAULT 0 CHECK(play_session_id >= 0),
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
            server_run_key TEXT NOT NULL DEFAULT '',
            reconnect_merge_count INTEGER NOT NULL DEFAULT 0 CHECK(reconnect_merge_count >= 0),
            last_reconnect_at TEXT,
            last_reconnect_close_observed_at TEXT,
            last_reconnect_close_reason TEXT,
            last_gameplay_evidence_at TEXT,
            last_gameplay_source TEXT,
            last_gameplay_source_ref TEXT,
            last_gameplay_confidence TEXT,
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
    _ensure_columns(connection, "player_sessions", PLAYER_SESSION_V10_COLUMNS)
    connection.execute(
        """
        UPDATE player_sessions
        SET play_session_id = session_id
        WHERE play_session_id = 0
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
        CREATE INDEX IF NOT EXISTS idx_player_sessions_play_session_id
        ON player_sessions(play_session_id)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_player_sessions_server_run_key
        ON player_sessions(server_run_key)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_player_sessions_last_gameplay_evidence_at
        ON player_sessions(last_gameplay_evidence_at)
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


def _ensure_player_session_live_scan_windows_schema(
    connection: sqlite3.Connection,
) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS player_session_live_scan_windows (
            window_id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            reliable_id TEXT NOT NULL,
            window_source TEXT NOT NULL,
            window_ref TEXT NOT NULL DEFAULT '',
            first_absent_at TEXT NOT NULL,
            last_absent_at TEXT NOT NULL,
            last_scan_at TEXT NOT NULL,
            absent_scan_count INTEGER NOT NULL CHECK(absent_scan_count >= 1),
            confirmed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(session_id, window_source, window_ref),
            CHECK(length(reliable_id) > 0),
            CHECK(length(window_source) > 0),
            CHECK(length(first_absent_at) > 0),
            CHECK(length(last_absent_at) > 0),
            CHECK(length(last_scan_at) > 0),
            FOREIGN KEY (session_id) REFERENCES player_sessions(session_id)
                ON DELETE CASCADE
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_player_session_live_scan_windows_reliable_id
        ON player_session_live_scan_windows(reliable_id)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_player_session_live_scan_windows_source
        ON player_session_live_scan_windows(window_source, window_ref)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_player_session_live_scan_windows_confirmed
        ON player_session_live_scan_windows(confirmed_at)
        """
    )


def _ensure_player_session_lifecycle_boundaries_schema(
    connection: sqlite3.Connection,
) -> None:
    confidence_values = _sql_text_values(PLAYER_SESSION_CONFIDENCES)
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS player_session_lifecycle_boundaries (
            boundary_id INTEGER PRIMARY KEY AUTOINCREMENT,
            boundary_at TEXT NOT NULL,
            source TEXT NOT NULL,
            source_ref TEXT NOT NULL DEFAULT '',
            confidence TEXT NOT NULL CHECK(confidence IN ({confidence_values})),
            created_at TEXT NOT NULL,
            UNIQUE(boundary_at, source, source_ref),
            CHECK(length(boundary_at) > 0),
            CHECK(length(source) > 0)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_player_session_lifecycle_boundaries_at
        ON player_session_lifecycle_boundaries(boundary_at)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_player_session_lifecycle_boundaries_source
        ON player_session_lifecycle_boundaries(source, source_ref)
        """
    )

def _ensure_player_log_ingest_metadata_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS player_log_ingest_checkpoints (
            scope TEXT NOT NULL CHECK(length(trim(scope)) > 0),
            source_key TEXT NOT NULL CHECK(length(trim(source_key)) > 0),
            source_label TEXT NOT NULL DEFAULT '',
            size_bytes INTEGER NOT NULL DEFAULT 0 CHECK(size_bytes >= 0),
            mtime_ns INTEGER NOT NULL DEFAULT 0 CHECK(mtime_ns >= 0),
            fingerprint TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'unknown',
            last_scanned_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL CHECK(length(updated_at) > 0),
            PRIMARY KEY(scope, source_key)
        )
        """
    )
    _ensure_columns(
        connection,
        "player_log_ingest_checkpoints",
        (
            ("scope", "scope TEXT NOT NULL DEFAULT 'player_logs'"),
            ("source_key", "source_key TEXT NOT NULL DEFAULT 'legacy'"),
            ("source_label", "source_label TEXT NOT NULL DEFAULT ''"),
            ("size_bytes", "size_bytes INTEGER NOT NULL DEFAULT 0 CHECK(size_bytes >= 0)"),
            ("mtime_ns", "mtime_ns INTEGER NOT NULL DEFAULT 0 CHECK(mtime_ns >= 0)"),
            ("fingerprint", "fingerprint TEXT NOT NULL DEFAULT ''"),
            ("status", "status TEXT NOT NULL DEFAULT 'unknown'"),
            ("last_scanned_at", "last_scanned_at TEXT NOT NULL DEFAULT ''"),
            (
                "updated_at",
                f"updated_at TEXT NOT NULL DEFAULT '{_LEGACY_DEFAULT_TIMESTAMP}'",
            ),
        ),
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_player_log_ingest_checkpoints_scope
        ON player_log_ingest_checkpoints(scope, status)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS player_log_ingest_freshness (
            scope TEXT PRIMARY KEY CHECK(length(trim(scope)) > 0),
            status TEXT NOT NULL DEFAULT 'unavailable',
            last_run_at TEXT NOT NULL DEFAULT '',
            last_success_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL CHECK(length(updated_at) > 0),
            scanned_files INTEGER NOT NULL DEFAULT 0 CHECK(scanned_files >= 0),
            parsed_events INTEGER NOT NULL DEFAULT 0 CHECK(parsed_events >= 0),
            stored_events INTEGER NOT NULL DEFAULT 0 CHECK(stored_events >= 0),
            skipped_files INTEGER NOT NULL DEFAULT 0 CHECK(skipped_files >= 0),
            skipped_reasons TEXT NOT NULL DEFAULT '',
            checkpoint_updated INTEGER NOT NULL DEFAULT 0
                CHECK(checkpoint_updated IN (0, 1))
        )
        """
    )
    _ensure_columns(
        connection,
        "player_log_ingest_freshness",
        (
            ("scope", "scope TEXT NOT NULL DEFAULT 'player_logs'"),
            ("status", "status TEXT NOT NULL DEFAULT 'unavailable'"),
            ("last_run_at", "last_run_at TEXT NOT NULL DEFAULT ''"),
            ("last_success_at", "last_success_at TEXT NOT NULL DEFAULT ''"),
            (
                "updated_at",
                f"updated_at TEXT NOT NULL DEFAULT '{_LEGACY_DEFAULT_TIMESTAMP}'",
            ),
            ("scanned_files", "scanned_files INTEGER NOT NULL DEFAULT 0 CHECK(scanned_files >= 0)"),
            ("parsed_events", "parsed_events INTEGER NOT NULL DEFAULT 0 CHECK(parsed_events >= 0)"),
            ("stored_events", "stored_events INTEGER NOT NULL DEFAULT 0 CHECK(stored_events >= 0)"),
            ("skipped_files", "skipped_files INTEGER NOT NULL DEFAULT 0 CHECK(skipped_files >= 0)"),
            ("skipped_reasons", "skipped_reasons TEXT NOT NULL DEFAULT ''"),
            (
                "checkpoint_updated",
                "checkpoint_updated INTEGER NOT NULL DEFAULT 0 CHECK(checkpoint_updated IN (0, 1))",
            ),
        ),
    )


def _drop_player_log_event_history_indexes(connection: sqlite3.Connection) -> None:
    for index_name in (
        "idx_player_log_events_history_order",
        "idx_player_log_events_type_history_order",
        "idx_player_log_events_player_history_order",
        "idx_player_log_events_victim_history_order",
        "idx_player_log_events_instigator_history_order",
    ):
        connection.execute(f"DROP INDEX IF EXISTS {_quote_identifier(index_name)}")


def _drop_player_session_indexes(connection: sqlite3.Connection) -> None:
    for index_name in (
        "idx_player_sessions_reliable_id",
        "idx_player_sessions_play_session_id",
        "idx_player_sessions_server_run_key",
        "idx_player_sessions_last_gameplay_evidence_at",
        "idx_player_sessions_status",
        "idx_player_sessions_open_observed_at",
        "idx_player_sessions_last_seen_at",
        "idx_player_sessions_close_observed_at",
        "idx_player_sessions_open_source",
        "idx_player_sessions_last_seen_source",
        "idx_player_sessions_close_source",
        "idx_player_sessions_scanner_checkpoint",
        "idx_player_sessions_one_open_per_reliable_id",
    ):
        connection.execute(f"DROP INDEX IF EXISTS {_quote_identifier(index_name)}")


def _player_sessions_schema_allows_end_reason(
    connection: sqlite3.Connection,
    end_reason: str,
) -> bool:
    row = connection.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'player_sessions'
        """
    ).fetchone()
    return row is not None and end_reason in str(row[0])


def _rebuild_player_sessions_schema_for_end_reason(
    connection: sqlite3.Connection,
    *,
    end_reason: str,
    legacy_table: str,
) -> None:
    if not _table_exists(connection, "player_sessions"):
        _ensure_player_sessions_schema(connection)
        return
    if _player_sessions_schema_allows_end_reason(connection, end_reason):
        _ensure_player_sessions_schema(connection)
        return
    if _table_exists(connection, legacy_table):
        raise RuntimeError("player_sessions migration workspace already exists.")

    connection.execute(
        f"ALTER TABLE player_sessions RENAME TO {_quote_identifier(legacy_table)}"
    )
    _drop_player_session_indexes(connection)
    _ensure_player_sessions_schema(connection)
    legacy_columns = _table_columns(connection, legacy_table)
    copy_columns = tuple(
        column
        for column in PlayerSessionRecord.__dataclass_fields__
        if column in legacy_columns
    )
    columns_sql = ", ".join(_quote_identifier(column) for column in copy_columns)
    connection.execute(
        f"INSERT INTO player_sessions ({columns_sql}) "
        f"SELECT {columns_sql} FROM {_quote_identifier(legacy_table)}"
    )
    connection.execute(
        """
        UPDATE player_sessions
        SET play_session_id = session_id
        WHERE play_session_id = 0
        """
    )
    connection.execute(f"DROP TABLE {_quote_identifier(legacy_table)}")


def _rebuild_player_sessions_schema_for_v6(connection: sqlite3.Connection) -> None:
    _rebuild_player_sessions_schema_for_end_reason(
        connection,
        end_reason=PLAYER_SESSION_END_REASON_STALE_TIMEOUT,
        legacy_table="player_sessions_migration_v6",
    )


def _rebuild_player_sessions_schema_for_v7(connection: sqlite3.Connection) -> None:
    _rebuild_player_sessions_schema_for_end_reason(
        connection,
        end_reason=PLAYER_SESSION_END_REASON_STALE_ABSENCE,
        legacy_table="player_sessions_migration_v7",
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
        current_version = 3
    if current_version < 4:
        _ensure_player_log_events_schema(connection)
        _write_player_registry_schema_version(connection, 4)
        current_version = 4
    if current_version < 5:
        _ensure_player_log_events_schema(connection)
        _write_player_registry_schema_version(connection, 5)
        current_version = 5
    if current_version < 6:
        _rebuild_player_sessions_schema_for_v6(connection)
        _write_player_registry_schema_version(connection, 6)
        current_version = 6
    if current_version < 7:
        _rebuild_player_sessions_schema_for_v7(connection)
        _ensure_player_session_live_scan_windows_schema(connection)
        _write_player_registry_schema_version(connection, 7)
        current_version = 7
    if current_version < 8:
        _drop_player_log_event_history_indexes(connection)
        _ensure_player_log_events_schema(connection)
        _write_player_registry_schema_version(connection, 8)
        current_version = 8
    if current_version < 9:
        _ensure_player_log_ingest_metadata_schema(connection)
        _write_player_registry_schema_version(connection, 9)
        current_version = 9
    if current_version < 10:
        _ensure_player_sessions_schema(connection)
        _ensure_player_session_lifecycle_boundaries_schema(connection)
        _write_player_registry_schema_version(connection, 10)


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


def _connect_existing_readonly(db_path: Path) -> sqlite3.Connection | None:
    if not db_path.is_file():
        return None
    quoted_path = quote(str(db_path), safe="/:")
    connection = sqlite3.connect(f"file:{quoted_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection



def _safe_ingest_text(value: object, *, max_length: int = 120) -> str:
    text = safe_player_text(value, max_length=max_length)
    text = text.replace("/", "_").replace("\\", "_")
    text = _IPV4_ADDRESS_RE.sub("***", text)
    text = _BRACKETED_IPV6_ADDRESS_RE.sub("***", text).strip()
    return text


def _safe_ingest_status(value: object) -> str:
    status = _safe_ingest_text(value, max_length=40).strip().lower()
    if status in PLAYER_LOG_INGEST_STATUSES:
        return status
    return PLAYER_LOG_INGEST_STATUS_UNAVAILABLE


def _safe_ingest_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _player_log_ingest_checkpoint_from_row(
    row: sqlite3.Row,
) -> PlayerLogIngestCheckpoint:
    return PlayerLogIngestCheckpoint(
        scope=_safe_ingest_text(row["scope"]),
        source_key=_safe_ingest_text(row["source_key"], max_length=120),
        source_label=_safe_ingest_text(row["source_label"], max_length=120),
        size_bytes=_safe_ingest_int(row["size_bytes"]),
        mtime_ns=_safe_ingest_int(row["mtime_ns"]),
        fingerprint=_safe_ingest_text(row["fingerprint"], max_length=120),
        status=_safe_ingest_text(row["status"], max_length=40),
        last_scanned_at=_safe_ingest_text(row["last_scanned_at"], max_length=80),
        updated_at=_safe_ingest_text(row["updated_at"], max_length=80),
    )


def _default_player_log_ingest_freshness(scope: str) -> PlayerLogIngestFreshness:
    return PlayerLogIngestFreshness(scope=_safe_ingest_text(scope) or "player_logs")


def _player_log_ingest_freshness_from_row(row: sqlite3.Row) -> PlayerLogIngestFreshness:
    return PlayerLogIngestFreshness(
        scope=_safe_ingest_text(row["scope"]),
        status=_safe_ingest_status(row["status"]),
        last_run_at=_safe_ingest_text(row["last_run_at"], max_length=80),
        last_success_at=_safe_ingest_text(row["last_success_at"], max_length=80),
        updated_at=_safe_ingest_text(row["updated_at"], max_length=80),
        scanned_files=_safe_ingest_int(row["scanned_files"]),
        parsed_events=_safe_ingest_int(row["parsed_events"]),
        stored_events=_safe_ingest_int(row["stored_events"]),
        skipped_files=_safe_ingest_int(row["skipped_files"]),
        skipped_reasons=_safe_ingest_text(row["skipped_reasons"], max_length=240),
        checkpoint_updated=bool(row["checkpoint_updated"]),
    )


def list_player_log_ingest_checkpoints(
    db_path: Path,
    *,
    scope: str,
) -> list[PlayerLogIngestCheckpoint]:
    safe_scope = _safe_ingest_text(scope) or "player_logs"
    connection = _connect_existing_readonly(db_path)
    if connection is None:
        return []
    try:
        if not _table_exists(connection, "player_log_ingest_checkpoints"):
            return []
        rows = connection.execute(
            "SELECT * FROM player_log_ingest_checkpoints "
            "WHERE scope = ? ORDER BY updated_at DESC, source_key ASC",
            (safe_scope,),
        ).fetchall()
        return [_player_log_ingest_checkpoint_from_row(row) for row in rows]
    except sqlite3.Error:
        return []
    finally:
        connection.close()


def upsert_player_log_ingest_checkpoints(
    db_path: Path,
    checkpoints: Iterable[PlayerLogIngestCheckpoint],
) -> int:
    records = tuple(checkpoints)
    if not records:
        return 0
    ensure_player_registry_db(db_path)
    written = 0
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        for checkpoint in records:
            safe_scope = _safe_ingest_text(checkpoint.scope) or "player_logs"
            safe_source_key = _safe_ingest_text(
                checkpoint.source_key,
                max_length=120,
            )
            if not safe_source_key:
                continue
            connection.execute(
                "INSERT INTO player_log_ingest_checkpoints("
                "scope, source_key, source_label, size_bytes, mtime_ns, "
                "fingerprint, status, last_scanned_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(scope, source_key) DO UPDATE SET "
                "source_label = excluded.source_label, "
                "size_bytes = excluded.size_bytes, "
                "mtime_ns = excluded.mtime_ns, "
                "fingerprint = excluded.fingerprint, "
                "status = excluded.status, "
                "last_scanned_at = excluded.last_scanned_at, "
                "updated_at = excluded.updated_at",
                (
                    safe_scope,
                    safe_source_key,
                    _safe_ingest_text(checkpoint.source_label),
                    _safe_ingest_int(checkpoint.size_bytes),
                    _safe_ingest_int(checkpoint.mtime_ns),
                    _safe_ingest_text(checkpoint.fingerprint, max_length=120),
                    _safe_ingest_text(checkpoint.status, max_length=40) or "scanned",
                    _safe_ingest_text(checkpoint.last_scanned_at, max_length=80),
                    _safe_ingest_text(checkpoint.updated_at, max_length=80)
                    or _utc_now(),
                ),
            )
            written += 1
    return written


def record_player_log_ingest_freshness(
    db_path: Path,
    *,
    scope: str,
    status: str,
    last_run_at: str,
    scanned_files: int,
    parsed_events: int,
    stored_events: int,
    skipped_files: int,
    skipped_reasons: str = "",
    checkpoint_updated: bool = False,
) -> PlayerLogIngestFreshness:
    safe_scope = _safe_ingest_text(scope) or "player_logs"
    safe_status = _safe_ingest_status(status)
    safe_last_run_at = _safe_ingest_text(last_run_at, max_length=80) or _utc_now()
    safe_skipped_reasons = _safe_ingest_text(skipped_reasons, max_length=240)
    last_success_at = ""
    if safe_status != PLAYER_LOG_INGEST_STATUS_FAILED:
        last_success_at = safe_last_run_at
    previous = get_player_log_ingest_freshness(db_path, scope=safe_scope)
    if not last_success_at:
        last_success_at = previous.last_success_at
    ensure_player_registry_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            "INSERT INTO player_log_ingest_freshness("
            "scope, status, last_run_at, last_success_at, updated_at, "
            "scanned_files, parsed_events, stored_events, skipped_files, "
            "skipped_reasons, checkpoint_updated"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(scope) DO UPDATE SET "
            "status = excluded.status, "
            "last_run_at = excluded.last_run_at, "
            "last_success_at = excluded.last_success_at, "
            "updated_at = excluded.updated_at, "
            "scanned_files = excluded.scanned_files, "
            "parsed_events = excluded.parsed_events, "
            "stored_events = excluded.stored_events, "
            "skipped_files = excluded.skipped_files, "
            "skipped_reasons = excluded.skipped_reasons, "
            "checkpoint_updated = excluded.checkpoint_updated",
            (
                safe_scope,
                safe_status,
                safe_last_run_at,
                last_success_at,
                safe_last_run_at,
                _safe_ingest_int(scanned_files),
                _safe_ingest_int(parsed_events),
                _safe_ingest_int(stored_events),
                _safe_ingest_int(skipped_files),
                safe_skipped_reasons,
                1 if checkpoint_updated else 0,
            ),
        )
    return get_player_log_ingest_freshness(db_path, scope=safe_scope)


def get_player_log_ingest_freshness(
    db_path: Path,
    *,
    scope: str,
) -> PlayerLogIngestFreshness:
    safe_scope = _safe_ingest_text(scope) or "player_logs"
    connection = _connect_existing_readonly(db_path)
    if connection is None:
        return _default_player_log_ingest_freshness(safe_scope)
    try:
        if not _table_exists(connection, "player_log_ingest_freshness"):
            return _default_player_log_ingest_freshness(safe_scope)
        row = connection.execute(
            "SELECT * FROM player_log_ingest_freshness WHERE scope = ?",
            (safe_scope,),
        ).fetchone()
        if row is None:
            return _default_player_log_ingest_freshness(safe_scope)
        return _player_log_ingest_freshness_from_row(row)
    except sqlite3.Error:
        return _default_player_log_ingest_freshness(safe_scope)
    finally:
        connection.close()


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
        occurred_at=_safe_event_text(row["occurred_at"]) or "",
        observed_at=_safe_event_text(row["observed_at"]) or "",
        log_timestamp=_safe_event_text(row["log_timestamp"]) or "",
        time_source=_safe_event_text(row["time_source"]) or "",
        time_confidence=_safe_event_text(row["time_confidence"]) or "",
        player_id=_safe_event_player_id(row["player_id"]) or "",
        player_name=_safe_event_text(row["player_name"]) or "",
        session_player_id=_safe_event_text(row["session_player_id"]) or "",
        rpl_identity=_safe_event_text(row["rpl_identity"]) or "",
        connection_id=_safe_event_correlation(row["connection_id"]) or "",
        be_slot=_safe_event_correlation(row["be_slot"]) or "",
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
        collected_at=_safe_event_text(row["collected_at"]) or "",
        created_at=_safe_event_text(row["created_at"]) or "",
    )


def _row_value(
    row: sqlite3.Row,
    column: str,
    default: object = None,
) -> object:
    try:
        return row[column]
    except (IndexError, KeyError):
        return default


def _player_session_record_from_row(row: sqlite3.Row) -> PlayerSessionRecord:
    return PlayerSessionRecord(
        session_id=int(row["session_id"]),
        play_session_id=int(_row_value(row, "play_session_id", row["session_id"]) or 0),
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
        server_run_key=_safe_session_text(
            _row_value(row, "server_run_key"),
            max_length=120,
        ),
        reconnect_merge_count=int(_row_value(row, "reconnect_merge_count", 0) or 0),
        last_reconnect_at=_safe_session_text(
            _row_value(row, "last_reconnect_at"),
            max_length=80,
        ),
        last_reconnect_close_observed_at=_safe_session_text(
            _row_value(row, "last_reconnect_close_observed_at"),
            max_length=80,
        ),
        last_reconnect_close_reason=_safe_session_text(
            _row_value(row, "last_reconnect_close_reason"),
            max_length=80,
        ),
        last_gameplay_evidence_at=_safe_session_text(
            _row_value(row, "last_gameplay_evidence_at"),
            max_length=80,
        ),
        last_gameplay_source=_safe_session_source(
            _row_value(row, "last_gameplay_source"),
            default="",
        ),
        last_gameplay_source_ref=_safe_session_source_ref(
            _row_value(row, "last_gameplay_source_ref"),
        ),
        last_gameplay_confidence=_safe_session_text(
            _row_value(row, "last_gameplay_confidence"),
            max_length=40,
        ),
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


def _parse_session_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_session_timestamp_before(value: str, cutoff: str) -> bool:
    value_dt = _parse_session_timestamp(value)
    cutoff_dt = _parse_session_timestamp(cutoff)
    if value_dt is not None and cutoff_dt is not None:
        return value_dt < cutoff_dt
    if value and cutoff:
        return value < cutoff
    return False


def _session_has_reliable_last_seen(session: PlayerSessionRecord) -> bool:
    return session.last_seen_confidence in {
        PLAYER_SESSION_CONFIDENCE_HIGH,
        PLAYER_SESSION_CONFIDENCE_MEDIUM,
    }


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


def _safe_reconnect_grace_seconds(value: object) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        seconds = DEFAULT_PLAYER_SESSION_RECONNECT_GRACE_SECONDS
    return max(0, seconds)


def _is_gameplay_session_source(source: str) -> bool:
    return source in PLAYER_SESSION_GAMEPLAY_SOURCES


def _session_gap_seconds(left: str, right: str) -> int | None:
    left_dt = _parse_session_timestamp(left)
    right_dt = _parse_session_timestamp(right)
    if left_dt is None or right_dt is None:
        return None
    return math.floor((right_dt - left_dt).total_seconds())


def _lifecycle_boundary_key(row: sqlite3.Row | None) -> str:
    if row is None:
        return ""
    try:
        boundary_id = int(row["boundary_id"])
    except (TypeError, ValueError):
        return ""
    return f"boundary:{boundary_id}" if boundary_id > 0 else ""


def _latest_lifecycle_boundary_row(
    connection: sqlite3.Connection,
    *,
    at: str,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT *
        FROM player_session_lifecycle_boundaries
        WHERE boundary_at <= ?
        ORDER BY boundary_at DESC, boundary_id DESC
        LIMIT 1
        """,
        (at,),
    ).fetchone()


def _latest_session_lifecycle_boundary_key(
    connection: sqlite3.Connection,
    *,
    at: str,
) -> str:
    return _lifecycle_boundary_key(_latest_lifecycle_boundary_row(connection, at=at))


def _first_lifecycle_boundary_between(
    connection: sqlite3.Connection,
    *,
    after: str,
    at: str,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT *
        FROM player_session_lifecycle_boundaries
        WHERE boundary_at > ?
          AND boundary_at <= ?
        ORDER BY boundary_at ASC, boundary_id ASC
        LIMIT 1
        """,
        (after, at),
    ).fetchone()


def _has_lifecycle_boundary_between(
    connection: sqlite3.Connection,
    *,
    after: str,
    at: str,
) -> bool:
    return _first_lifecycle_boundary_between(connection, after=after, at=at) is not None


def _record_player_session_lifecycle_boundary_connection(
    connection: sqlite3.Connection,
    *,
    boundary_at: str,
    source: str,
    source_ref: str,
    confidence: str,
) -> bool:
    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO player_session_lifecycle_boundaries(
            boundary_at,
            source,
            source_ref,
            confidence,
            created_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (boundary_at, source, source_ref, confidence, boundary_at),
    )
    return bool(cursor.rowcount)


def record_player_session_lifecycle_boundary(
    db_path: Path,
    *,
    boundary_at: str | None = None,
    source: object = PLAYER_SESSION_SOURCE_SERVICE_LIFECYCLE,
    source_ref: object = None,
    confidence: object = PLAYER_SESSION_CONFIDENCE_MEDIUM,
) -> bool:
    timestamp = _safe_session_timestamp(boundary_at, fallback=_utc_now())
    safe_source = _safe_session_source(source)
    safe_source_ref = _safe_session_source_ref(source_ref)
    safe_confidence = _safe_session_confidence(
        confidence,
        default=PLAYER_SESSION_CONFIDENCE_MEDIUM,
    )
    ensure_player_registry_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return _record_player_session_lifecycle_boundary_connection(
            connection,
            boundary_at=timestamp,
            source=safe_source,
            source_ref=safe_source_ref,
            confidence=safe_confidence,
        )


def _fetch_latest_closed_player_session_for_reconnect(
    connection: sqlite3.Connection,
    reliable_id: str,
    *,
    observed_at: str,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT *
        FROM player_sessions
        WHERE reliable_id = ?
          AND status = ?
          AND close_observed_at IS NOT NULL
          AND close_observed_at != ""
          AND close_observed_at <= ?
        ORDER BY close_observed_at DESC, session_id DESC
        LIMIT 1
        """,
        (reliable_id, PLAYER_SESSION_STATUS_CLOSED, observed_at),
    ).fetchone()


def _has_conflicting_open_session(
    connection: sqlite3.Connection,
    reliable_id: str,
    *,
    rpl_identity: str,
    connection_id: str,
    session_player_id: str,
    be_slot: str,
) -> bool:
    checks = (
        ("rpl_identity", rpl_identity),
        ("connection_id", connection_id),
        ("session_player_id", session_player_id),
        ("be_slot", be_slot),
    )
    for column, value in checks:
        if not value:
            continue
        row = connection.execute(
            f"""
            SELECT 1
            FROM player_sessions
            WHERE reliable_id != ?
              AND status = ?
              AND {column} = ?
            LIMIT 1
            """,
            (reliable_id, PLAYER_SESSION_STATUS_OPEN, value),
        ).fetchone()
        if row is not None:
            return True
    return False


def _can_merge_reconnect_session(
    connection: sqlite3.Connection,
    session: sqlite3.Row,
    *,
    observed_at: str,
    server_run_key: str,
    reconnect_grace_seconds: int,
    rpl_identity: str,
    connection_id: str,
    session_player_id: str,
    be_slot: str,
) -> bool:
    close_observed_at = _safe_session_text(
        _row_value(session, "close_observed_at"),
        max_length=80,
    )
    if not close_observed_at:
        return False
    end_reason = _safe_session_end_reason(_row_value(session, "end_reason"))
    if end_reason not in PLAYER_SESSION_RECONNECT_COMPATIBLE_END_REASONS:
        return False
    if _safe_session_text(_row_value(session, "server_run_key"), max_length=120) != server_run_key:
        return False
    gap_seconds = _session_gap_seconds(close_observed_at, observed_at)
    if gap_seconds is None or gap_seconds < 0:
        return False
    if gap_seconds > reconnect_grace_seconds:
        return False
    if _has_lifecycle_boundary_between(
        connection,
        after=close_observed_at,
        at=observed_at,
    ):
        return False
    reliable_id = normalize_reliable_player_id(session["reliable_id"])
    return not _has_conflicting_open_session(
        connection,
        reliable_id,
        rpl_identity=rpl_identity,
        connection_id=connection_id,
        session_player_id=session_player_id,
        be_slot=be_slot,
    )


def _close_player_session_row(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    close_timestamp: str,
    source: str,
    source_ref: str,
    confidence: str,
    end_reason: str,
) -> sqlite3.Row | None:
    session_id = int(row["session_id"])
    if end_reason == PLAYER_SESSION_END_REASON_SERVER_BOUNDARY:
        _record_player_session_lifecycle_boundary_connection(
            connection,
            boundary_at=close_timestamp,
            source=source,
            source_ref=source_ref,
            confidence=confidence,
        )
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
            source,
            _nullable(source_ref),
            confidence,
            end_reason,
            close_timestamp,
            session_id,
        ),
    )
    return _fetch_player_session_by_id(connection, session_id)


def _reopen_player_session_row_for_reconnect(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    timestamp: str,
    name_last: str,
    source: str,
    source_ref: str,
    confidence: str,
    rpl_identity: str,
    connection_id: str,
    session_player_id: str,
    be_slot: str,
    faction: str,
    side: str,
    server_run_key: str,
    gameplay_evidence_at: str,
    gameplay_source: str,
    gameplay_source_ref: str,
    gameplay_confidence: str,
    scanner_checkpoint_source: str,
    scanner_checkpoint_ref: str,
    scanner_checkpoint_at: str,
) -> sqlite3.Row | None:
    session_id = int(row["session_id"])
    reconnect_count = int(_row_value(row, "reconnect_merge_count", 0) or 0) + 1
    values = {
        "name_last": name_last,
        "last_seen_at": timestamp,
        "status": PLAYER_SESSION_STATUS_OPEN,
        "last_seen_source": source,
        "last_seen_source_ref": _nullable(source_ref),
        "close_observed_at": None,
        "close_source": None,
        "close_source_ref": None,
        "close_confidence": None,
        "end_reason": None,
        "last_seen_confidence": confidence,
        "rpl_identity": _session_update_value(row, "rpl_identity", rpl_identity),
        "connection_id": _session_update_value(row, "connection_id", connection_id),
        "session_player_id": _session_update_value(row, "session_player_id", session_player_id),
        "be_slot": _session_update_value(row, "be_slot", be_slot),
        "faction": _session_update_value(row, "faction", faction),
        "side": _session_update_value(row, "side", side),
        "server_run_key": server_run_key,
        "reconnect_merge_count": reconnect_count,
        "last_reconnect_at": timestamp,
        "last_reconnect_close_observed_at": _safe_session_text(
            row["close_observed_at"],
            max_length=80,
        ),
        "last_reconnect_close_reason": _safe_session_end_reason(row["end_reason"]),
        "last_gameplay_evidence_at": _session_update_value(
            row,
            "last_gameplay_evidence_at",
            gameplay_evidence_at,
        ),
        "last_gameplay_source": _session_update_value(row, "last_gameplay_source", gameplay_source),
        "last_gameplay_source_ref": _session_update_value(
            row,
            "last_gameplay_source_ref",
            gameplay_source_ref,
        ),
        "last_gameplay_confidence": _session_update_value(
            row,
            "last_gameplay_confidence",
            gameplay_confidence,
        ),
        "scanner_checkpoint_source": _session_update_value(
            row,
            "scanner_checkpoint_source",
            scanner_checkpoint_source,
        ),
        "scanner_checkpoint_ref": _session_update_value(
            row,
            "scanner_checkpoint_ref",
            scanner_checkpoint_ref,
        ),
        "scanner_checkpoint_at": _session_update_value(
            row,
            "scanner_checkpoint_at",
            scanner_checkpoint_at,
        ),
        "updated_at": timestamp,
    }
    assignments = ", ".join(f"{column} = ?" for column in values)
    connection.execute(
        f"UPDATE player_sessions SET {assignments} WHERE session_id = ?",
        (*values.values(), session_id),
    )
    return _fetch_player_session_by_id(connection, session_id)


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
    reconnect_grace_seconds: object = DEFAULT_PLAYER_SESSION_RECONNECT_GRACE_SECONDS,
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
    safe_reconnect_grace_seconds = _safe_reconnect_grace_seconds(
        reconnect_grace_seconds,
    )
    gameplay_evidence_at = timestamp if _is_gameplay_session_source(safe_source) else ""
    gameplay_source = safe_source if gameplay_evidence_at else ""
    gameplay_source_ref = safe_source_ref if gameplay_evidence_at else ""
    gameplay_confidence = safe_confidence if gameplay_evidence_at else ""

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
        server_run_key = _latest_session_lifecycle_boundary_key(
            connection,
            at=timestamp,
        )
        open_row = _fetch_open_player_session(connection, normalized_id)
        if open_row is not None:
            boundary_after_last_seen = _first_lifecycle_boundary_between(
                connection,
                after=_safe_session_text(open_row["open_observed_at"], max_length=80),
                at=timestamp,
            )
            if boundary_after_last_seen is not None:
                _close_player_session_row(
                    connection,
                    open_row,
                    close_timestamp=_safe_session_text(
                        boundary_after_last_seen["boundary_at"],
                        max_length=80,
                    ),
                    source=_safe_session_source(boundary_after_last_seen["source"]),
                    source_ref=_safe_session_source_ref(
                        boundary_after_last_seen["source_ref"],
                    ),
                    confidence=_safe_session_confidence(
                        boundary_after_last_seen["confidence"],
                        default=PLAYER_SESSION_CONFIDENCE_MEDIUM,
                    ),
                    end_reason=PLAYER_SESSION_END_REASON_SERVER_BOUNDARY,
                )
                open_row = None
        if open_row is None:
            closed_row = _fetch_latest_closed_player_session_for_reconnect(
                connection,
                normalized_id,
                observed_at=timestamp,
            )
            if closed_row is not None and _can_merge_reconnect_session(
                connection,
                closed_row,
                observed_at=timestamp,
                server_run_key=server_run_key,
                reconnect_grace_seconds=safe_reconnect_grace_seconds,
                rpl_identity=safe_rpl_identity,
                connection_id=safe_connection_id,
                session_player_id=safe_session_player_id,
                be_slot=safe_be_slot,
            ):
                row = _reopen_player_session_row_for_reconnect(
                    connection,
                    closed_row,
                    timestamp=timestamp,
                    name_last=name_for_observation,
                    source=safe_source,
                    source_ref=safe_source_ref,
                    confidence=safe_confidence,
                    rpl_identity=safe_rpl_identity,
                    connection_id=safe_connection_id,
                    session_player_id=safe_session_player_id,
                    be_slot=safe_be_slot,
                    faction=safe_faction,
                    side=safe_side,
                    server_run_key=server_run_key,
                    gameplay_evidence_at=gameplay_evidence_at,
                    gameplay_source=gameplay_source,
                    gameplay_source_ref=gameplay_source_ref,
                    gameplay_confidence=gameplay_confidence,
                    scanner_checkpoint_source=safe_checkpoint_source,
                    scanner_checkpoint_ref=safe_checkpoint_ref,
                    scanner_checkpoint_at=safe_checkpoint_at,
                )
                return PlayerSessionWriteResult(
                    written=True,
                    updated=True,
                    reconnected=True,
                    session=_player_session_record_from_row(row),
                )

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
                    server_run_key,
                    last_gameplay_evidence_at,
                    last_gameplay_source,
                    last_gameplay_source_ref,
                    last_gameplay_confidence,
                    scanner_checkpoint_source,
                    scanner_checkpoint_ref,
                    scanner_checkpoint_at,
                    created_at,
                    updated_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
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
                    server_run_key,
                    _nullable(gameplay_evidence_at),
                    _nullable(gameplay_source),
                    _nullable(gameplay_source_ref),
                    _nullable(gameplay_confidence),
                    _nullable(safe_checkpoint_source),
                    _nullable(safe_checkpoint_ref),
                    _nullable(safe_checkpoint_at),
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                """
                UPDATE player_sessions
                SET play_session_id = ?
                WHERE session_id = ?
                """,
                (cursor.lastrowid, cursor.lastrowid),
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
                server_run_key = ?,
                last_gameplay_evidence_at = ?,
                last_gameplay_source = ?,
                last_gameplay_source_ref = ?,
                last_gameplay_confidence = ?,
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
                server_run_key,
                _session_update_value(
                    open_row,
                    "last_gameplay_evidence_at",
                    gameplay_evidence_at,
                ),
                _session_update_value(open_row, "last_gameplay_source", gameplay_source),
                _session_update_value(
                    open_row,
                    "last_gameplay_source_ref",
                    gameplay_source_ref,
                ),
                _session_update_value(
                    open_row,
                    "last_gameplay_confidence",
                    gameplay_confidence,
                ),
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
            row = _close_player_session_row(
                connection,
                open_row,
                close_timestamp=close_timestamp,
                source=safe_source,
                source_ref=safe_source_ref,
                confidence=safe_confidence,
                end_reason=safe_end_reason,
            )
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


def summarize_player_sessions(db_path: Path) -> PlayerSessionSummary:
    """Return counts-only player-session summary without mutating storage."""
    connection = _connect_existing_readonly(db_path)
    if connection is None:
        return PlayerSessionSummary()
    if not _table_exists(connection, "player_sessions"):
        connection.close()
        return PlayerSessionSummary()

    inferred_reason_placeholders = ", ".join(
        "?" for _reason in PLAYER_SESSION_INFERRED_OR_STALE_END_REASONS
    )
    try:
        counts_row = connection.execute(
            f"""
            SELECT
                COALESCE(SUM(CASE WHEN status = ? THEN 1 ELSE 0 END), 0)
                    AS open_sessions,
                COALESCE(SUM(CASE WHEN status = ? THEN 1 ELSE 0 END), 0)
                    AS closed_sessions,
                COALESCE(
                    SUM(
                        CASE
                            WHEN status = ?
                             AND COALESCE(end_reason, '') IN ({inferred_reason_placeholders})
                            THEN 1 ELSE 0
                        END
                    ),
                    0
                ) AS inferred_or_stale_closes
            FROM player_sessions
            """,
            (
                PLAYER_SESSION_STATUS_OPEN,
                PLAYER_SESSION_STATUS_CLOSED,
                PLAYER_SESSION_STATUS_CLOSED,
                *PLAYER_SESSION_INFERRED_OR_STALE_END_REASONS,
            ),
        ).fetchone()
        latest_row = connection.execute(
            """
            SELECT MAX(observed_at) AS latest_observed_at
            FROM (
                SELECT open_observed_at AS observed_at FROM player_sessions
                UNION ALL
                SELECT last_seen_at AS observed_at FROM player_sessions
                UNION ALL
                SELECT close_observed_at AS observed_at FROM player_sessions
            )
            WHERE observed_at IS NOT NULL
              AND observed_at != ''
            """
        ).fetchone()
    finally:
        connection.close()

    return PlayerSessionSummary(
        open_sessions=int(counts_row["open_sessions"] or 0),
        closed_sessions=int(counts_row["closed_sessions"] or 0),
        inferred_or_stale_closes=int(counts_row["inferred_or_stale_closes"] or 0),
        latest_observed_at=_safe_session_text(
            latest_row["latest_observed_at"],
            max_length=80,
        ),
    )


def list_open_player_sessions(db_path: Path) -> list[PlayerSessionRecord]:
    """Return sanitized currently open player sessions."""
    connection = _connect_existing(db_path)
    if connection is None:
        return []
    try:
        rows = connection.execute(
            """
            SELECT *
            FROM player_sessions
            WHERE status = ?
            ORDER BY session_id ASC
            """,
            (PLAYER_SESSION_STATUS_OPEN,),
        ).fetchall()
    finally:
        connection.close()
    return [_player_session_record_from_row(row) for row in rows]


def _normalize_session_status_filter(value: object) -> str:
    candidate = _safe_session_text(value, max_length=40)
    if candidate in PLAYER_SESSION_STATUSES:
        return candidate
    return ""


def _normalize_session_end_reason_filter(value: object) -> str:
    candidate = _safe_session_text(value, max_length=80)
    if candidate in PLAYER_SESSION_END_REASONS:
        return candidate
    return ""


def list_player_sessions(
    db_path: Path,
    *,
    limit: object = DEFAULT_PLAYER_SESSION_LIST_LIMIT,
    reliable_id: object = "",
    query: object = "",
    status: object = "",
    end_reason: object = "",
    source: object = "",
) -> list[PlayerSessionRecord]:
    """List sanitized player sessions newest first without mutating storage."""
    connection = _connect_existing_readonly(db_path)
    if connection is None:
        return []
    if not _table_exists(connection, "player_sessions"):
        connection.close()
        return []

    normalized_limit = _bounded_player_session_list_limit(limit)
    raw_reliable_id = safe_player_text(reliable_id, max_length=120)
    normalized_reliable_id = normalize_reliable_player_id(raw_reliable_id)
    if raw_reliable_id and not normalized_reliable_id:
        connection.close()
        return []
    normalized_query = safe_player_text(query, max_length=120)
    normalized_status = _normalize_session_status_filter(status)
    normalized_end_reason = _normalize_session_end_reason_filter(end_reason)
    normalized_source = _safe_session_source(source, default="")

    where_clauses: list[str] = []
    params: list[object] = []
    if normalized_reliable_id:
        where_clauses.append("reliable_id = ?")
        params.append(normalized_reliable_id)
    if normalized_query:
        where_clauses.append("(name_at_open LIKE ? OR name_last LIKE ?)")
        pattern = f"%{normalized_query}%"
        params.extend((pattern, pattern))
    if normalized_status:
        where_clauses.append("status = ?")
        params.append(normalized_status)
    if normalized_end_reason:
        where_clauses.append("COALESCE(end_reason, '') = ?")
        params.append(normalized_end_reason)
    if normalized_source:
        where_clauses.append(
            "("
            "open_source = ? OR "
            "last_seen_source = ? OR "
            "COALESCE(close_source, '') = ?"
            ")"
        )
        params.extend((normalized_source, normalized_source, normalized_source))

    sql = "SELECT * FROM player_sessions"
    if where_clauses:
        sql += " WHERE " + " AND ".join(where_clauses)
    sql += (
        " ORDER BY COALESCE(close_observed_at, last_seen_at, open_observed_at) "
        "DESC, session_id DESC LIMIT ?"
    )
    params.append(normalized_limit)

    try:
        rows = connection.execute(sql, tuple(params)).fetchall()
    finally:
        connection.close()
    return [_player_session_record_from_row(row) for row in rows]


def clear_player_session_live_absence_windows(
    db_path: Path,
    *,
    reliable_ids: Iterable[object],
    source: object = PLAYER_SESSION_SOURCE_SCANNER_CHECKPOINT,
    source_ref: object = None,
) -> int:
    """Clear pending live absence windows for players seen in a reliable scan."""
    normalized_ids: set[str] = set()
    for reliable_id in reliable_ids:
        normalized_id = normalize_reliable_player_id(reliable_id)
        if normalized_id:
            normalized_ids.add(normalized_id)
    if not normalized_ids:
        return 0

    safe_source = _safe_session_source(source)
    safe_source_ref = _safe_session_source_ref(source_ref)
    connection = _connect_existing(db_path)
    if connection is None:
        return 0

    placeholders = ", ".join("?" for _ in normalized_ids)
    parameters: tuple[object, ...] = (
        safe_source,
        safe_source_ref,
        *sorted(normalized_ids),
    )
    try:
        with connection:
            cursor = connection.execute(
                f"""
                DELETE FROM player_session_live_scan_windows
                WHERE window_source = ?
                  AND window_ref = ?
                  AND reliable_id IN ({placeholders})
                """,
                parameters,
            )
            return max(int(cursor.rowcount or 0), 0)
    finally:
        connection.close()


def record_player_session_live_absence(
    db_path: Path,
    *,
    session: PlayerSessionRecord,
    observed_at: str | None = None,
    source: object = PLAYER_SESSION_SOURCE_SCANNER_CHECKPOINT,
    source_ref: object = None,
    confirmation_scans: object = DEFAULT_PLAYER_SESSION_ABSENCE_CONFIRMATION_SCANS,
) -> PlayerSessionAbsenceWindowResult:
    """Advance one open session's reliable live absence window.

    The caller must already have established reliable RCON roster semantics for
    this scan. Repeated runs with the same or an older scan timestamp are
    idempotent and do not advance the absence counter.
    """
    normalized_id = normalize_reliable_player_id(session.reliable_id)
    if not normalized_id:
        return PlayerSessionAbsenceWindowResult(ignored_count=1)

    try:
        session_id = int(session.session_id)
    except (TypeError, ValueError):
        return PlayerSessionAbsenceWindowResult(ignored_count=1)
    if session_id <= 0:
        return PlayerSessionAbsenceWindowResult(ignored_count=1)

    threshold = _bounded_player_session_absence_confirmation_scans(
        confirmation_scans,
    )
    timestamp = _safe_session_timestamp(observed_at, fallback=_utc_now())
    safe_source = _safe_session_source(source)
    safe_source_ref = _safe_session_source_ref(source_ref)
    connection = _connect_existing(db_path)
    if connection is None:
        return PlayerSessionAbsenceWindowResult(ignored_count=1)

    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            row = _fetch_player_session_by_id(connection, session_id)
            if row is None:
                return PlayerSessionAbsenceWindowResult(ignored_count=1)
            row_reliable_id = normalize_reliable_player_id(row["reliable_id"])
            if row_reliable_id != normalized_id:
                return PlayerSessionAbsenceWindowResult(ignored_count=1)
            if str(row["status"] or "") != PLAYER_SESSION_STATUS_OPEN:
                return PlayerSessionAbsenceWindowResult(ignored_count=1)

            window = connection.execute(
                """
                SELECT *
                FROM player_session_live_scan_windows
                WHERE session_id = ?
                  AND window_source = ?
                  AND window_ref = ?
                """,
                (session_id, safe_source, safe_source_ref),
            ).fetchone()
            if window is None:
                connection.execute(
                    """
                    INSERT INTO player_session_live_scan_windows(
                        session_id,
                        reliable_id,
                        window_source,
                        window_ref,
                        first_absent_at,
                        last_absent_at,
                        last_scan_at,
                        absent_scan_count,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        session_id,
                        normalized_id,
                        safe_source,
                        safe_source_ref,
                        timestamp,
                        timestamp,
                        timestamp,
                        timestamp,
                        timestamp,
                    ),
                )
                return PlayerSessionAbsenceWindowResult(
                    advanced=True,
                    confirmation_reached=threshold <= 1,
                    absent_scan_count=1,
                )

            try:
                current_count = int(window["absent_scan_count"])
            except (TypeError, ValueError):
                current_count = 1
            current_count = max(1, current_count)
            last_scan_at = _safe_session_text(window["last_scan_at"], max_length=80)
            if timestamp == last_scan_at or _is_session_timestamp_before(
                timestamp,
                last_scan_at,
            ):
                return PlayerSessionAbsenceWindowResult(
                    absent_scan_count=current_count,
                )

            new_count = current_count + 1
            confirmed_at = _safe_session_text(window["confirmed_at"], max_length=80)
            confirmation_reached = not confirmed_at and new_count >= threshold
            connection.execute(
                """
                UPDATE player_session_live_scan_windows
                SET last_absent_at = ?,
                    last_scan_at = ?,
                    absent_scan_count = ?,
                    updated_at = ?
                WHERE window_id = ?
                """,
                (timestamp, timestamp, new_count, timestamp, int(window["window_id"])),
            )
            return PlayerSessionAbsenceWindowResult(
                advanced=True,
                confirmation_reached=confirmation_reached,
                absent_scan_count=new_count,
            )
    finally:
        connection.close()


def confirm_player_session_live_absence_window(
    db_path: Path,
    *,
    session_id: object,
    confirmed_at: str | None = None,
    source: object = PLAYER_SESSION_SOURCE_SCANNER_CHECKPOINT,
    source_ref: object = None,
) -> bool:
    """Mark one live absence window confirmed after its session is closed."""
    try:
        normalized_session_id = int(session_id)
    except (TypeError, ValueError):
        return False
    if normalized_session_id <= 0:
        return False

    timestamp = _safe_session_timestamp(confirmed_at, fallback=_utc_now())
    safe_source = _safe_session_source(source)
    safe_source_ref = _safe_session_source_ref(source_ref)
    connection = _connect_existing(db_path)
    if connection is None:
        return False
    try:
        with connection:
            cursor = connection.execute(
                """
                UPDATE player_session_live_scan_windows
                SET confirmed_at = ?,
                    updated_at = ?
                WHERE session_id = ?
                  AND window_source = ?
                  AND window_ref = ?
                  AND confirmed_at IS NULL
                """,
                (
                    timestamp,
                    timestamp,
                    normalized_session_id,
                    safe_source,
                    safe_source_ref,
                ),
            )
            return bool(cursor.rowcount)
    finally:
        connection.close()


def list_player_sessions_for_reliable_id(
    db_path: Path,
    reliable_id: object,
) -> list[PlayerSessionRecord]:
    """Return sanitized player sessions for one reliable ID, newest first."""
    normalized_id = normalize_reliable_player_id(reliable_id)
    if not normalized_id:
        return []
    connection = _connect_existing(db_path)
    if connection is None:
        return []
    try:
        rows = connection.execute(
            """
            SELECT *
            FROM player_sessions
            WHERE reliable_id = ?
            ORDER BY open_observed_at DESC, session_id DESC
            """,
            (normalized_id,),
        ).fetchall()
    finally:
        connection.close()
    return [_player_session_record_from_row(row) for row in rows]


def close_stale_open_player_sessions(
    db_path: Path,
    *,
    last_seen_before: str,
    close_observed_at: str | None = None,
    source: object = PLAYER_SESSION_SOURCE_SCANNER_CHECKPOINT,
    source_ref: object = "stale-timeout",
    confidence: object = PLAYER_SESSION_CONFIDENCE_LOW,
    limit: int = DEFAULT_PLAYER_SESSION_MAINTENANCE_BATCH_LIMIT,
) -> PlayerSessionStaleCloseResult:
    """Close overdue open sessions from an explicit stale-close caller.

    The caller must provide the policy cutoff. This helper does not read live
    rosters, infer absence from failed queries, or run from GET routes.
    """
    cutoff = _safe_session_timestamp(last_seen_before, fallback="")
    if not cutoff:
        raise ValueError("last_seen_before is required.")

    batch_limit = _bounded_player_session_maintenance_limit(limit)
    close_timestamp = _safe_session_timestamp(close_observed_at, fallback=_utc_now())
    sessions = list_open_player_sessions(db_path)
    overdue_sessions: list[PlayerSessionRecord] = []
    skipped_count = 0
    for session in sessions:
        if not _session_has_reliable_last_seen(session):
            skipped_count += 1
            continue
        if not _is_session_timestamp_before(session.last_seen_at, cutoff):
            skipped_count += 1
            continue
        overdue_sessions.append(session)

    closed_count = 0
    for session in overdue_sessions[:batch_limit]:
        result = close_player_session(
            db_path,
            reliable_id=session.reliable_id,
            close_observed_at=close_timestamp,
            source=source,
            source_ref=source_ref,
            confidence=confidence,
            end_reason=PLAYER_SESSION_END_REASON_STALE_TIMEOUT,
        )
        if result.closed:
            closed_count += 1
        else:
            skipped_count += 1

    skipped_count += max(len(overdue_sessions) - batch_limit, 0)
    return PlayerSessionStaleCloseResult(
        open_sessions_scanned=len(sessions),
        sessions_overdue=len(overdue_sessions),
        sessions_closed=closed_count,
        sessions_skipped=skipped_count,
    )


def cleanup_player_sessions_by_retention(
    db_path: Path,
    *,
    closed_before: str,
    limit: int = DEFAULT_PLAYER_SESSION_MAINTENANCE_BATCH_LIMIT,
) -> PlayerSessionRetentionCleanupResult:
    """Delete old closed player session rows using an explicit retention cutoff.

    This intentionally touches only `player_sessions`. It does not delete
    players, player_names, or player_log_events evidence rows.
    """
    cutoff = _safe_session_timestamp(closed_before, fallback="")
    if not cutoff:
        raise ValueError("closed_before is required.")

    connection = _connect_existing(db_path)
    if connection is None:
        return PlayerSessionRetentionCleanupResult()

    batch_limit = _bounded_player_session_maintenance_limit(limit)
    try:
        with connection:
            rows = connection.execute(
                """
                SELECT session_id
                FROM player_sessions
                WHERE status = ?
                  AND close_observed_at IS NOT NULL
                  AND close_observed_at < ?
                ORDER BY close_observed_at ASC, session_id ASC
                LIMIT ?
                """,
                (PLAYER_SESSION_STATUS_CLOSED, cutoff, batch_limit),
            ).fetchall()
            session_ids = [int(row["session_id"]) for row in rows]
            if not session_ids:
                return PlayerSessionRetentionCleanupResult()
            placeholders = ", ".join("?" for _ in session_ids)
            cursor = connection.execute(
                f"DELETE FROM player_sessions WHERE session_id IN ({placeholders})",
                session_ids,
            )
            deleted_count = cursor.rowcount if cursor.rowcount >= 0 else len(session_ids)
            return PlayerSessionRetentionCleanupResult(
                sessions_scanned=len(session_ids),
                sessions_deleted=deleted_count,
                sessions_skipped=max(len(session_ids) - deleted_count, 0),
            )
    finally:
        connection.close()


def ingest_player_log_events(
    db_path: Path,
    events: Iterable[PlayerLogEvent],
    *,
    ingested_at: str | None = None,
) -> PlayerLogEventIngestResult:
    """Persist sanitized parsed player log events into the registry database."""
    timestamp = ingested_at or _utc_now()
    rows = tuple(
        _player_log_event_row(event, collected_at=timestamp, created_at=timestamp)
        for event in events
    )

    ensure_player_registry_db(db_path)
    stored_count = 0
    duplicate_count = 0
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        for row in rows:
            if _player_log_event_exists(connection, row):
                duplicate_count += 1
                continue
            observed_at = _trusted_player_log_event_time(row)
            if observed_at:
                _record_player_log_event_observations(
                    connection,
                    row,
                    observed_at=observed_at,
                )
            cursor = _insert_player_log_event(connection, row)
            if cursor.rowcount == 1:
                stored_count += 1
            else:
                duplicate_count += 1

    return PlayerLogEventIngestResult(
        stored_count=stored_count,
        duplicate_count=duplicate_count,
    )


def _player_log_event_exists(
    connection: sqlite3.Connection,
    row: dict[str, object],
) -> bool:
    keys = _player_log_event_lookup_keys(row)
    placeholders = ", ".join("?" for _ in keys)
    found = connection.execute(
        f"""
        SELECT 1
        FROM player_log_events
        WHERE event_key IN ({placeholders})
        LIMIT 1
        """,
        keys,
    ).fetchone()
    return found is not None


def _player_log_event_lookup_keys(row: dict[str, object]) -> tuple[str, ...]:
    keys = [str(row.get("event_key") or "")]
    keys.extend(_player_log_event_legacy_v7_keys(row))
    deduped: list[str] = []
    for key in keys:
        if key and key not in deduped:
            deduped.append(key)
    return tuple(deduped)


def _player_log_event_legacy_v7_keys(row: dict[str, object]) -> tuple[str, ...]:
    legacy_row = dict(row)
    legacy_row["occurred_at"] = None
    legacy_row["time_source"] = None
    legacy_row["time_confidence"] = None
    legacy_row["collected_at"] = None
    keys = [
        _player_log_event_key(
            legacy_row,
            dedupe_columns=_PLAYER_LOG_EVENT_LEGACY_V7_DEDUPE_COLUMNS,
        )
    ]
    if legacy_row.get("log_timestamp"):
        legacy_without_log_timestamp = dict(legacy_row)
        legacy_without_log_timestamp["log_timestamp"] = None
        keys.append(
            _player_log_event_key(
                legacy_without_log_timestamp,
                dedupe_columns=_PLAYER_LOG_EVENT_LEGACY_V7_DEDUPE_COLUMNS,
            )
        )
    return tuple(keys)


def _trusted_player_log_event_time(row: dict[str, object]) -> str:
    return str(row.get("occurred_at") or row.get("observed_at") or "")


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
    collected_at: str,
    created_at: str,
) -> dict[str, object]:
    occurred_at = _safe_event_text(event.occurred_at)
    observed_at = _safe_event_text(event.observed_at)
    raw_timestamp = _safe_event_text(event.raw_timestamp)
    row: dict[str, object] = {
        "event_key": "",
        "event_type": _safe_event_text(event.event_type) or "unknown",
        "source": _safe_event_text(event.source) or "unknown",
        "source_ref": _safe_event_source_ref(event.raw_source_ref),
        "confidence": _safe_event_text(event.confidence) or "unknown",
        "occurred_at": occurred_at,
        "observed_at": observed_at,
        "log_timestamp": raw_timestamp,
        "time_source": _event_time_source(event, occurred_at, observed_at, raw_timestamp),
        "time_confidence": _event_time_confidence(
            event,
            occurred_at,
            observed_at,
            raw_timestamp,
        ),
        "player_id": _safe_event_player_id(event.player_id),
        "player_name": _safe_event_text(event.player_name),
        "session_player_id": _safe_event_correlation(event.session_player_id),
        "rpl_identity": _safe_event_correlation(event.rpl_identity),
        "connection_id": _safe_event_correlation(event.connection_id),
        "be_slot": _safe_event_correlation(event.be_slot),
        "player_faction": _safe_event_text(event.player_faction),
        "faction_resource": _safe_event_text(event.faction_resource),
        "victim_id": _safe_event_player_id(event.victim_id),
        "victim_name": _safe_event_text(event.victim_name),
        "victim_session_player_id": _safe_event_correlation(
            event.victim_session_player_id,
        ),
        "victim_faction": _safe_event_text(event.victim_faction),
        "instigator_id": _safe_event_player_id(event.instigator_id),
        "instigator_name": _safe_event_text(event.instigator_name),
        "instigator_session_player_id": _safe_event_correlation(
            event.instigator_session_player_id,
        ),
        "instigator_faction": _safe_event_text(event.instigator_faction),
        "teamkill": _event_bool(event.teamkill),
        "suicide": _event_bool(event.suicide),
        "ai_instigator": _event_bool(event.ai_instigator),
        "damage_type": _safe_event_text(event.damage_type),
        "hit_zone": _safe_event_text(event.hit_zone),
        "distance_m": _event_distance(event.distance_m),
        "collected_at": collected_at,
        "created_at": created_at,
    }
    row["event_key"] = _player_log_event_key(row)
    return row


def _event_time_source(
    event: PlayerLogEvent,
    occurred_at: str | None,
    observed_at: str | None,
    raw_timestamp: str | None,
) -> str:
    source = _safe_event_text(event.time_source, max_length=80)
    if source:
        return source
    if occurred_at:
        return EVENT_TIME_SOURCE_CALLER_OCCURRED_AT
    if observed_at:
        return EVENT_TIME_SOURCE_CALLER_OBSERVED_AT
    if raw_timestamp:
        return EVENT_TIME_SOURCE_LOG_PREFIX_WITHOUT_DATE
    return EVENT_TIME_SOURCE_UNAVAILABLE


def _event_time_confidence(
    event: PlayerLogEvent,
    occurred_at: str | None,
    observed_at: str | None,
    raw_timestamp: str | None,
) -> str:
    confidence = _safe_event_text(event.time_confidence, max_length=80)
    if confidence:
        return confidence
    if occurred_at or observed_at:
        return EVENT_TIME_CONFIDENCE_EXACT
    if raw_timestamp:
        return EVENT_TIME_CONFIDENCE_AMBIGUOUS
    return EVENT_TIME_CONFIDENCE_AMBIGUOUS


def _player_log_event_key(
    row: dict[str, object],
    *,
    dedupe_columns: tuple[str, ...] = _PLAYER_LOG_EVENT_DEDUPE_COLUMNS,
) -> str:
    payload = {column: row.get(column) for column in dedupe_columns}
    if row.get("event_type") == EVENT_TYPE_PLAYER_DISCONNECTED:
        payload["connection_id"] = row.get("connection_id")
        payload["be_slot"] = row.get("be_slot")
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


def _safe_event_correlation(value: object) -> str | None:
    raw_text = "" if value is None else str(value).strip()
    if "/" in raw_text or "\\" in raw_text:
        return None
    text = _safe_event_text(raw_text, max_length=80)
    if text == "***":
        return None
    return text


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
    player_events_only: bool = False,
    session_evidence_only: bool = False,
) -> list[PlayerLogEventRecord]:
    """List sanitized stored player log events, newest first."""
    connection = _connect_existing_readonly(db_path)
    if connection is None:
        return []
    if not _table_exists(connection, "player_log_events"):
        connection.close()
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
    if player_events_only:
        where_clauses.append(
            "("
            "event_type NOT IN (?, ?) "
            "AND ("
            "(COALESCE(player_id, '') <> '' "
            "OR COALESCE(victim_id, '') <> '' "
            "OR COALESCE(instigator_id, '') <> '') "
            "OR ("
            "event_type <> ? "
            "AND (COALESCE(player_name, '') <> '' "
            "OR COALESCE(victim_name, '') <> '' "
            "OR COALESCE(instigator_name, '') <> '')"
            ")"
            ")"
            ")"
        )
        params.extend(
            (
                EVENT_TYPE_SERVER_LIFECYCLE,
                EVENT_TYPE_PLAYER_DISCONNECTED,
                EVENT_TYPE_COMBAT_HINT,
            )
        )
    if session_evidence_only:
        where_clauses.append(
            "("
            "event_type IN (?, ?) "
            "OR (event_type = ? "
            "AND COALESCE(player_id, '') = '' "
            "AND COALESCE(victim_id, '') = '' "
            "AND COALESCE(instigator_id, '') = '')"
            ")"
        )
        params.extend(
            (
                EVENT_TYPE_SERVER_LIFECYCLE,
                EVENT_TYPE_PLAYER_DISCONNECTED,
                EVENT_TYPE_COMBAT_HINT,
            )
        )
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
            "session_player_id",
            "rpl_identity",
            "connection_id",
            "be_slot",
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
    sql += (
        " ORDER BY COALESCE(occurred_at, observed_at, collected_at, created_at) "
        "DESC, event_id DESC LIMIT ?"
    )
    params.append(normalized_limit)

    try:
        with connection:
            rows = connection.execute(sql, tuple(params)).fetchall()
        return [_player_log_event_record_from_row(row) for row in rows]
    except (IndexError, KeyError, sqlite3.OperationalError):
        return []
    finally:
        connection.close()


def list_player_log_events_for_sessionization(
    db_path: Path,
) -> list[PlayerLogEventRecord]:
    """List stored player log events in stable ingest order for sessionization."""
    connection = _connect_existing(db_path)
    if connection is None:
        return []

    try:
        with connection:
            rows = connection.execute(
                """
                SELECT *
                FROM player_log_events
                ORDER BY COALESCE(occurred_at, observed_at, collected_at, created_at) ASC,
                         event_id ASC
                """
            ).fetchall()
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
                COALESCE(occurred_at, observed_at, collected_at, created_at) AS event_time,
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
