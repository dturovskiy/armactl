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
from armactl.player_log_events import PlayerLogEvent
from armactl.web.services.player_identity import (
    normalize_reliable_player_id,
    safe_player_text,
)

PLAYER_REGISTRY_DB_NAME = "players.db"
PLAYER_REGISTRY_SCHEMA_VERSION = "2"
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
    text = _safe_event_text(
        value,
        max_length=PLAYER_LOG_EVENT_REF_MAX_LENGTH,
    )
    if not text:
        return None
    normalized = text.replace("\\", "/")
    if "/" not in normalized:
        return text
    return normalized.rsplit("/", 1)[-1].strip() or None


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
