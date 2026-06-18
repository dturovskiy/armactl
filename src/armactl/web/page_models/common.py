"""Shared helpers for web page DTO loaders."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any

from armactl import discovery
from armactl.redaction import redact_sensitive_text
from armactl.state import ServerState


@dataclass(frozen=True)
class DashboardError:
    section: str
    message: str

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

def _paths(state: ServerState) -> dict[str, Any]:
    config_dir = _config_dir_from_state(state)
    return {
        "available": bool(state.instance_root or state.install_dir or state.config_path),
        "instance_root": state.instance_root,
        "install_dir": state.install_dir,
        "config_path": state.config_path,
        "config_dir": str(config_dir) if config_dir is not None else "",
        "logs_dir": str(config_dir / "logs") if config_dir is not None else "",
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
