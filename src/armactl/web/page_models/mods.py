"""Workshop mods page DTO loader."""

from __future__ import annotations

from typing import Any

from armactl import mods_manager, mods_state
from armactl.web.page_models.common import (
    DISABLED_MODS_STATE_DISPLAY,
    UNAVAILABLE_LABEL,
    _discover_management_state,
    _label_display,
    _missing_config_page,
    _paths,
    _safe_error_message,
    _state_status,
)


def _mod_entry(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {"mod_id": str(raw or "").strip(), "name": "", "version": ""}
    return {
        "mod_id": str(raw.get("modId") or raw.get("mod_id") or "").strip(),
        "name": str(raw.get("name") or "").strip(),
        "version": str(raw.get("version") or "").strip(),
    }


def load_mods_page(instance: str) -> dict[str, Any]:
    """Return active and disabled mod list details for a web page."""
    state, error_page = _discover_management_state(instance)
    if error_page is not None:
        return error_page
    assert state is not None
    if not state.config_path:
        return _missing_config_page(instance, state, "config path is not available")

    disabled_mods_state = UNAVAILABLE_LABEL
    disabled_mods: list[dict[str, str]] = []
    disabled_mods_error = ""
    try:
        raw_mods = mods_manager.get_mods(state.config_path)
        if not isinstance(raw_mods, list):
            raw_mods = []
        mods = [_mod_entry(raw) for raw in raw_mods]
    except Exception as error:
        return _missing_config_page(instance, state, _safe_error_message(error))

    try:
        state_path = mods_state.mods_state_path_for_config(state.config_path)
        raw_disabled_mods = mods_state.load_disabled_mods(state.config_path)
        disabled_mods_state = _label_display(state_path, DISABLED_MODS_STATE_DISPLAY)
        if not isinstance(raw_disabled_mods, list):
            raw_disabled_mods = []
        disabled_mods = [_mod_entry(raw) for raw in raw_disabled_mods]
    except Exception as error:
        disabled_mods_error = _safe_error_message(error)

    return {
        "instance": instance,
        "available": True,
        "error": "",
        "status": _state_status(state),
        "paths": _paths(state),
        "count": len(mods),
        "mods": mods,
        "disabled_count": len(disabled_mods),
        "disabled_mods": disabled_mods,
        "disabled_mods_state": disabled_mods_state,
        "disabled_mods_state_display": disabled_mods_state,
        "disabled_mods_error": disabled_mods_error,
    }
