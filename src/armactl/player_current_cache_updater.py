"""Foreground updater for the shared safe current-player roster cache."""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from armactl import paths
from armactl.redaction import redact_sensitive_text
from armactl.web.services import player_current_cache, player_sources

DEFAULT_INTERVAL_SECONDS = 10
MIN_INTERVAL_SECONDS = 10
MAX_INTERVAL_SECONDS = 3600


@dataclass(frozen=True)
class CurrentRosterCacheUpdaterResult:
    """Controlled outcome for one current-roster cache update."""

    success: bool
    message: str
    instance: str
    total_count: int = 0
    source: str = ""
    status: str = ""
    cache_status: str = ""
    exit_code: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "message": self.message,
            "instance": self.instance,
            "total_count": self.total_count,
            "source": self.source,
            "status": self.status,
            "cache_status": self.cache_status,
            "exit_code": self.exit_code,
        }


def _safe_update_error_detail(error: object, *, max_length: int = 180) -> str:
    detail = " ".join(redact_sensitive_text(error).split())
    if not detail:
        detail = error.__class__.__name__
    if len(detail) > max_length:
        return f"{detail[: max_length - 3]}..."
    return detail


def _normalize_instance(instance: str) -> str:
    try:
        return paths.validate_instance_name(instance or paths.DEFAULT_INSTANCE_NAME)
    except paths.InvalidInstanceNameError:
        return paths.DEFAULT_INSTANCE_NAME


def _normalize_interval_seconds(value: int) -> int:
    try:
        interval = int(value)
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_SECONDS
    return min(max(interval, MIN_INTERVAL_SECONDS), MAX_INTERVAL_SECONDS)


def refresh_current_roster_cache_once(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
) -> CurrentRosterCacheUpdaterResult:
    """Refresh only the shared safe current-roster cache once."""
    normalized_instance = _normalize_instance(instance)
    try:
        roster = player_sources.load_current_player_roster(normalized_instance)
    except Exception as error:  # noqa: BLE001 - loop reports safe warnings only.
        detail = _safe_update_error_detail(error)
        snapshot = player_current_cache.get_persistent_current_roster_snapshot(
            normalized_instance,
            data_root=data_root,
        )
        if snapshot is None:
            snapshot = player_current_cache.unavailable_snapshot_from_error(
                error,
                instance=normalized_instance,
            )
            player_current_cache.store_current_roster_snapshot(
                snapshot,
                data_root=data_root,
            )
            try:
                player_current_cache.store_persistent_current_roster_snapshot(
                    snapshot,
                    data_root=data_root,
                )
            except player_current_cache.CurrentRosterPersistentCacheError as store_error:
                detail = _safe_update_error_detail(store_error)
        return CurrentRosterCacheUpdaterResult(
            False,
            f"Current roster cache refresh failed: {detail}",
            normalized_instance,
            source="unavailable",
            status="unavailable",
            cache_status="error",
            exit_code=1,
        )

    snapshot = player_current_cache.store_current_roster_snapshot_from_roster(
        roster,
        instance=normalized_instance,
        data_root=data_root,
    )
    try:
        snapshot = player_current_cache.store_persistent_current_roster_snapshot(
            snapshot,
            data_root=data_root,
        )
        player_current_cache.store_current_roster_snapshot(snapshot, data_root=data_root)
    except player_current_cache.CurrentRosterPersistentCacheError as error:
        detail = _safe_update_error_detail(error)
        return CurrentRosterCacheUpdaterResult(
            False,
            f"Current roster cache write failed: {detail}",
            normalized_instance,
            total_count=snapshot.total_count,
            source=snapshot.source,
            status=snapshot.status,
            cache_status="write_error",
            exit_code=1,
        )

    return CurrentRosterCacheUpdaterResult(
        True,
        (
            "Current roster cache updated: "
            f"{snapshot.total_count} player(s), source={snapshot.source}, "
            f"status={snapshot.status}."
        ),
        normalized_instance,
        total_count=snapshot.total_count,
        source=snapshot.source,
        status=snapshot.status,
        cache_status="updated",
    )


def _print_current_roster_cache_warning(
    message: object,
    *,
    retry_seconds: int | None,
) -> None:
    detail = _safe_update_error_detail(message)
    action = "exiting" if retry_seconds is None else f"retrying in {retry_seconds}s"
    print(
        f"Current roster cache updater warning: {detail}; {action}.",
        file=sys.stderr,
        flush=True,
    )


def run_current_roster_cache_updater(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    once: bool = False,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
) -> CurrentRosterCacheUpdaterResult | None:
    """Run the current-roster cache updater loop."""
    interval = _normalize_interval_seconds(interval_seconds)
    while True:
        result = refresh_current_roster_cache_once(instance, data_root=data_root)
        if result.success:
            print(result.message, flush=True)
            if once:
                return result
        else:
            _print_current_roster_cache_warning(
                result.message,
                retry_seconds=None if once else interval,
            )
            if once:
                return result
        time.sleep(interval)
