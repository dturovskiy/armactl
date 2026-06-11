# Read-only dashboard facade for the future web panel.

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any

from armactl import discovery, metrics, player_view, ports, service_manager, status_summary
from armactl.state import ServerState

DEFAULT_GAME_PORT = 2001
DEFAULT_A2S_PORT = 17777
DEFAULT_RCON_PORT = 19999


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
    state: dict[str, Any]
    service: dict[str, Any]
    timer: dict[str, Any]
    config: dict[str, Any]
    mods: dict[str, Any]
    host_metrics: dict[str, Any]
    fps_metrics: dict[str, Any]
    players: dict[str, Any]
    ports: dict[str, Any]
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


def _load_summaries(
    state: ServerState,
    errors: list[DashboardError],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not state.config_path:
        message = "config path is not available"
        return _unavailable(message), _unavailable(message)

    try:
        config_summary, mods_summary = status_summary.load_status_summaries(state.config_path)
    except Exception as error:
        message = str(error) or error.__class__.__name__
        errors.append(DashboardError(section="status_summaries", message=message))
        return _unavailable(message), _unavailable(message)

    return _plain_dict(config_summary), _plain_dict(mods_summary)


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


def load_dashboard_snapshot(instance: str) -> dict[str, Any]:
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

    host_metrics = _safe_section(
        "host_metrics",
        errors,
        _unavailable("host metrics are not available"),
        metrics.query_host_metrics,
    )

    config_dir = _config_dir_from_state(state)
    if config_dir is None:
        fps_metrics = _unavailable("config path is not available")
    else:
        fps_metrics = _safe_section(
            "fps_metrics",
            errors,
            _unavailable("server FPS metrics are not available"),
            metrics.query_server_fps_metrics,
            config_dir,
        )

    if state.server_running:
        players = _safe_section(
            "players",
            errors,
            _unavailable("player view is not available"),
            player_view.query_player_view,
            instance,
            state=state,
        )
    else:
        players = _unavailable("server is not running")

    snapshot = DashboardSnapshot(
        instance=instance,
        lifecycle=lifecycle,
        installed=state.server_installed,
        running=state.server_running,
        state=state_dict,
        service=service,
        timer=timer,
        config=config_summary,
        mods=mods_summary,
        host_metrics=host_metrics,
        fps_metrics=fps_metrics,
        players=players,
        ports=_load_ports(state, config_summary, errors),
        errors=tuple(errors),
    )
    return snapshot.to_dict()
