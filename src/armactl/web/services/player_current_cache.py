'''Runtime and persistent safe cache for current-player roster snapshots.'''

from __future__ import annotations

import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from armactl import paths
from armactl.web.runtime import ensure_web_db, web_db_file
from armactl.web.services import player_sources
from armactl.web.services.player_identity import (
    normalize_reliable_player_id,
    safe_player_text,
)

CURRENT_ROSTER_CACHE_TTL_SECONDS = 10
STALE_ROSTER_UNAVAILABLE_CACHE_MAX_AGE_SECONDS = 120
_PERSISTENT_SNAPSHOT_TABLE = "web_current_roster_cache"
_PERSISTENT_PLAYER_TABLE = "web_current_roster_cache_players"
_SOURCE_UNAVAILABLE = 'unavailable'
_STATUS_UNAVAILABLE = 'unavailable'
_STATUS_UNKNOWN = 'unknown'
_IPV4_ADDRESS_RE = re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?\b')
_BRACKETED_IPV6_ADDRESS_RE = re.compile(r'\[[0-9A-Fa-f:.]{2,}\](?::\d{1,5})?')


@dataclass(frozen=True)
class CurrentRosterPlayerSnapshot:
    '''One sanitized player row safe for runtime cache and web DTOs.'''

    display_name: str
    reliable_id: str
    source: str


@dataclass(frozen=True)
class CurrentRosterSnapshot:
    '''Sanitized current-player roster snapshot safe for memory and web.db cache.'''

    instance: str
    players: tuple[CurrentRosterPlayerSnapshot, ...]
    source: str
    status: str
    error: str
    collected_at: str
    updated_at: str = ""
    observed_count: int | None = None
    count_source: str = "unknown"
    roster_available: bool = False
    roster_configured: bool = False

    @property
    def available(self) -> bool:
        return self.status == 'available'

    @property
    def total_count(self) -> int:
        if self.observed_count is not None:
            return self.observed_count
        return len(self.players)


@dataclass(frozen=True)
class CurrentRosterSnapshotResult:
    '''Current roster result with cache freshness metadata for callers.'''

    snapshot: CurrentRosterSnapshot
    age_seconds: int | None
    is_stale: bool
    cache_status: str
    refresh_error: str = ''


_cache_lock = threading.Lock()
_current_roster_cache: dict[tuple[str, str], CurrentRosterSnapshot] = {}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_text() -> str:
    return _utc_now().isoformat()


def _safe_instance(instance: object) -> str:
    try:
        return paths.validate_instance_name(str(instance or paths.DEFAULT_INSTANCE_NAME))
    except paths.InvalidInstanceNameError:
        return paths.DEFAULT_INSTANCE_NAME


def _cache_scope(data_root: Path) -> str:
    return str(Path(data_root).expanduser())


def _cache_key(instance: object, data_root: Path) -> tuple[str, str]:
    return (_safe_instance(instance), _cache_scope(data_root))


def _parse_collected_at(value: object) -> datetime | None:
    text = safe_player_text(value, max_length=80)
    if not text:
        return None
    normalized = f'{text[:-1]}+00:00' if text.endswith('Z') else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def current_roster_snapshot_age_seconds(
    snapshot: CurrentRosterSnapshot,
    *,
    now: datetime | None = None,
) -> int | None:
    '''Return a bounded whole-second age for a cached roster snapshot.'''
    collected_at = _parse_collected_at(snapshot.collected_at)
    if collected_at is None:
        return None
    current = now or _utc_now()
    age = current.astimezone(timezone.utc) - collected_at
    if age < timedelta(0):
        return 0
    return int(age.total_seconds())


def current_roster_snapshot_is_fresh(
    snapshot: CurrentRosterSnapshot | None,
    *,
    max_age_seconds: int = CURRENT_ROSTER_CACHE_TTL_SECONDS,
    now: datetime | None = None,
) -> bool:
    '''Return whether a cached roster snapshot is fresh enough for GET use.'''
    if snapshot is None:
        return False
    age_seconds = current_roster_snapshot_age_seconds(snapshot, now=now)
    if age_seconds is None:
        return False
    return age_seconds <= max(0, int(max_age_seconds))


def _current_roster_snapshot_within_age(
    snapshot: CurrentRosterSnapshot | None,
    *,
    max_age_seconds: int | None,
    now: datetime | None = None,
) -> bool:
    if snapshot is None or max_age_seconds is None:
        return False
    age_seconds = current_roster_snapshot_age_seconds(snapshot, now=now)
    if age_seconds is None:
        return False
    return age_seconds <= max(0, int(max_age_seconds))


