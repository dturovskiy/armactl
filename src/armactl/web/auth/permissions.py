"""Code-level web permission categories."""

from __future__ import annotations

from dataclasses import dataclass

from armactl.web.auth.models import UserRecord

DASHBOARD_VIEW = "dashboard:view"
ACTIONS_RUN = "actions:run"
FILES_READ = "files:read"
FILES_WRITE = "files:write"
BACKUPS_MANAGE = "backups:manage"
USERS_MANAGE = "users:manage"
SETTINGS_MANAGE = "settings:manage"
LOGS_VIEW = "logs:view"
JOBS_VIEW = "jobs:view"

ALL_PERMISSIONS = frozenset(
    {
        DASHBOARD_VIEW,
        ACTIONS_RUN,
        FILES_READ,
        FILES_WRITE,
        BACKUPS_MANAGE,
        USERS_MANAGE,
        SETTINGS_MANAGE,
        LOGS_VIEW,
        JOBS_VIEW,
    }
)

OWNER_ROLE = "owner"
ROLE_PERMISSIONS = {
    OWNER_ROLE: ALL_PERMISSIONS,
}


@dataclass(frozen=True)
class PermissionCheck:
    """Result of checking a web permission."""

    allowed: bool
    permission: str


def user_has_permission(user: UserRecord | None, permission: str) -> bool:
    """Return whether a user has a declared web permission."""
    if user is None or not user.is_active:
        return False
    if permission not in ALL_PERMISSIONS:
        return False
    return permission in ROLE_PERMISSIONS.get(user.role, frozenset())


def check_permission(user: UserRecord | None, permission: str) -> PermissionCheck:
    """Return a structured permission check result for route helpers."""
    return PermissionCheck(
        allowed=user_has_permission(user, permission),
        permission=permission,
    )
