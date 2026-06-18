"""Compatibility re-exports for legacy web facade imports."""

from __future__ import annotations

from armactl.web.page_models.admins import load_admins_page
from armactl.web.page_models.bot import load_bot_page
from armactl.web.page_models.common import DashboardError
from armactl.web.page_models.config import load_config_page
from armactl.web.page_models.dashboard import (
    DASHBOARD_PLAYER_TIMEOUT_SECONDS,
    DASHBOARD_ROSTER_TIMEOUT_SECONDS,
    DashboardSnapshot,
    load_dashboard_snapshot,
)
from armactl.web.page_models.mods import load_mods_page
from armactl.web.page_models.schedule import load_schedule_page

__all__ = [
    "DASHBOARD_PLAYER_TIMEOUT_SECONDS",
    "DASHBOARD_ROSTER_TIMEOUT_SECONDS",
    "DashboardError",
    "DashboardSnapshot",
    "load_admins_page",
    "load_bot_page",
    "load_config_page",
    "load_dashboard_snapshot",
    "load_mods_page",
    "load_schedule_page",
]
