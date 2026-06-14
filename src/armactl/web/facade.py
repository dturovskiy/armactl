# Read-only dashboard facade for the future web panel.

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any

from armactl import (
    admins_manager,
    bot_config,
    config_manager,
    discovery,
    metrics,
    mods_manager,
    paths,
    player_view,
    ports,
    sat_admin_guard,
    service_manager,
    status_summary,
)
from armactl.redaction import redact_sensitive_text
from armactl.state import ServerState
from armactl.web.security.exposure import get_exposure_warning

DEFAULT_GAME_PORT = 2001
DEFAULT_A2S_PORT = 17777
DEFAULT_RCON_PORT = 19999
DASHBOARD_PLAYER_TIMEOUT_SECONDS = 0.35
DASHBOARD_ROSTER_TIMEOUT_SECONDS = 0.35


@dataclass(frozen=True)
class DashboardError:
    section: str
    message: str


@dataclass(frozen=True)
class DashboardSnapshot:
    instance: str
    lifecycle: str
    installed: bool
    running: bool
    overview: dict[str, Any]
    state: dict[str, Any]
    paths: dict[str, Any]
    service: dict[str, Any]
    timer: dict[str, Any]
    service_runtime: dict[str, Any]
    operational_status: dict[str, Any]
    config: dict[str, Any]
    mods: dict[str, Any]
    host_metrics: dict[str, Any]
    fps_metrics: dict[str, Any]
    players: dict[str, Any]
    ports: dict[str, Any]
    web: dict[str, Any]
    bot: dict[str, Any]
    sat: dict[str, Any]
    errors: tuple[DashboardError, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return _to_plain(asdict(self))


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


def _lifecycle(state: ServerState) -> str:
    if state.server_running:
        return "running"
    if state.server_installed and state.config_exists:
        return "stopped"
    if state.server_installed or state.has_install_evidence():
        return "incomplete"
    return "not_installed"


def _config_dir_from_state(state: ServerState) -> Path | None:
    if not state.config_path:
        return None
    return Path(state.config_path).parent


def _port_value(*values: int | None, default: int) -> int:
    for value in values:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return default


def _bool_text(value: Any) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "unknown"


def _format_memory_kb(value: Any) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        return metrics.format_bytes(value * 1024)
    return "Unknown"


def _format_used_total(used: Any, total: Any) -> str:
    used_value = used if isinstance(used, int) and not isinstance(used, bool) else None
    total_value = total if isinstance(total, int) and not isinstance(total, bool) else None
    if used_value is None and total_value is None:
        return "Unknown"
    return f"{metrics.format_bytes(used_value)} / {metrics.format_bytes(total_value)}"


def _overview(state: ServerState, lifecycle: str) -> dict[str, Any]:
    if lifecycle == "not_installed":
        return {
            "label": "no server",
            "empty_state": True,
            "empty_title": "No server found",
            "empty_message": (
                "Discovery did not find an installed Arma Reforger server for this "
                "instance yet."
            ),
        }
    if lifecycle == "incomplete":
        return {
            "label": "incomplete",
            "empty_state": True,
            "empty_title": "Installation incomplete",
            "empty_message": (
                "Some server evidence exists, but the install or config is incomplete."
            ),
        }
    return {
        "label": lifecycle,
        "empty_state": False,
        "empty_title": "",
        "empty_message": "",
        "install_evidence": state.has_install_evidence(),
    }


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


def _decorate_mods(mods: dict[str, Any]) -> dict[str, Any]:
    decorated = dict(mods)
    preview_labels: list[str] = []
    for item in decorated.get("preview") or []:
        if not isinstance(item, dict):
            continue
        mod_id = str(item.get("mod_id") or "").strip()
        name = str(item.get("name") or "").strip()
        if name and mod_id:
            preview_labels.append(f"{name} ({mod_id})")
        elif name or mod_id:
            preview_labels.append(name or mod_id)
    decorated["preview_labels"] = preview_labels
    return decorated


def _decorate_host_metrics(host_metrics: dict[str, Any]) -> dict[str, Any]:
    decorated = dict(host_metrics)
    decorated["cpu_text"] = metrics.format_cpu_percent(decorated.get("cpu_percent"))
    decorated["memory_text"] = _format_used_total(
        decorated.get("memory_used_bytes"),
        decorated.get("memory_total_bytes"),
    )
    decorated["disk_text"] = _format_used_total(
        decorated.get("disk_used_bytes"),
        decorated.get("disk_total_bytes"),
    )
    decorated["load_text"] = metrics.format_load_average(
        decorated.get("load_average_1m"),
        decorated.get("load_average_5m"),
        decorated.get("load_average_15m"),
    )
    decorated["uptime_text"] = metrics.format_duration(decorated.get("uptime_seconds"))
    return decorated


def _decorate_fps_metrics(fps_metrics: dict[str, Any]) -> dict[str, Any]:
    decorated = dict(fps_metrics)
    decorated["fps_text"] = metrics.format_fps(decorated.get("fps"))
    decorated["frame_avg_text"] = metrics.format_frame_time_ms(
        decorated.get("frame_avg_ms")
    )
    decorated["frame_max_text"] = metrics.format_frame_time_ms(
        decorated.get("frame_max_ms")
    )
    decorated["age_text"] = metrics.format_duration(decorated.get("age_seconds"))
    decorated["engine_memory_text"] = _format_memory_kb(
        decorated.get("engine_memory_kb")
    )
    if decorated.get("available"):
        decorated["freshness"] = "stale" if decorated.get("stale") else "fresh"
    else:
        decorated["freshness"] = "unavailable"
    return decorated


def _decorate_players(players: dict[str, Any]) -> dict[str, Any]:
    decorated = dict(players)
    current = decorated.get("current")
    max_players = decorated.get("max_players")
    if decorated.get("available"):
        decorated["count_text"] = f"{current or 0} / {max_players or '?'}"
    else:
        decorated["count_text"] = "unavailable"
    return decorated


def _decorate_service_runtime(runtime: dict[str, Any]) -> dict[str, Any]:
    decorated = dict(runtime)
    decorated["cpu_text"] = metrics.format_cpu_percent(decorated.get("cpu_percent"))
    decorated["memory_text"] = metrics.format_bytes(decorated.get("memory_rss_bytes"))
    return decorated


def _decorate_operational_status(status: dict[str, Any]) -> dict[str, Any]:
    decorated = dict(status)
    decorated["age_text"] = metrics.format_duration(decorated.get("age_seconds"))
    return decorated


def _load_summaries(
    state: ServerState,
    errors: list[DashboardError],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not state.config_path:
        message = "config path is not available"
        return _unavailable(message), _unavailable(message)

    try:
        config_summary, mods_summary = status_summary.load_status_summaries(
            state.config_path
        )
    except Exception as error:
        message = str(error) or error.__class__.__name__
        errors.append(DashboardError(section="status_summaries", message=message))
        return _unavailable(message), _unavailable(message)

    return _decorate_config(_plain_dict(config_summary)), _decorate_mods(
        _plain_dict(mods_summary)
    )


def _load_ports(
    state: ServerState,
    config_summary: dict[str, Any],
    errors: list[DashboardError],
) -> dict[str, Any]:
    if not state.server_installed:
        return _unavailable("server is not installed")

    game_port = _port_value(
        state.ports.game,
        config_summary.get("bind_port"),
        default=DEFAULT_GAME_PORT,
    )
    a2s_port = _port_value(
        state.ports.a2s,
        config_summary.get("a2s_port"),
        default=DEFAULT_A2S_PORT,
    )
    rcon_port = _port_value(
        state.ports.rcon,
        config_summary.get("rcon_port"),
        default=DEFAULT_RCON_PORT,
    )

    port_data = _safe_section(
        "ports",
        errors,
        _unavailable("port status is not available"),
        ports.check_server_ports,
        game_port=game_port,
        a2s_port=a2s_port,
        rcon_port=rcon_port,
    )
    if port_data.get("available") is not False:
        port_data = {"available": True, "ports": port_data}
    return port_data


def _load_service_runtime(
    service: dict[str, Any],
    errors: list[DashboardError],
) -> dict[str, Any]:
    if service.get("available") is False:
        return _unavailable("service status is not available")

    return _decorate_service_runtime(
        _safe_section(
            "service_runtime",
            errors,
            _unavailable("service runtime metrics are not available"),
            metrics.query_service_runtime_metrics,
            service,
        )
    )


def _load_operational_status(
    state: ServerState,
    errors: list[DashboardError],
) -> dict[str, Any]:
    config_dir = _config_dir_from_state(state)
    if config_dir is None:
        return _unavailable("config path is not available")

    return _decorate_operational_status(
        _safe_section(
            "operational_status",
            errors,
            _unavailable("server operational status is not available"),
            metrics.query_server_operational_status,
            config_dir,
        )
    )


def _load_web_runtime(web_config: Any | None) -> dict[str, Any]:
    if web_config is None:
        return _unavailable("web runtime config is not available")

    https_required = bool(web_config.https_required)
    warning = get_exposure_warning(web_config.bind_host, https_required)
    return {
        "available": True,
        "data_root": str(web_config.data_root),
        "runtime_dir": str(web_config.runtime_dir),
        "env_path": str(web_config.env_path),
        "db_path": str(web_config.db_path),
        "audit_log_path": str(web_config.audit_log_path),
        "bind_host": web_config.bind_host,
        "bind_port": web_config.bind_port,
        "https_required": https_required,
        "https_required_text": _bool_text(https_required),
        "exposure_warning": warning.to_dict() if warning is not None else None,
    }


def _load_bot_summary(instance: str, errors: list[DashboardError]) -> dict[str, Any]:
    try:
        config = bot_config.load_bot_config(instance)
    except Exception as error:
        message = str(error) or error.__class__.__name__
        errors.append(DashboardError(section="bot", message=message))
        return _unavailable("Telegram bot status is not available")

    summary: dict[str, Any] = {
        "available": True,
        "enabled": bool(config.enabled),
        "token_configured": bool(config.token.strip()),
        "admin_chat_count": len(config.admin_chat_ids),
        "language": config.language,
        "env_path": str(config.env_path) if config.env_path else "",
        "service": _unavailable("Telegram bot service is not installed"),
    }

    try:
        service_file = paths.bot_service_file()
    except Exception as error:
        message = str(error) or error.__class__.__name__
        errors.append(DashboardError(section="bot_service", message=message))
        return summary

    if not service_file.exists():
        return summary

    try:
        from armactl import bot_manager

        service_status = bot_manager.get_bot_service_status()
    except Exception as error:
        message = str(error) or error.__class__.__name__
        errors.append(DashboardError(section="bot_service", message=message))
        summary["service"] = {
            **_unavailable("Telegram bot service status is not available"),
            "error": message,
        }
        return summary

    runtime = service_status.get("runtime")
    if not isinstance(runtime, dict):
        runtime = {}
    summary["service"] = {
        "available": True,
        "service_name": service_status.get("service_name", ""),
        "service_file": service_status.get("service_file", ""),
        "installed": bool(service_status.get("installed")),
        "active": bool(service_status.get("active")),
        "enabled": bool(service_status.get("enabled")),
        "active_state": service_status.get("active_state", "unknown"),
        "main_pid": service_status.get("main_pid", 0),
        "runtime_ready": bool(runtime.get("success")) if runtime else None,
    }
    return summary


def _load_sat_summary(state: ServerState, errors: list[DashboardError]) -> dict[str, Any]:
    if not state.config_path:
        return _unavailable("config path is not available")

    try:
        summary = sat_admin_guard.inspect_sat_admin_config(state.config_path).to_dict()
    except Exception as error:
        message = str(error) or error.__class__.__name__
        errors.append(DashboardError(section="sat_admin_guard", message=message))
        return _unavailable("ServerAdminTools admin status is not available")

    return {"available": bool(summary.get("available")), **summary}



def _safe_error_message(error: Exception) -> str:
    return redact_sensitive_text(str(error) or error.__class__.__name__)


def _state_status(state: ServerState) -> dict[str, Any]:
    lifecycle = _lifecycle(state)
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
    except Exception as error:
        return _missing_config_page(instance, state, _safe_error_message(error))

    return {
        "instance": instance,
        "available": bool(summary.get("available")),
        "error": "",
        "status": _state_status(state),
        "paths": _paths(state),
        "config": summary,
    }


def _mod_entry(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {"mod_id": str(raw or "").strip(), "name": "", "version": ""}
    return {
        "mod_id": str(raw.get("modId") or raw.get("mod_id") or "").strip(),
        "name": str(raw.get("name") or "").strip(),
        "version": str(raw.get("version") or "").strip(),
    }


def load_mods_page(instance: str) -> dict[str, Any]:
    """Return active mod list details without mutating config or sidecars."""
    state, error_page = _discover_management_state(instance)
    if error_page is not None:
        return error_page
    assert state is not None
    if not state.config_path:
        return _missing_config_page(instance, state, "config path is not available")

    try:
        raw_mods = mods_manager.get_mods(state.config_path)
        if not isinstance(raw_mods, list):
            raw_mods = []
        mods = [_mod_entry(raw) for raw in raw_mods]
    except Exception as error:
        return _missing_config_page(instance, state, _safe_error_message(error))

    return {
        "instance": instance,
        "available": True,
        "error": "",
        "status": _state_status(state),
        "paths": _paths(state),
        "count": len(mods),
        "mods": mods,
    }


def _admin_identity(raw: Any) -> dict[str, str]:
    if isinstance(raw, dict):
        identity = (
            raw.get("identityId")
            or raw.get("steamId64")
            or raw.get("steamid")
            or raw.get("playerId")
            or raw.get("uid")
            or raw.get("id")
            or ""
        )
        return {
            "identity_id": str(identity or "").strip(),
            "name": str(raw.get("name") or raw.get("displayName") or "").strip(),
            "source": str(raw.get("source") or "game.admins").strip(),
        }
    return {
        "identity_id": str(raw or "").strip(),
        "name": "",
        "source": "game.admins",
    }


def _admin_key(entry: dict[str, str]) -> str:
    return entry.get("identity_id", "").upper()


def load_admins_page(instance: str) -> dict[str, Any]:
    """Return official game admin IDs plus optional local labels without migration."""
    state, error_page = _discover_management_state(instance)
    if error_page is not None:
        return error_page
    assert state is not None
    if not state.config_path:
        return _missing_config_page(instance, state, "config path is not available")

    try:
        config = config_manager.load_config(state.config_path)
        game = config.get("game", {}) if isinstance(config.get("game"), dict) else {}
        raw_admins = game.get("admins", [])
        if raw_admins in (None, ""):
            raw_admins = []
        if not isinstance(raw_admins, list):
            raise ValueError("game.admins must be a list")
        official = [_admin_identity(item) for item in raw_admins]
    except Exception as error:
        return _missing_config_page(instance, state, _safe_error_message(error))

    sidecar_path = ""
    local_labels: list[dict[str, str]] = []
    label_error = ""
    try:
        sidecar_path = str(admins_manager.admins_state_path_for_config(state.config_path))
        local_labels = [
            {
                "identity_id": str(item.get("identityId") or "").strip(),
                "name": str(item.get("name") or "").strip(),
                "source": str(item.get("source") or "").strip(),
            }
            for item in admins_manager.load_admins(state.config_path)
        ]
    except Exception as error:
        label_error = _safe_error_message(error)

    labels_by_key = {_admin_key(item): item for item in local_labels if _admin_key(item)}
    enriched: list[dict[str, str]] = []
    for entry in official:
        label = labels_by_key.get(_admin_key(entry))
        if label is not None:
            entry = {
                **entry,
                "name": label.get("name") or entry.get("name", ""),
                "source": label.get("source") or entry.get("source", ""),
            }
        enriched.append(entry)

    return {
        "instance": instance,
        "available": True,
        "error": "",
        "status": _state_status(state),
        "paths": _paths(state),
        "official_admins": enriched,
        "official_count": len(enriched),
        "local_labels": local_labels,
        "local_label_count": len(local_labels),
        "local_labels_path": sidecar_path,
        "local_labels_error": label_error,
    }


def load_bot_page(instance: str) -> dict[str, Any]:
    """Return safe read-only Telegram bot status without exposing token values."""
    errors: list[DashboardError] = []
    summary = _load_bot_summary(instance, errors)
    return {
        "instance": instance,
        "available": bool(summary.get("available")),
        "error": summary.get("error", ""),
        "bot": summary,
        "errors": [
            {"section": error.section, "message": redact_sensitive_text(error.message)}
            for error in errors
        ],
    }

def load_dashboard_snapshot(
    instance: str,
    *,
    web_config: Any | None = None,
) -> dict[str, Any]:
    errors: list[DashboardError] = []
    state = discovery.discover(instance=instance, save=False)
    state_dict = state.to_dict()
    lifecycle = _lifecycle(state)

    if state.server_installed or state.service_exists:
        service = _safe_section(
            "service",
            errors,
            _unavailable("service status is not available"),
            service_manager.get_service_status,
            state.service_name,
        )
        if service.get("available") is not False:
            service = {"available": True, **service}
    else:
        service = _unavailable("server service is not installed")

    if state.server_installed or state.timer_exists:
        timer = _safe_section(
            "timer",
            errors,
            _unavailable("timer status is not available"),
            service_manager.get_timer_status,
            state.timer_name,
        )
        if timer.get("available") is not False:
            timer = {"available": True, **timer}
    else:
        timer = _unavailable("restart timer is not installed")

    config_summary, mods_summary = _load_summaries(state, errors)

    host_metrics = _decorate_host_metrics(
        _safe_section(
            "host_metrics",
            errors,
            _unavailable("host metrics are not available"),
            metrics.query_host_metrics,
        )
    )

    config_dir = _config_dir_from_state(state)
    if config_dir is None:
        fps_metrics = _decorate_fps_metrics(_unavailable("config path is not available"))
    else:
        fps_metrics = _decorate_fps_metrics(
            _safe_section(
                "fps_metrics",
                errors,
                _unavailable("server FPS metrics are not available"),
                metrics.query_server_fps_metrics,
                config_dir,
            )
        )

    if state.server_running:
        players = _decorate_players(
            _safe_section(
                "players",
                errors,
                _unavailable("player view is not available"),
                player_view.query_player_view,
                instance,
                timeout=DASHBOARD_PLAYER_TIMEOUT_SECONDS,
                roster_timeout=DASHBOARD_ROSTER_TIMEOUT_SECONDS,
                state=state,
                include_roster=False,
            )
        )
    else:
        players = _decorate_players(_unavailable("server is not running"))

    snapshot = DashboardSnapshot(
        instance=instance,
        lifecycle=lifecycle,
        installed=state.server_installed,
        running=state.server_running,
        overview=_overview(state, lifecycle),
        state=state_dict,
        paths=_paths(state),
        service=service,
        timer=timer,
        service_runtime=_load_service_runtime(service, errors),
        operational_status=_load_operational_status(state, errors),
        config=config_summary,
        mods=mods_summary,
        host_metrics=host_metrics,
        fps_metrics=fps_metrics,
        players=players,
        ports=_load_ports(state, config_summary, errors),
        web=_load_web_runtime(web_config),
        bot=_load_bot_summary(instance, errors),
        sat=_load_sat_summary(state, errors),
        errors=tuple(errors),
    )
    return snapshot.to_dict()
