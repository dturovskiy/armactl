"""Restart schedule page DTO loader."""

from __future__ import annotations

from typing import Any

from armactl import paths
from armactl.platform.service_adapter import ServiceAdapter, get_service_adapter
from armactl.state import ServerState
from armactl.web.page_models.common import (
    _discover_management_state,
    _paths,
    _plain_dict,
    _state_status,
    _unavailable,
)


def _resolve_service_adapter(adapter: ServiceAdapter | None) -> ServiceAdapter:
    return adapter if adapter is not None else get_service_adapter()


def _timer_status_for_page(state: ServerState, adapter: ServiceAdapter) -> dict[str, Any]:
    timer_name = state.timer_name or adapter.timer_unit_name(paths.DEFAULT_INSTANCE_NAME)
    if state.server_installed or state.timer_exists:
        timer = _plain_dict(adapter.get_timer_status(timer_name))
    else:
        timer = _unavailable("restart timer is not installed")

    timer = {
        "available": timer.get("available", True) is not False,
        "timer_name": timer.get("timer_name") or timer_name,
        "exists": bool(timer.get("exists", state.timer_exists)),
        "active": bool(timer.get("active")),
        "enabled": bool(timer.get("enabled")),
        "active_state": str(timer.get("active_state") or "unknown"),
        "sub_state": str(timer.get("sub_state") or "unknown"),
        "unit_file_state": str(timer.get("unit_file_state") or "unknown"),
        "description": str(timer.get("description") or ""),
        "schedule": str(timer.get("schedule") or ""),
        "schedule_entries": list(timer.get("schedule_entries") or []),
        "next_run": str(timer.get("next_run") or ""),
        "last_trigger": str(timer.get("last_trigger") or ""),
        "error": str(timer.get("error") or ""),
    }
    timer["schedule_display"] = timer["schedule"] or "unknown"
    return timer


def _service_policy_for_page(state: ServerState, adapter: ServiceAdapter) -> dict[str, Any]:
    service_name = state.service_name or adapter.service_unit_name(paths.DEFAULT_INSTANCE_NAME)
    if state.server_installed or state.service_exists:
        service = _plain_dict(adapter.get_service_status(service_name))
        available = service.get("available", True) is not False
    else:
        service = _unavailable("server service is not installed")
        available = False

    enabled = service.get("enabled") if available else None
    if enabled is True:
        boot_policy = "enabled"
    elif enabled is False:
        boot_policy = "disabled"
    else:
        boot_policy = "unknown"

    warning = bool(available and enabled is False)
    return {
        "available": available,
        "service_name": service.get("service_name") or service_name,
        "exists": bool(state.service_exists or state.server_installed),
        "active": bool(service.get("active")),
        "enabled": enabled,
        "boot_policy": boot_policy,
        "active_state": str(service.get("active_state") or "unknown"),
        "sub_state": str(service.get("sub_state") or "unknown"),
        "description": str(service.get("description") or ""),
        "warning": warning,
        "warning_message": (
            "Game service is disabled; scheduled restarts will not guarantee "
            "boot-start after host reboot."
            if warning
            else ""
        ),
        "error": str(service.get("error") or ""),
    }


def load_schedule_page(
    instance: str,
    *,
    adapter: ServiceAdapter | None = None,
) -> dict[str, Any]:
    """Return restart timer state and game service boot policy for the web page."""
    service_adapter = _resolve_service_adapter(adapter)
    state, error_page = _discover_management_state(instance)
    if error_page is not None:
        return error_page
    assert state is not None
    return {
        "instance": instance,
        "available": True,
        "error": "",
        "status": _state_status(state),
        "paths": _paths(state),
        "timer": _timer_status_for_page(state, service_adapter),
        "service_policy": _service_policy_for_page(state, service_adapter),
    }
