"""Generated systemd service and timer for the supervised session pipeline."""

from __future__ import annotations

import os
import pwd
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from armactl import paths, player_log_ingest_service
from armactl.service_manager import (
    ServiceResult,
    daemon_reload,
    disable_service,
    enable_service,
    get_systemd_unit_status,
    install_privileged_systemctl_channel,
    install_systemd_unit_file,
    resolve_linux_user,
    start_service,
    stop_service,
)
from armactl.web.services.player_session_scheduler_runner import (
    PlayerSessionSchedulerStatus,
    read_player_session_scheduler_status,
)

PLAYER_SESSION_PIPELINE_TIMER_INTERVAL_SECONDS: Final = 120
PLAYER_SESSION_PIPELINE_TIMER_INITIAL_DELAY_SECONDS: Final = 150
PLAYER_SESSION_PIPELINE_RUNTIME_GUARD_SECONDS: Final = 240


@dataclass(frozen=True)
class PlayerSessionPipelineInstallResult:
    instance: str
    service_name: str
    timer_name: str
    service_path: Path
    timer_path: Path
    preserved_enabled: bool
    results: tuple[ServiceResult, ...]

    @property
    def success(self) -> bool:
        return bool(self.results) and all(result.success for result in self.results)

    @property
    def exit_code(self) -> int:
        return next(
            (result.exit_code or 1 for result in self.results if not result.success),
            0,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance": self.instance,
            "service_name": self.service_name,
            "timer_name": self.timer_name,
            "service_path": str(self.service_path),
            "timer_path": str(self.timer_path),
            "preserved_enabled": self.preserved_enabled,
            "success": self.success,
            "results": [result.to_dict() for result in self.results],
        }


@dataclass(frozen=True)
class PlayerSessionPipelineActionResult:
    action: str
    timer_name: str
    results: tuple[ServiceResult, ...]

    @property
    def success(self) -> bool:
        return bool(self.results) and all(result.success for result in self.results)

    @property
    def exit_code(self) -> int:
        return next(
            (result.exit_code or 1 for result in self.results if not result.success),
            0,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "timer_name": self.timer_name,
            "success": self.success,
            "results": [result.to_dict() for result in self.results],
        }


@dataclass(frozen=True)
class PlayerSessionPipelineServiceStatus:
    instance: str
    cadence_seconds: int
    initial_delay_seconds: int
    runtime_guard_seconds: int
    service: dict[str, Any]
    timer: dict[str, Any]
    pipeline: PlayerSessionSchedulerStatus

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance": self.instance,
            "cadence_seconds": self.cadence_seconds,
            "initial_delay_seconds": self.initial_delay_seconds,
            "runtime_guard_seconds": self.runtime_guard_seconds,
            "service": dict(self.service),
            "timer": dict(self.timer),
            "pipeline": self.pipeline.to_dict(),
        }