def _should_keep_stale_roster_snapshot(
    live_snapshot: CurrentRosterSnapshot,
    stale_snapshot: CurrentRosterSnapshot | None,
    *,
    max_stale_age_seconds: int | None,
) -> bool:
    if stale_snapshot is None:
        return False
    if not _current_roster_snapshot_within_age(
        stale_snapshot,
        max_age_seconds=max_stale_age_seconds,
    ):
        return False
    if not stale_snapshot.available or not stale_snapshot.roster_available:
        return False
    if stale_snapshot.total_count <= 0:
        return False
    if live_snapshot.total_count != 0:
        return False
    if live_snapshot.roster_available:
        return False
    if not live_snapshot.roster_configured:
        return False
    return live_snapshot.count_source != "rcon"


def _redact_ips_and_paths(text: str) -> str:
    text = _IPV4_ADDRESS_RE.sub('***', text)
    text = _BRACKETED_IPV6_ADDRESS_RE.sub('***', text)
    parts: list[str] = []
    for token in text.split():
        prefix = token[: len(token) - len(token.lstrip('([{<'))]
        suffix = token[len(token.rstrip(')]}>,.;:')) :]
        core_end = len(token) - len(suffix) if suffix else len(token)
        core = token[len(prefix) : core_end]
        backslash = chr(92)
        if '/' in core or backslash in core:
            core = core.replace(backslash, '/').rsplit('/', 1)[-1]
        parts.append(f'{prefix}{core}{suffix}')
    return ' '.join(parts)


def _safe_snapshot_text(
    value: object,
    *,
    max_length: int = 160,
    default: str = '',
) -> str:
    text = safe_player_text(value, max_length=max_length)
    text = _redact_ips_and_paths(text).strip()
    return text or default


def _safe_observed_count(
    value: object,
    *,
    fallback: int | None = None,
) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    if parsed < 0:
        return fallback
    return parsed


def _snapshot_player(
    player: player_sources.CurrentPlayer,
) -> CurrentRosterPlayerSnapshot:
    return CurrentRosterPlayerSnapshot(
        display_name=_safe_snapshot_text(player.display_name, default='Unknown player'),
        reliable_id=normalize_reliable_player_id(player.reliable_id),
        source=_safe_snapshot_text(player.source, max_length=80, default='unknown'),
    )


def snapshot_from_roster(
    roster: player_sources.CurrentPlayerRoster,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    collected_at: str | None = None,
) -> CurrentRosterSnapshot:
    '''Build a cache-safe current roster snapshot from a live source result.'''
    players = tuple(_snapshot_player(player) for player in roster.players)
    status_default = 'available' if roster.available else _STATUS_UNAVAILABLE
    observed_at = collected_at or _utc_now_text()
    observed_count = _safe_observed_count(
        roster.observed_count,
        fallback=roster.total_count,
    )
    if observed_count is None:
        observed_count = len(players)
    roster_available = bool(roster.roster_available or players)
    count_source = _safe_snapshot_text(
        roster.count_source,
        max_length=80,
        default='unknown',
    )
    if count_source == 'unknown' and roster_available:
        count_source = 'rcon'
    return CurrentRosterSnapshot(
        instance=_safe_instance(instance),
        players=players,
        source=_safe_snapshot_text(roster.source, max_length=80, default=_SOURCE_UNAVAILABLE),
        status=_safe_snapshot_text(roster.status, max_length=80, default=status_default),
        error=_safe_snapshot_text(roster.error, max_length=240),
        collected_at=observed_at,
        updated_at=observed_at,
        observed_count=observed_count,
        count_source=count_source,
        roster_available=roster_available,
        roster_configured=bool(roster.roster_configured or roster_available),
    )


def unavailable_snapshot_from_error(
    error: object,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    collected_at: str | None = None,
) -> CurrentRosterSnapshot:
    '''Build a cache-safe unavailable snapshot for controlled source failures.'''
    observed_at = collected_at or _utc_now_text()
    return CurrentRosterSnapshot(
        instance=_safe_instance(instance),
        players=(),
        source=_SOURCE_UNAVAILABLE,
        status=_STATUS_UNAVAILABLE,
        error=_safe_snapshot_text(error, max_length=240),
        collected_at=observed_at,
        updated_at=observed_at,
        observed_count=0,
        count_source=_SOURCE_UNAVAILABLE,
        roster_available=False,
        roster_configured=False,
    )


