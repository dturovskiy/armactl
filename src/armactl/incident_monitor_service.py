"""Generated systemd service/timer for persistent incident collection."""

from __future__ import annotations

import os
import pwd
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from armactl import incident_monitor, paths
from armactl.service_manager import (
    ServiceResult,
    daemon_reload,
    disable_service,
    enable_service,
    get_systemd_unit_status,
    install_systemd_unit_file,
    resolve_linux_user,
    service_unit_name,
    start_service,
    stop_service,
)

INCIDENT_MONITOR_INTERVAL_SECONDS: Final = 15
INCIDENT_MONITOR_INITIAL_DELAY_SECONDS: Final = 20
INCIDENT_MONITOR_RUNTIME_GUARD_SECONDS: Final = 12


@dataclass(frozen=True)
class IncidentMonitorInstallResult:
    instance: str
    service_name: str
    timer_name: str
    service_path: Path
    timer_path: Path
    core_dropin_path: Path
    preserved_enabled: bool
    results: tuple[ServiceResult, ...]

    @property
    def success(self) -> bool:
        return bool(self.results) and all(result.success for result in self.results)

    @property
    def exit_code(self) -> int:
        return next((result.exit_code or 1 for result in self.results if not result.success), 0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance": self.instance,
            "service_name": self.service_name,
            "timer_name": self.timer_name,
            "service_path": str(self.service_path),
            "timer_path": str(self.timer_path),
            "core_dropin_path": str(self.core_dropin_path),
            "preserved_enabled": self.preserved_enabled,
            "success": self.success,
            "results": [result.to_dict() for result in self.results],
        }


@dataclass(frozen=True)
class IncidentMonitorActionResult:
    action: str
    timer_name: str
    results: tuple[ServiceResult, ...]

    @property
    def success(self) -> bool:
        return bool(self.results) and all(result.success for result in self.results)

    @property
    def exit_code(self) -> int:
        return next((result.exit_code or 1 for result in self.results if not result.success), 0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "timer_name": self.timer_name,
            "success": self.success,
            "results": [result.to_dict() for result in self.results],
        }


