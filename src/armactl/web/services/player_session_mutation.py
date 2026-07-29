"""Shared typed player-session mutation services and process lock."""

from __future__ import annotations

import fcntl
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from armactl import paths
from armactl.web.services import (
    player_live_session_scanner,
    player_registry,
    player_sessionizer,
)

PLAYER_SESSION_MUTATION_LOCK_FILENAME = ".player-session-mutation.lock"
DEFAULT_PLAYER_SESSION_STALE_TIMEOUT = timedelta(hours=24)
DEFAULT_CLOSED_PLAYER_SESSION_RETENTION = timedelta(days=90)


class PlayerSessionMutationBusyError(RuntimeError):
    """Another automatic or manual session mutation owns the instance lock."""

    code = "db_contention"


@dataclass(frozen=True)
class PlayerSessionMaintenanceSummary:
    """Counts-only summary of stale close and retention cleanup."""

    stale_close: player_registry.PlayerSessionStaleCloseResult
    retention_cleanup: player_registry.PlayerSessionRetentionCleanupResult


def player_session_mutation_lock_path(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
) -> Path:
    normalized = paths.validate_instance_name(instance)
    return paths.instance_root(normalized, data_root) / PLAYER_SESSION_MUTATION_LOCK_FILENAME


def read_player_session_mutation_lock_state(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
) -> str:
    """Return idle, busy, or unknown without creating the lock path."""
    lock_path = player_session_mutation_lock_path(instance, data_root=data_root)
    try:
        if not lock_path.is_file():
            return "idle"
        descriptor = os.open(lock_path, os.O_RDONLY)
    except OSError:
        return "unknown"
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "busy"
        except OSError:
            return "unknown"
        try:
            return "idle"
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


@contextmanager
def acquire_player_session_mutation_lock(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
) -> Iterator[None]:
    """Acquire the one nonblocking process lock shared by all session mutations."""
    lock_path = player_session_mutation_lock_path(instance, data_root=data_root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise PlayerSessionMutationBusyError(
                "Player session mutation is already running."
            ) from error
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def run_player_log_sessionization(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
    event_id_floor: int = 0,
    after_event_time: str = "",
    after_event_id: int = 0,
    through_event_id: int | None = None,
    page_limit: int = player_registry.DEFAULT_PLAYER_SESSIONIZATION_EVENT_PAGE_LIMIT,
    max_pages: int = player_sessionizer.DEFAULT_SESSIONIZATION_MAX_PAGES,
    progress_callback: (
        Callable[[player_sessionizer.PlayerLogSessionizationSummary], None] | None
    ) = None,
    acquire_lock: bool = True,
) -> player_sessionizer.PlayerLogSessionizationSummary:
    """Run the existing bounded sessionizer through the shared mutation service."""
    normalized = paths.validate_instance_name(instance)
    db_path = player_registry.player_registry_db_path(normalized, data_root=data_root)

    def execute() -> player_sessionizer.PlayerLogSessionizationSummary:
        return player_sessionizer.sessionize_stored_player_log_events(
            db_path,
            event_id_floor=event_id_floor,
            after_event_time=after_event_time,
            after_event_id=after_event_id,
            through_event_id=through_event_id,
            page_limit=page_limit,
            max_pages=max_pages,
            progress_callback=progress_callback,
        )

    if not acquire_lock:
        return execute()
    with acquire_player_session_mutation_lock(normalized, data_root=data_root):
        return execute()


def run_live_player_session_scan(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
    observed_at: str | None = None,
    absence_confirmation_scans: int = (
        player_live_session_scanner.LIVE_SESSION_ABSENCE_CONFIRMATION_SCANS
    ),
    acquire_lock: bool = True,
) -> player_live_session_scanner.LivePlayerSessionScanSummary:
    """Run one live scan through the same mutation lock as automatic execution."""
    normalized = paths.validate_instance_name(instance)

    def execute() -> player_live_session_scanner.LivePlayerSessionScanSummary:
        return player_live_session_scanner.scan_live_player_sessions_once(
            normalized,
            data_root=data_root,
            observed_at=observed_at,
            absence_confirmation_scans=absence_confirmation_scans,
        )

    if not acquire_lock:
        return execute()
    with acquire_player_session_mutation_lock(normalized, data_root=data_root):
        return execute()


def run_player_session_maintenance(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
    now: datetime | None = None,
    acquire_lock: bool = True,
) -> PlayerSessionMaintenanceSummary:
    """Run stale close and retention cleanup through the shared mutation lock."""
    normalized = paths.validate_instance_name(instance)
    db_path = player_registry.player_registry_db_path(normalized, data_root=data_root)
    current = now or datetime.now(timezone.utc)

    def execute() -> PlayerSessionMaintenanceSummary:
        close_observed_at = current.isoformat()
        stale_cutoff = (current - DEFAULT_PLAYER_SESSION_STALE_TIMEOUT).isoformat()
        retention_cutoff = (
            current - DEFAULT_CLOSED_PLAYER_SESSION_RETENTION
        ).isoformat()
        stale_close = player_registry.close_stale_open_player_sessions(
            db_path,
            last_seen_before=stale_cutoff,
            close_observed_at=close_observed_at,
            source=player_registry.PLAYER_SESSION_SOURCE_SCANNER_CHECKPOINT,
            source_ref="session-maintenance:stale-timeout",
            confidence=player_registry.PLAYER_SESSION_CONFIDENCE_LOW,
        )
        retention_cleanup = player_registry.cleanup_player_sessions_by_retention(
            db_path,
            closed_before=retention_cutoff,
        )
        return PlayerSessionMaintenanceSummary(
            stale_close=stale_close,
            retention_cleanup=retention_cleanup,
        )

    if not acquire_lock:
        return execute()
    with acquire_player_session_mutation_lock(normalized, data_root=data_root):
        return execute()
