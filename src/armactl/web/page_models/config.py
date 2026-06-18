"""Server config page DTO loader."""

from __future__ import annotations

from typing import Any

from armactl import config_manager, status_summary
from armactl.web.page_models.common import (
    _decorate_config,
    _discover_management_state,
    _missing_config_page,
    _paths,
    _plain_dict,
    _safe_error_message,
    _state_status,
)
from armactl.web.services.config_edit import build_config_edit_form


def load_config_page(instance: str) -> dict[str, Any]:
    """Return safe read-only config details for a web page."""
    state, error_page = _discover_management_state(instance)
    if error_page is not None:
        return error_page
    assert state is not None
    if not state.config_path:
        return _missing_config_page(instance, state, "config path is not available")

    try:
        config = config_manager.load_config(state.config_path)
        summary = _decorate_config(_plain_dict(status_summary.summarize_config(config)))
        edit_form = build_config_edit_form(config)
    except Exception as error:
        return _missing_config_page(instance, state, _safe_error_message(error))

    return {
        "instance": instance,
        "available": bool(summary.get("available")),
        "error": "",
        "status": _state_status(state),
        "paths": _paths(state),
        "config": summary,
        "edit": edit_form,
    }