def player_session_pipeline_service_unit_name(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> str:
    normalized = paths.validate_instance_name(instance)
    if normalized == paths.DEFAULT_INSTANCE_NAME:
        return paths.PLAYER_SESSION_PIPELINE_SERVICE_NAME
    return f"armactl-player-session-pipeline@{normalized}.service"


def player_session_pipeline_timer_unit_name(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> str:
    normalized = paths.validate_instance_name(instance)
    if normalized == paths.DEFAULT_INSTANCE_NAME:
        return paths.PLAYER_SESSION_PIPELINE_TIMER_NAME
    return f"armactl-player-session-pipeline@{normalized}.timer"


def player_session_pipeline_python_path(project_root: Path | None = None) -> Path:
    root = project_root or paths.project_root()
    return root / ".venv" / "bin" / "python"


def check_player_session_pipeline_runtime(
    project_root: Path | None = None,
) -> ServiceResult:
    root = project_root or paths.project_root()
    python_bin = player_session_pipeline_python_path(root)
    if not python_bin.is_file() or not os.access(python_bin, os.X_OK):
        return ServiceResult(False, "Player-session pipeline runtime is missing.", 1)
    try:
        result = subprocess.run(
            [str(python_bin), "-c", "import armactl"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ServiceResult(False, "Player-session pipeline runtime check failed.", 1)
    if result.returncode:
        return ServiceResult(False, "Player-session pipeline runtime import failed.", 1)
    return ServiceResult(True, "Player-session pipeline runtime is ready.", 0)


def render_player_session_pipeline_service_unit(
    *,
    instance: str,
    data_root: Path,
    project_root: Path,
    python_bin: Path,
    user: str,
    home_dir: Path,
) -> str:
    template = _template_environment(project_root).get_template(
        "armactl-player-session-pipeline.service.j2"
    )
    return _normalize_generated_text(
        template.render(
            instance=paths.validate_instance_name(instance),
            data_root=data_root,
            project_root=project_root,
            python_bin=python_bin,
            user=user,
            home_dir=home_dir,
            runtime_guard_seconds=PLAYER_SESSION_PIPELINE_RUNTIME_GUARD_SECONDS,
        )
    )


def render_player_session_pipeline_timer_unit(
    *,
    instance: str,
    service_name: str,
    project_root: Path,
) -> str:
    template = _template_environment(project_root).get_template(
        "armactl-player-session-pipeline.timer.j2"
    )
    return _normalize_generated_text(
        template.render(
            instance=paths.validate_instance_name(instance),
            service_name=service_name,
            initial_delay_seconds=PLAYER_SESSION_PIPELINE_TIMER_INITIAL_DELAY_SECONDS,
            interval_seconds=PLAYER_SESSION_PIPELINE_TIMER_INTERVAL_SECONDS,
        )
    )


def install_player_session_pipeline_service(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
    project_root: Path | None = None,
) -> PlayerSessionPipelineInstallResult:
    """Install units, preserving enablement; first install stays disabled/inactive."""
    normalized = paths.validate_instance_name(instance)
    source_root = (project_root or paths.project_root()).expanduser().resolve(strict=False)
    resolved_data_root = data_root.expanduser().resolve(strict=False)
    service_name = player_session_pipeline_service_unit_name(normalized)
    timer_name = player_session_pipeline_timer_unit_name(normalized)
    service_path = paths.SYSTEMD_DIR / service_name
    timer_path = paths.SYSTEMD_DIR / timer_name
    previous = get_systemd_unit_status(timer_name, unit_path=timer_path)
    preserve_enabled = bool(previous["enabled"])
    first_install = not bool(previous["exists"])
    results: list[ServiceResult] = []

    runtime = check_player_session_pipeline_runtime(source_root)
    results.append(runtime)
    if not runtime.success:
        return PlayerSessionPipelineInstallResult(
            normalized,
            service_name,
            timer_name,
            service_path,
            timer_path,
            preserve_enabled,
            tuple(results),
        )

    user = resolve_linux_user()
    home_dir = _home_directory_for_user(user)
    python_bin = player_session_pipeline_python_path(source_root)
    service_text = render_player_session_pipeline_service_unit(
        instance=normalized,
        data_root=resolved_data_root,
        project_root=source_root,
        python_bin=python_bin,
        user=user,
        home_dir=home_dir,
    )
    timer_text = render_player_session_pipeline_timer_unit(
        instance=normalized,
        service_name=service_name,
        project_root=source_root,
    )
    with tempfile.TemporaryDirectory() as tempd:
        temp_dir = Path(tempd)
        temp_service = temp_dir / service_name
        temp_timer = temp_dir / timer_name
        temp_service.write_text(service_text, encoding="utf-8")
        temp_timer.write_text(timer_text, encoding="utf-8")
        results.append(install_systemd_unit_file(temp_service, service_path))
        if results[-1].success:
            results.append(install_systemd_unit_file(temp_timer, timer_path))

    if all(result.success for result in results):
        results.append(daemon_reload())
    if all(result.success for result in results):
        results.extend(install_privileged_systemctl_channel())
    if all(result.success for result in results) and first_install:
        results.append(stop_service(timer_name))
        results.append(disable_service(timer_name))
    return PlayerSessionPipelineInstallResult(
        normalized,
        service_name,
        timer_name,
        service_path,
        timer_path,
        preserve_enabled,
        tuple(results),
    )


def enable_player_session_pipeline_timer(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
) -> PlayerSessionPipelineActionResult:
    normalized = paths.validate_instance_name(instance)
    timer_name = player_session_pipeline_timer_unit_name(normalized)
    ingest_status = player_log_ingest_service.get_player_log_ingest_service_status(
        normalized,
        data_root=data_root,
    )
    if not bool(ingest_status.timer.get("exists")):
        return PlayerSessionPipelineActionResult(
            "enable",
            timer_name,
            (ServiceResult(False, "Player-log ingest timer is not installed.", 1),),
        )
    if not bool(ingest_status.timer.get("enabled")):
        return PlayerSessionPipelineActionResult(
            "enable",
            timer_name,
            (ServiceResult(False, "Player-log ingest timer is not enabled.", 1),),
        )
    if ingest_status.freshness.freshness_status == "failed":
        return PlayerSessionPipelineActionResult(
            "enable",
            timer_name,
            (ServiceResult(False, "Player-log ingest status is failed.", 1),),
        )
    results = [enable_service(timer_name)]
    if results[0].success:
        results.append(start_service(timer_name))
    return PlayerSessionPipelineActionResult("enable", timer_name, tuple(results))


def disable_player_session_pipeline_timer(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> PlayerSessionPipelineActionResult:
    timer_name = player_session_pipeline_timer_unit_name(instance)
    results = [stop_service(timer_name), disable_service(timer_name)]
    return PlayerSessionPipelineActionResult("disable", timer_name, tuple(results))


def get_player_session_pipeline_service_status(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
) -> PlayerSessionPipelineServiceStatus:
    """Read unit and players.db status without creating either."""
    normalized = paths.validate_instance_name(instance)
    service_name = player_session_pipeline_service_unit_name(normalized)
    timer_name = player_session_pipeline_timer_unit_name(normalized)
    service = get_systemd_unit_status(
        service_name,
        unit_path=paths.SYSTEMD_DIR / service_name,
    )
    timer = get_systemd_unit_status(
        timer_name,
        unit_path=paths.SYSTEMD_DIR / timer_name,
    )
    pipeline = read_player_session_scheduler_status(
        data_root / "web" / "web.db",
        instance=normalized,
        data_root=data_root,
    )
    return PlayerSessionPipelineServiceStatus(
        instance=normalized,
        cadence_seconds=PLAYER_SESSION_PIPELINE_TIMER_INTERVAL_SECONDS,
        initial_delay_seconds=PLAYER_SESSION_PIPELINE_TIMER_INITIAL_DELAY_SECONDS,
        runtime_guard_seconds=PLAYER_SESSION_PIPELINE_RUNTIME_GUARD_SECONDS,
        service=service,
        timer=timer,
        pipeline=pipeline,
    )


def _template_environment(project_root: Path) -> Environment:
    return Environment(
        loader=FileSystemLoader(str(project_root / "templates")),
        autoescape=False,
        keep_trailing_newline=True,
        undefined=StrictUndefined,
    )


def _normalize_generated_text(value: str) -> str:
    return value.rstrip() + "\n"


def _home_directory_for_user(user: str) -> Path:
    try:
        return Path(pwd.getpwnam(user).pw_dir)
    except KeyError:
        return Path.home()