class CurrentRosterPersistentCacheError(RuntimeError):
    """Raised when persistent current-roster cache cannot be written."""


def _safe_timestamp_text(value: object) -> str:
    text = _safe_snapshot_text(value, max_length=80)
    if _parse_collected_at(text) is None:
        return _utc_now_text()
    return text


def _safe_cached_player(
    player: CurrentRosterPlayerSnapshot,
) -> CurrentRosterPlayerSnapshot:
    return CurrentRosterPlayerSnapshot(
        display_name=_safe_snapshot_text(player.display_name, default="Unknown player"),
        reliable_id=normalize_reliable_player_id(player.reliable_id),
        source=_safe_snapshot_text(player.source, max_length=80, default="unknown"),
    )


def _safe_current_roster_snapshot(
    snapshot: CurrentRosterSnapshot,
    *,
    updated_at: str | None = None,
) -> CurrentRosterSnapshot:
    collected_at = _safe_timestamp_text(snapshot.collected_at)
    updated_at_value = updated_at or snapshot.updated_at or collected_at
    safe_updated_at = _safe_timestamp_text(updated_at_value)
    players = tuple(_safe_cached_player(player) for player in snapshot.players)
    return CurrentRosterSnapshot(
        instance=_safe_instance(snapshot.instance),
        players=players,
        source=_safe_snapshot_text(snapshot.source, max_length=80, default=_SOURCE_UNAVAILABLE),
        status=_safe_snapshot_text(snapshot.status, max_length=80, default=_STATUS_UNKNOWN),
        error=_safe_snapshot_text(snapshot.error, max_length=240),
        collected_at=collected_at,
        updated_at=safe_updated_at,
        observed_count=_safe_observed_count(
            snapshot.observed_count,
            fallback=len(players),
        ),
        count_source=_safe_snapshot_text(
            snapshot.count_source,
            max_length=80,
            default='unknown',
        ),
        roster_available=bool(snapshot.roster_available or players),
        roster_configured=bool(snapshot.roster_configured or snapshot.roster_available),
    )


def clear_current_roster_cache() -> None:
    '''Clear all runtime current-roster cache entries.'''
    with _cache_lock:
        _current_roster_cache.clear()


def get_cached_current_roster_snapshot(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
) -> CurrentRosterSnapshot | None:
    '''Return the runtime cached current roster snapshot for one scope.'''
    with _cache_lock:
        return _current_roster_cache.get(_cache_key(instance, data_root))


def store_current_roster_snapshot(
    snapshot: CurrentRosterSnapshot,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
) -> CurrentRosterSnapshot:
    '''Store one already sanitized current roster snapshot in runtime cache.'''
    safe_snapshot = _safe_current_roster_snapshot(snapshot)
    with _cache_lock:
        _current_roster_cache[
            _cache_key(safe_snapshot.instance, data_root)
        ] = safe_snapshot
    return safe_snapshot


def store_current_roster_snapshot_from_roster(
    roster: player_sources.CurrentPlayerRoster,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
) -> CurrentRosterSnapshot:
    '''Sanitize and store a live current roster result in runtime cache.'''
    return store_current_roster_snapshot(
        snapshot_from_roster(roster, instance=instance),
        data_root=data_root,
    )


def _persistent_cache_db_path(data_root: Path) -> Path:
    return web_db_file(data_root)


def _connect_persistent_cache_for_write(data_root: Path) -> sqlite3.Connection:
    db_path = _persistent_cache_db_path(data_root)
    try:
        ensure_web_db(db_path)
        connection = sqlite3.connect(db_path)
        connection.execute("PRAGMA foreign_keys = ON")
    except (OSError, RuntimeError, sqlite3.Error) as error:
        raise CurrentRosterPersistentCacheError(
            "Failed to open persistent current roster cache."
        ) from error
    connection.row_factory = sqlite3.Row
    return connection


def _connect_persistent_cache_for_read(data_root: Path) -> sqlite3.Connection | None:
    db_path = _persistent_cache_db_path(data_root)
    if not db_path.exists():
        return None
    try:
        ensure_web_db(db_path)
        connection = sqlite3.connect(db_path)
        connection.execute("PRAGMA foreign_keys = ON")
    except (OSError, RuntimeError, sqlite3.Error):
        return None
    connection.row_factory = sqlite3.Row
    return connection