def incident_monitor_service_unit_name(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> str:
    normalized = paths.validate_instance_name(instance)
    if normalized == paths.DEFAULT_INSTANCE_NAME:
        return paths.INCIDENT_MONITOR_SERVICE_NAME
    return f"armactl-incident-monitor@{normalized}.service"


def incident_monitor_timer_unit_name(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> str:
    normalized = paths.validate_instance_name(instance)
    if normalized == paths.DEFAULT_INSTANCE_NAME:
        return paths.INCIDENT_MONITOR_TIMER_NAME
    return f"armactl-incident-monitor@{normalized}.timer"


def _python_path(project_root: Path) -> Path:
    return project_root / ".venv" / "bin" / "python"


def _runtime_check(project_root: Path) -> ServiceResult:
    python_bin = _python_path(project_root)
    if not python_bin.is_file() or not os.access(python_bin, os.X_OK):
        return ServiceResult(False, "Incident monitor runtime is missing.", 1)
    try:
        result = subprocess.run(
            [str(python_bin), "-c", "import armactl.incident_monitor"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ServiceResult(False, "Incident monitor runtime check failed.", 1)
    if result.returncode:
        return ServiceResult(False, "Incident monitor runtime import failed.", 1)
    return ServiceResult(True, "Incident monitor runtime is ready.", 0)


def _environment(project_root: Path) -> Environment:
    return Environment(
        loader=FileSystemLoader(str(project_root / "templates")),
        autoescape=False,
        keep_trailing_newline=True,
        undefined=StrictUndefined,
    )


def _home_directory(user: str) -> Path:
    try:
        return Path(pwd.getpwnam(user).pw_dir)
    except KeyError:
        return Path.home()


def render_incident_monitor_service_unit(
    *,
    instance: str,
    data_root: Path,
    project_root: Path,
    user: str,
    home_dir: Path,
) -> str:
    service = _environment(project_root).get_template("armactl-incident-monitor.service.j2")
    return (
        service.render(
            instance=paths.validate_instance_name(instance),
            data_root=data_root,
            project_root=project_root,
            python_bin=_python_path(project_root),
            user=user,
            home_dir=home_dir,
            game_service_name=service_unit_name(instance),
            runtime_guard_seconds=INCIDENT_MONITOR_RUNTIME_GUARD_SECONDS,
        ).rstrip()
        + "\n"
    )


def render_incident_monitor_timer_unit(
    *,
    instance: str,
    service_name: str,
    project_root: Path,
) -> str:
    timer = _environment(project_root).get_template("armactl-incident-monitor.timer.j2")
    return (
        timer.render(
            instance=paths.validate_instance_name(instance),
            service_name=service_name,
            initial_delay_seconds=INCIDENT_MONITOR_INITIAL_DELAY_SECONDS,
            interval_seconds=INCIDENT_MONITOR_INTERVAL_SECONDS,
        ).rstrip()
        + "\n"
    )


def render_incident_core_dropin(*, project_root: Path) -> str:
    dropin = _environment(project_root).get_template("armactl-incident-core.conf.j2")
    return dropin.render().rstrip() + "\n"


def install_incident_monitor_service(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
    project_root: Path | None = None,
) -> IncidentMonitorInstallResult:
    """Install units, preserving enablement; first install remains inactive."""
    normalized = paths.validate_instance_name(instance)
    source_root = (project_root or paths.project_root()).expanduser().resolve(strict=False)
    resolved_data_root = data_root.expanduser().resolve(strict=False)
    service_name = incident_monitor_service_unit_name(normalized)
    timer_name = incident_monitor_timer_unit_name(normalized)
    service_path = paths.SYSTEMD_DIR / service_name
    timer_path = paths.SYSTEMD_DIR / timer_name
    core_dropin_path = (
        paths.SYSTEMD_DIR
        / f"{service_unit_name(normalized)}.d"
        / "20-armactl-incident-core.conf"
    )
    previous = get_systemd_unit_status(timer_name, unit_path=timer_path)
    preserved_enabled = bool(previous["enabled"])
    first_install = not bool(previous["exists"])
    results: list[ServiceResult] = [_runtime_check(source_root)]
    if not results[-1].success:
        return IncidentMonitorInstallResult(
            normalized,
            service_name,
            timer_name,
            service_path,
            timer_path,
            core_dropin_path,
            preserved_enabled,
            tuple(results),
        )

    user = resolve_linux_user()
    service_text = render_incident_monitor_service_unit(
        instance=normalized,
        data_root=resolved_data_root,
        project_root=source_root,
        user=user,
        home_dir=_home_directory(user),
    )
    timer_text = render_incident_monitor_timer_unit(
        instance=normalized,
        service_name=service_name,
        project_root=source_root,
    )
    core_dropin_text = render_incident_core_dropin(project_root=source_root)
    with tempfile.TemporaryDirectory() as tempd:
        temp_dir = Path(tempd)
        temp_service = temp_dir / service_name
        temp_timer = temp_dir / timer_name
        temp_core_dropin = temp_dir / core_dropin_path.name
        temp_service.write_text(service_text, encoding="utf-8")
        temp_timer.write_text(timer_text, encoding="utf-8")
        temp_core_dropin.write_text(core_dropin_text, encoding="utf-8")
        results.append(install_systemd_unit_file(temp_service, service_path))
        if results[-1].success:
            results.append(install_systemd_unit_file(temp_timer, timer_path))
        if results[-1].success:
            results.append(install_systemd_unit_file(temp_core_dropin, core_dropin_path))
    if all(result.success for result in results):
        results.append(daemon_reload())
    if all(result.success for result in results) and first_install:
        results.extend((stop_service(timer_name), disable_service(timer_name)))
    return IncidentMonitorInstallResult(
        normalized,
        service_name,
        timer_name,
        service_path,
        timer_path,
        core_dropin_path,
        preserved_enabled,
        tuple(results),
    )


def enable_incident_monitor_timer(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> IncidentMonitorActionResult:
    timer_name = incident_monitor_timer_unit_name(instance)
    results = [enable_service(timer_name)]
    if results[-1].success:
        results.append(start_service(timer_name))
    return IncidentMonitorActionResult("enable", timer_name, tuple(results))


def disable_incident_monitor_timer(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> IncidentMonitorActionResult:
    timer_name = incident_monitor_timer_unit_name(instance)
    return IncidentMonitorActionResult(
        "disable",
        timer_name,
        (stop_service(timer_name), disable_service(timer_name)),
    )


def get_incident_monitor_status(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
) -> dict[str, Any]:
    normalized = paths.validate_instance_name(instance)
    service_name = incident_monitor_service_unit_name(normalized)
    timer_name = incident_monitor_timer_unit_name(normalized)
    return {
        "instance": normalized,
        "cadence_seconds": INCIDENT_MONITOR_INTERVAL_SECONDS,
        "service": get_systemd_unit_status(service_name),
        "timer": get_systemd_unit_status(timer_name),
        "collector": incident_monitor.read_monitor_status(normalized, data_root=data_root),
    }
