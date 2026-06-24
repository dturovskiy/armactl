"""Shared helpers for web page DTO loaders."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path, PureWindowsPath
from typing import Any

from armactl import discovery
from armactl.redaction import redact_sensitive_text
from armactl.state import ServerState


@dataclass(frozen=True)
class DashboardError:
    section: str
    message: str


AVAILABLE_LABEL = "available"
UNAVAILABLE_LABEL = "unavailable"
CONFIG_FILE_DISPLAY = "config.json"
INSTANCE_CONFIG_DISPLAY = "instance config"
SERVER_INSTALL_DISPLAY = "server install"
DISABLED_MODS_STATE_DISPLAY = "disabled mods state"
BOT_CONFIG_DISPLAY = "bot config"


def _to_plain(value: Any) -> Any:
    if is_dataclass(value):
        return _to_plain(asdict(value))
    if isinstance(value, dict):
        return {key: _to_plain(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_to_plain(item) for item in value]
    return value


def _plain_dict(value: Any) -> dict[str, Any]:
    plain = _to_plain(value)
    if isinstance(plain, dict):
        return plain
    return {"value": plain}


def _unavailable(message: str) -> dict[str, Any]:
    return {"available": False, "error": message}


def _safe_section(
    section: str,
    errors: list[DashboardError],
    fallback: dict[str, Any],
    func: Any,
    *args: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    try:
        return _plain_dict(func(*args, **kwargs))
    except Exception as error:  # dashboard sections degrade independently
        message = str(error) or error.__class__.__name__
        errors.append(DashboardError(section=section, message=message))
        return {**fallback, "error": message}


def _base_lifecycle(state: ServerState) -> str:
    if state.server_installed and state.config_exists:
        return "stopped"
    if state.server_installed or state.has_install_evidence():
        return "incomplete"
    return "not_installed"

def _config_dir_from_state(state: ServerState) -> Path | None:
    if not state.config_path:
        return None
    return Path(state.config_path).parent

def _bool_text(value: Any) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "unknown"


def _has_display_value(value: Any) -> bool:
    return bool(str(value or "").strip())


def _label_display(value: Any, label: str) -> str:
    if _has_display_value(value):
        return label
    return UNAVAILABLE_LABEL


def _basename_display(value: Any, fallback: str) -> str:
    if not _has_display_value(value):
        return UNAVAILABLE_LABEL

    text = str(value).strip()
    names: list[str] = []
    for candidate in (Path(text).name, PureWindowsPath(text).name):
        if not candidate or "/" in candidate or "\\" in candidate:
            continue
        if candidate not in names:
            names.append(candidate)
    if names:
        return min(names, key=len)
    return fallback


def _paths(state: ServerState) -> dict[str, Any]:
    config_dir = _config_dir_from_state(state)
    config_display = _basename_display(state.config_path, CONFIG_FILE_DISPLAY)
    instance_display = _label_display(state.instance_root, INSTANCE_CONFIG_DISPLAY)
    install_display = _label_display(state.install_dir, SERVER_INSTALL_DISPLAY)
    config_dir_display = _label_display(config_dir, INSTANCE_CONFIG_DISPLAY)
    logs_display = _label_display(config_dir, AVAILABLE_LABEL)
    return {
        "available": bool(state.instance_root or state.install_dir or state.config_path),
        "instance_root": instance_display,
        "install_dir": install_display,
        "config_path": config_display,
        "config_dir": config_dir_display,
        "logs_dir": logs_display,
        "instance_display": instance_display,
        "server_display": install_display,
        "config_display": config_display,
        "instance_root_display": instance_display,
        "install_dir_display": install_display,
        "config_path_display": config_display,
        "config_dir_display": config_dir_display,
        "logs_dir_display": logs_display,
        "service_name": state.service_name,
        "timer_name": state.timer_name,
    }


def _decorate_config(config: dict[str, Any]) -> dict[str, Any]:
    decorated = dict(config)
    decorated["visible_text"] = _bool_text(decorated.get("visible"))
    decorated["battleye_text"] = _bool_text(decorated.get("battleye"))
    return decorated

def _safe_error_message(error: Exception) -> str:
    return redact_sensitive_text(str(error) or error.__class__.__name__)


def _state_status(state: ServerState) -> dict[str, Any]:
    lifecycle = "running" if state.server_running else _base_lifecycle(state)
    return {
        "lifecycle": lifecycle,
        "installed": state.server_installed,
        "running": state.server_running,
        "label": lifecycle,
    }


def _discover_management_state(instance: str) -> tuple[ServerState | None, dict[str, Any] | None]:
    try:
        return discovery.discover(instance=instance, save=False), None
    except Exception as error:
        return None, {
            "instance": instance,
            "available": False,
            "error": _safe_error_message(error),
            "status": _unavailable("server discovery is not available"),
            "paths": _unavailable("server discovery is not available"),
        }


def _missing_config_page(instance: str, state: ServerState, message: str) -> dict[str, Any]:
    return {
        "instance": instance,
        "available": False,
        "error": message,
        "status": _state_status(state),
        "paths": _paths(state),
    }