def get_persistent_current_roster_snapshot(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
) -> CurrentRosterSnapshot | None:
    """Return the shared persistent safe current-roster snapshot, if present."""
    normalized_instance = _safe_instance(instance)
    connection = _connect_persistent_cache_for_read(data_root)
    if connection is None:
        return None
    try:
        with connection:
            row = connection.execute(
                """
                SELECT
                    instance, collected_at, updated_at, source, status, error,
                    observed_count, count_source, roster_available, roster_configured
                FROM web_current_roster_cache
                WHERE instance = ?
                """,
                (normalized_instance,),
            ).fetchone()
            if row is None:
                return None
            player_rows = connection.execute(
                """
                SELECT display_name, reliable_id, source
                FROM web_current_roster_cache_players
                WHERE instance = ?
                ORDER BY ordinal ASC
                """,
                (normalized_instance,),
            ).fetchall()
    except sqlite3.Error:
        return None
    finally:
        connection.close()

    players = tuple(
        CurrentRosterPlayerSnapshot(
            display_name=str(player_row["display_name"] or ""),
            reliable_id=str(player_row["reliable_id"] or ""),
            source=str(player_row["source"] or ""),
        )
        for player_row in player_rows
    )
    return _safe_current_roster_snapshot(
        CurrentRosterSnapshot(
            instance=str(row["instance"] or normalized_instance),
            players=players,
            source=str(row["source"] or ""),
            status=str(row["status"] or ""),
            error=str(row["error"] or ""),
            collected_at=str(row["collected_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
            observed_count=row["observed_count"],
            count_source=str(row["count_source"] or ""),
            roster_available=bool(row["roster_available"]),
            roster_configured=bool(row["roster_configured"]),
        )
    )


def store_persistent_current_roster_snapshot(
    snapshot: CurrentRosterSnapshot,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
) -> CurrentRosterSnapshot:
    """Store one sanitized current-roster snapshot in shared web.db cache."""
    safe_snapshot = _safe_current_roster_snapshot(snapshot, updated_at=_utc_now_text())
    connection = _connect_persistent_cache_for_write(data_root)
    try:
        with connection:
            connection.execute(
                """
                INSERT INTO web_current_roster_cache(
                    instance, collected_at, updated_at, source, status, error,
                    observed_count, count_source, roster_available, roster_configured
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(instance) DO UPDATE SET
                    collected_at = excluded.collected_at,
                    updated_at = excluded.updated_at,
                    source = excluded.source,
                    status = excluded.status,
                    error = excluded.error,
                    observed_count = excluded.observed_count,
                    count_source = excluded.count_source,
                    roster_available = excluded.roster_available,
                    roster_configured = excluded.roster_configured
                """,
                (
                    safe_snapshot.instance,
                    safe_snapshot.collected_at,
                    safe_snapshot.updated_at,
                    safe_snapshot.source,
                    safe_snapshot.status,
                    safe_snapshot.error,
                    safe_snapshot.observed_count,
                    safe_snapshot.count_source,
                    int(safe_snapshot.roster_available),
                    int(safe_snapshot.roster_configured),
                ),
            )
            connection.execute(
                """
                DELETE FROM web_current_roster_cache_players
                WHERE instance = ?
                """,
                (safe_snapshot.instance,),
            )
            connection.executemany(
                """
                INSERT INTO web_current_roster_cache_players(
                    instance, ordinal, display_name, reliable_id, source
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    (
                        safe_snapshot.instance,
                        index,
                        player.display_name,
                        player.reliable_id,
                        player.source,
                    )
                    for index, player in enumerate(safe_snapshot.players)
                ),
            )
    except sqlite3.Error as error:
        raise CurrentRosterPersistentCacheError(
            "Failed to store persistent current roster cache."
        ) from error
    finally:
        connection.close()
    return safe_snapshot


def store_persistent_current_roster_snapshot_from_roster(
    roster: player_sources.CurrentPlayerRoster,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
) -> CurrentRosterSnapshot:
    """Sanitize and store a live roster result in the shared cache."""
    return store_persistent_current_roster_snapshot(
        snapshot_from_roster(roster, instance=instance),
        data_root=data_root,
    )


def clear_persistent_current_roster_cache(
    instance: str | None = None,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
) -> None:
    """Clear shared current-roster cache rows for tests and maintenance."""
    connection = _connect_persistent_cache_for_read(data_root)
    if connection is None:
        return
    try:
        with connection:
            if instance is None:
                connection.execute("DELETE FROM web_current_roster_cache")
            else:
                connection.execute(
                    "DELETE FROM web_current_roster_cache WHERE instance = ?",
                    (_safe_instance(instance),),
                )
    except sqlite3.Error:
        return
    finally:
        connection.close()


def load_current_roster_snapshot(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
    max_age_seconds: int = CURRENT_ROSTER_CACHE_TTL_SECONDS,
    max_stale_age_seconds: int | None = STALE_ROSTER_UNAVAILABLE_CACHE_MAX_AGE_SECONDS,
) -> CurrentRosterSnapshotResult:
    """Return current roster from memory, persistent cache, or live fallback."""
    cached = get_cached_current_roster_snapshot(instance, data_root=data_root)
    if current_roster_snapshot_is_fresh(cached, max_age_seconds=max_age_seconds):
        assert cached is not None
        return CurrentRosterSnapshotResult(
            snapshot=cached,
            age_seconds=current_roster_snapshot_age_seconds(cached),
            is_stale=False,
            cache_status="hit",
        )

    normalized_instance = _safe_instance(instance)
    persistent = get_persistent_current_roster_snapshot(
        normalized_instance,
        data_root=data_root,
    )
    if current_roster_snapshot_is_fresh(persistent, max_age_seconds=max_age_seconds):
        assert persistent is not None
        snapshot = store_current_roster_snapshot(persistent, data_root=data_root)
        return CurrentRosterSnapshotResult(
            snapshot=snapshot,
            age_seconds=current_roster_snapshot_age_seconds(snapshot),
            is_stale=False,
            cache_status="persistent",
        )

    try:
        roster = player_sources.load_current_player_roster(normalized_instance)
    except Exception as error:  # noqa: BLE001 - GET callers need controlled fallback.
        safe_error = _safe_snapshot_text(error, max_length=240)
        stale_snapshot = persistent or cached
        if stale_snapshot is not None:
            stale_snapshot = store_current_roster_snapshot(
                stale_snapshot,
                data_root=data_root,
            )
            return CurrentRosterSnapshotResult(
                snapshot=stale_snapshot,
                age_seconds=current_roster_snapshot_age_seconds(stale_snapshot),
                is_stale=True,
                cache_status="stale_persistent" if persistent is not None else "stale",
                refresh_error=safe_error,
            )
        snapshot = store_current_roster_snapshot(
            unavailable_snapshot_from_error(error, instance=normalized_instance),
            data_root=data_root,
        )
        try:
            snapshot = store_persistent_current_roster_snapshot(
                snapshot,
                data_root=data_root,
            )
            store_current_roster_snapshot(snapshot, data_root=data_root)
        except CurrentRosterPersistentCacheError:
            pass
        return CurrentRosterSnapshotResult(
            snapshot=snapshot,
            age_seconds=current_roster_snapshot_age_seconds(snapshot),
            is_stale=False,
            cache_status="error",
        )

    snapshot = snapshot_from_roster(roster, instance=normalized_instance)
    stale_snapshot = persistent or cached
    if _should_keep_stale_roster_snapshot(
        snapshot,
        stale_snapshot,
        max_stale_age_seconds=max_stale_age_seconds,
    ):
        assert stale_snapshot is not None
        stale_snapshot = store_current_roster_snapshot(
            stale_snapshot,
            data_root=data_root,
        )
        safe_error = snapshot.error or "RCON roster unavailable; A2S reported zero players."
        return CurrentRosterSnapshotResult(
            snapshot=stale_snapshot,
            age_seconds=current_roster_snapshot_age_seconds(stale_snapshot),
            is_stale=True,
            cache_status="stale_roster_unavailable",
            refresh_error=safe_error,
        )

    snapshot = store_current_roster_snapshot(snapshot, data_root=data_root)
    try:
        snapshot = store_persistent_current_roster_snapshot(
            snapshot,
            data_root=data_root,
        )
        store_current_roster_snapshot(snapshot, data_root=data_root)
    except CurrentRosterPersistentCacheError:
        pass
    return CurrentRosterSnapshotResult(
        snapshot=snapshot,
        age_seconds=current_roster_snapshot_age_seconds(snapshot),
        is_stale=False,
        cache_status="refresh",
    )
