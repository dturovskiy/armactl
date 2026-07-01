'''Runtime cache for sanitized current-player roster snapshots.'''

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from armactl import paths
from armactl.web.services import player_sources
from armactl.web.services.player_identity import (
    normalize_reliable_player_id,
    safe_player_text,
)

CURRENT_ROSTER_CACHE_TTL_SECONDS = 10
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
    '''Sanitized current-player roster snapshot held only in process memory.'''

    instance: str
    players: tuple[CurrentRosterPlayerSnapshot, ...]
    source: str
    status: str
    error: str
    collected_at: str

    @property
    def available(self) -> bool:
        return self.status == 'available'

    @property
    def total_count(self) -> int:
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
    return CurrentRosterSnapshot(
        instance=_safe_instance(instance),
        players=players,
        source=_safe_snapshot_text(roster.source, max_length=80, default=_SOURCE_UNAVAILABLE),
        status=_safe_snapshot_text(roster.status, max_length=80, default=status_default),
        error=_safe_snapshot_text(roster.error, max_length=240),
        collected_at=collected_at or _utc_now_text(),
    )


def unavailable_snapshot_from_error(
    error: object,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    collected_at: str | None = None,
) -> CurrentRosterSnapshot:
    '''Build a cache-safe unavailable snapshot for controlled source failures.'''
    return CurrentRosterSnapshot(
        instance=_safe_instance(instance),
        players=(),
        source=_SOURCE_UNAVAILABLE,
        status=_STATUS_UNAVAILABLE,
        error=_safe_snapshot_text(error, max_length=240),
        collected_at=collected_at or _utc_now_text(),
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
    safe_snapshot = CurrentRosterSnapshot(
        instance=_safe_instance(snapshot.instance),
        players=tuple(snapshot.players),
        source=_safe_snapshot_text(snapshot.source, max_length=80, default=_SOURCE_UNAVAILABLE),
        status=_safe_snapshot_text(snapshot.status, max_length=80, default=_STATUS_UNKNOWN),
        error=_safe_snapshot_text(snapshot.error, max_length=240),
        collected_at=(
            safe_player_text(snapshot.collected_at, max_length=80) or _utc_now_text()
        ),
    )
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


def load_current_roster_snapshot(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
    max_age_seconds: int = CURRENT_ROSTER_CACHE_TTL_SECONDS,
) -> CurrentRosterSnapshotResult:
    '''Return current roster from fresh cache or a bounded live source fallback.'''
    cached = get_cached_current_roster_snapshot(instance, data_root=data_root)
    if current_roster_snapshot_is_fresh(cached, max_age_seconds=max_age_seconds):
        assert cached is not None
        return CurrentRosterSnapshotResult(
            snapshot=cached,
            age_seconds=current_roster_snapshot_age_seconds(cached),
            is_stale=False,
            cache_status='hit',
        )

    normalized_instance = _safe_instance(instance)
    try:
        roster = player_sources.load_current_player_roster(normalized_instance)
    except Exception as error:  # noqa: BLE001 - GET callers need controlled fallback.
        safe_error = _safe_snapshot_text(error, max_length=240)
        if cached is not None:
            return CurrentRosterSnapshotResult(
                snapshot=cached,
                age_seconds=current_roster_snapshot_age_seconds(cached),
                is_stale=True,
                cache_status='stale',
                refresh_error=safe_error,
            )
        snapshot = store_current_roster_snapshot(
            unavailable_snapshot_from_error(error, instance=normalized_instance),
            data_root=data_root,
        )
        return CurrentRosterSnapshotResult(
            snapshot=snapshot,
            age_seconds=current_roster_snapshot_age_seconds(snapshot),
            is_stale=False,
            cache_status='error',
        )

    snapshot = store_current_roster_snapshot_from_roster(
        roster,
        instance=normalized_instance,
        data_root=data_root,
    )
    return CurrentRosterSnapshotResult(
        snapshot=snapshot,
        age_seconds=current_roster_snapshot_age_seconds(snapshot),
        is_stale=False,
        cache_status='refresh',
    )
