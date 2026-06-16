"""Compatibility helpers for restart-related pending operator work."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from armactl import paths
from armactl.web.services import pending_work

REASON_CONFIG = pending_work.KIND_CONFIG
REASON_ADMINS = pending_work.KIND_ADMINS
REASON_MODS = pending_work.KIND_MODS


@dataclass(frozen=True)
class PendingRestart:
    """One restart-related pending work item exposed in the legacy shape."""

    instance: str
    reason: str
    source_action: str
    details: str
    created_at: str
    updated_at: str
    created_by_username: str

    @property
    def reason_label(self) -> str:
        return {
            REASON_CONFIG: "Config changes",
            REASON_ADMINS: "Admin changes",
            REASON_MODS: "Mod changes",
        }.get(self.reason, "Saved changes")


def _from_pending_work(item: pending_work.PendingWorkItem) -> PendingRestart:
    return PendingRestart(
        instance=item.instance,
        reason=item.kind,
        source_action=item.source_action,
        details=item.details,
        created_at=item.created_at,
        updated_at=item.updated_at,
        created_by_username=item.created_by_username,
    )


def mark_pending_restart(
    db_path: Path,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    reason: str,
    source_action: str,
    username: str,
    details: object = "",
) -> PendingRestart:
    """Create or update restart-related pending work for one category."""
    item = pending_work.mark_restart_pending(
        db_path,
        instance=instance,
        kind=reason,
        source_action=source_action,
        username=username,
        details=details,
    )
    return _from_pending_work(item)


def get_pending_restart(
    db_path: Path,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> PendingRestart | None:
    """Return the newest restart-related pending work item, if present."""
    for item in pending_work.list_pending_work(db_path, instance=instance):
        if item.resolution_action == pending_work.RESOLUTION_RESTART_GAME_SERVER:
            return _from_pending_work(item)
    return None


def clear_pending_restart(
    db_path: Path,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> bool:
    """Clear restart-related pending work after a successful server restart."""
    return pending_work.clear_restart_pending_work(db_path, instance=instance) > 0
