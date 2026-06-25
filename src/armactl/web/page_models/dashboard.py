"""Dashboard snapshot DTO loader."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from armactl import (
    discovery,
    metrics,
    player_view,
    ports,
    sat_admin_guard,
    status_summary,
)
from armactl.platform.service_adapter import ServiceAdapter, get_service_adapter
from armactl.state import ServerState
from armactl.web.page_models.bot import load_bot_summary
from armactl.web.page_models.common import (
    DashboardError,
    _base_lifecycle,
    _bool_text,
    _config_dir_from_state,
    _decorate_config,
    _paths,
    _plain_dict,
    _safe_section,
    _to_plain,
    _unavailable,
)
from armactl.web.security.exposure import get_exposure_warning
from armactl.web.services import server_versions

DEFAULT_GAME_PORT = 2001
DEFAULT_A2S_PORT = 17777
DEFAULT_RCON_PORT = 19999
DASHBOARD_PLAYER_TIMEOUT_SECONDS = 0.35
DASHBOARD_ROSTER_TIMEOUT_SECONDS = 0.35


def _resolve_service_adapter(adapter: ServiceAdapter | None) -> ServiceAdapter:
    return adapter if adapter is not None else get_service_adapter()


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
    server_version: dict[str, Any]
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

def _service_looks_active_or_starting(service: dict[str, Any]) -> bool:
    active_state = str(service.get("active_state") or "").strip().lower()
    sub_state = str(service.get("sub_state") or "").strip().lower()
    return active_state in {"active", "activating"} or sub_state in {
        "running",
        "start",
        "auto-restart",
    }


def _service_is_activating(service: dict[str, Any]) -> bool:
    active_state = str(service.get("active_state") or "").strip().lower()
    sub_state = str(service.get("sub_state") or "").strip().lower()
    return active_state == "activating" or sub_state in {"start", "auto-restart"}


def _dashboard_lifecycle(
    state: ServerState,
    service: dict[str, Any],
) -> str:
    base = _base_lifecycle(state)
    if base != "stopped":
        return base
    if not state.server_running and not _service_looks_active_or_starting(service):
        return "stopped"
    if _service_is_activating(service):
        return "starting"
    return "running"

def _port_value(*values: int | None, default: int) -> int:
    for value in values:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return default

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


def _load_server_version(
    instance: str,
    state: ServerState,
    web_config: Any | None,
    errors: list[DashboardError],
    *,
    server_running: bool | None = None,
) -> dict[str, Any]:
    db_path = getattr(web_config, "db_path", None) if web_config is not None else None
    try:
        return server_versions.load_server_version_state(
            instance=instance,
            state=state,
            db_path=db_path,
            server_running=server_running,
        ).to_dict()
    except Exception as error:  # noqa: BLE001 - dashboard rendering must degrade.
        message = str(error) or error.__class__.__name__
        errors.append(DashboardError(section="server_version", message=message))
        return server_versions.failed_server_version_state(
            failure_reason=error,
            server_running=(
                bool(server_running)
                if server_running is not None
                else state.server_running
            ),
        ).to_dict()


def load_dashboard_snapshot(
    instance: str,
    *,
    web_config: Any | None = None,
    adapter: ServiceAdapter | None = None,
) -> dict[str, Any]:
    errors: list[DashboardError] = []
    service_adapter = _resolve_service_adapter(adapter)
    state = discovery.discover(instance=instance, save=False)
    state_dict = state.to_dict()

    if state.server_installed or state.service_exists:
        service = _safe_section(
            "service",
            errors,
            _unavailable("service status is not available"),
            service_adapter.get_service_status,
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
            service_adapter.get_timer_status,
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

    operational_status = _load_operational_status(state, errors)
    lifecycle = _dashboard_lifecycle(state, service)
    dashboard_running = lifecycle == "running"
    server_version = _load_server_version(
        instance,
        state,
        web_config,
        errors,
        server_running=dashboard_running,
    )
    if server_version.get("check_state") == server_versions.SERVER_VERSION_CHECK_UPDATING:
        lifecycle = "updating"
        dashboard_running = False

    if dashboard_running:
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
        running=dashboard_running,
        overview=_overview(state, lifecycle),
        state=state_dict,
        paths=_paths(state),
        service=service,
        timer=timer,
        service_runtime=_load_service_runtime(service, errors),
        operational_status=operational_status,
        config=config_summary,
        mods=mods_summary,
        server_version=server_version,
        host_metrics=host_metrics,
        fps_metrics=fps_metrics,
        players=players,
        ports=_load_ports(state, config_summary, errors),
        web=_load_web_runtime(web_config),
        bot=load_bot_summary(instance, errors),
        sat=_load_sat_summary(state, errors),
        errors=tuple(errors),
    )
    return snapshot.to_dict()
