"""Explicit systemd service/timer foundation for shared player-log ingest."""

from __future__ import annotations

import os
import pwd
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from armactl import paths
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
from armactl.web.services import player_registry
from armactl.web.services.audit import AuditLogError, append_audit_event
from armactl.web.services.player_log_ingest import (
    DEFAULT_MAX_LOG_FILES,
    PlayerLogIngestResult,
    PlayerLogIngestStatus,
    read_player_log_ingest_status,
    run_player_log_ingest_once,
)

PLAYER_LOG_INGEST_TIMER_INTERVAL_SECONDS: Final = 120
PLAYER_LOG_INGEST_RUNTIME_SECONDS_PER_FILE: Final = 10
PLAYER_LOG_INGEST_RUNTIME_FIXED_OVERHEAD_SECONDS: Final = 40
PLAYER_LOG_INGEST_RUNTIME_GUARD_SECONDS: Final = (
    DEFAULT_MAX_LOG_FILES * PLAYER_LOG_INGEST_RUNTIME_SECONDS_PER_FILE
    + PLAYER_LOG_INGEST_RUNTIME_FIXED_OVERHEAD_SECONDS
)


@dataclass(frozen=True)
class PlayerLogIngestInstallResult:
    instance: str
    service_name: str
    timer_name: str
    service_path: Path
    timer_path: Path
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
            "success": self.success,
            "results": [_service_result_dict(result) for result in self.results],
        }


@dataclass(frozen=True)
class PlayerLogIngestActionResult:
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
            "results": [_service_result_dict(result) for result in self.results],
        }


@dataclass(frozen=True)
class PlayerLogIngestServiceStatus:
    instance: str
    cadence_seconds: int
    service: dict[str, Any]
    timer: dict[str, Any]
    freshness: PlayerLogIngestStatus

    def to_dict(self) -> dict[str, Any]:
        payload = self.freshness.to_dict()
        payload.update(
            {
                "instance": self.instance,
                "cadence_seconds": self.cadence_seconds,
                "service": dict(self.service),
                "timer": dict(self.timer),
                "freshness": self.freshness.to_dict(),
            }
        )
        return payload


@dataclass(frozen=True)
class ScheduledPlayerLogIngestResult:
    ingest: PlayerLogIngestResult
    skipped: bool
    transition: str
    audit_state: str

    @property
    def exit_code(self) -> int:
        if self.audit_state == "write_failed":
            return 1
        return 0 if self.skipped else self.ingest.exit_code

    def to_dict(self) -> dict[str, Any]:
        outcome = "skipped" if self.skipped else self.ingest.outcome
        return {
            "outcome": outcome,
            "reason": self.ingest.failure_code,
            "freshness_status": self.ingest.freshness_status,
            "files_considered": self.ingest.files_considered,
            "files_selected": self.ingest.files_selected_for_scan,
            "files_scanned": self.ingest.files_scanned,
            "files_skipped": self.ingest.files_skipped,
            "lines_scanned": self.ingest.scanned_lines,
            "parsed_events": self.ingest.parsed_events,
            "stored_events": self.ingest.stored_events,
            "duplicate_events": self.ingest.duplicate_events,
            "unmatched_lines": self.ingest.unmatched_lines,
            "skipped_lines": self.ingest.skipped_lines,
            "error_count": self.ingest.error_count,
            "checkpoint_updated": self.ingest.checkpoint_updated,
            "transition": self.transition,
            "audit_state": self.audit_state,
            "exit_code": self.exit_code,
        }


def player_log_ingest_service_unit_name(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> str:
    normalized = paths.validate_instance_name(instance)
    if normalized == paths.DEFAULT_INSTANCE_NAME:
        return paths.PLAYER_LOG_INGEST_SERVICE_NAME
    return f"armactl-player-log-ingest@{normalized}.service"


def player_log_ingest_timer_unit_name(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> str:
    normalized = paths.validate_instance_name(instance)
    if normalized == paths.DEFAULT_INSTANCE_NAME:
        return paths.PLAYER_LOG_INGEST_TIMER_NAME
    return f"armactl-player-log-ingest@{normalized}.timer"


def player_log_ingest_python_path(project_root: Path | None = None) -> Path:
    root = project_root or paths.project_root()
    return root / ".venv" / "bin" / "python"


def check_player_log_ingest_service_runtime(
    project_root: Path | None = None,
) -> ServiceResult:
    root = project_root or paths.project_root()
    python_bin = player_log_ingest_python_path(root)
    if not python_bin.is_file() or not os.access(python_bin, os.X_OK):
        return ServiceResult(
            False,
            "Player-log ingest service runtime is missing from the project virtualenv.",
            1,
        )
    try:
        result = subprocess.run(
            [str(python_bin), "-c", "import armactl"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ServiceResult(False, "Player-log ingest service runtime check failed.", 1)
    if result.returncode:
        return ServiceResult(False, "Player-log ingest service runtime import failed.", 1)
    return ServiceResult(True, "Player-log ingest service runtime is ready.", 0)


def render_player_log_ingest_service_unit(
    *,
    instance: str,
    data_root: Path,
    project_root: Path,
    python_bin: Path,
    user: str,
    home_dir: Path,
) -> str:
    normalized = paths.validate_instance_name(instance)
    template = _template_environment(project_root).get_template(
        "armactl-player-log-ingest.service.j2"
    )
    return _normalize_generated_text(
        template.render(
            instance=normalized,
            data_root=data_root,
            project_root=project_root,
            python_bin=python_bin,
            user=user,
            home_dir=home_dir,
            runtime_guard_seconds=PLAYER_LOG_INGEST_RUNTIME_GUARD_SECONDS,
        )
    )


def render_player_log_ingest_timer_unit(
    *,
    instance: str,
    service_name: str,
    project_root: Path,
) -> str:
    normalized = paths.validate_instance_name(instance)
    template = _template_environment(project_root).get_template(
        "armactl-player-log-ingest.timer.j2"
    )
    return _normalize_generated_text(
        template.render(
            instance=normalized,
            service_name=service_name,
            interval_seconds=PLAYER_LOG_INGEST_TIMER_INTERVAL_SECONDS,
        )
    )


def install_player_log_ingest_service(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
    project_root: Path | None = None,
) -> PlayerLogIngestInstallResult:
    normalized = paths.validate_instance_name(instance)
    source_root = (project_root or paths.project_root()).expanduser().resolve(strict=False)
    resolved_data_root = data_root.expanduser().resolve(strict=False)
    service_name = player_log_ingest_service_unit_name(normalized)
    timer_name = player_log_ingest_timer_unit_name(normalized)
    service_path = paths.SYSTEMD_DIR / service_name
    timer_path = paths.SYSTEMD_DIR / timer_name
    results: list[ServiceResult] = []

    runtime_result = check_player_log_ingest_service_runtime(source_root)
    results.append(runtime_result)
    if not runtime_result.success:
        return PlayerLogIngestInstallResult(
            normalized,
            service_name,
            timer_name,
            service_path,
            timer_path,
            tuple(results),
        )

    user = resolve_linux_user()
    home_dir = _home_directory_for_user(user)
    python_bin = player_log_ingest_python_path(source_root)
    service_text = render_player_log_ingest_service_unit(
        instance=normalized,
        data_root=resolved_data_root,
        project_root=source_root,
        python_bin=python_bin,
        user=user,
        home_dir=home_dir,
    )
    timer_text = render_player_log_ingest_timer_unit(
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

        service_install = install_systemd_unit_file(temp_service, service_path)
        results.append(service_install)
        if service_install.success:
            timer_install = install_systemd_unit_file(temp_timer, timer_path)
            results.append(timer_install)

    if all(result.success for result in results):
        results.append(daemon_reload())
    if all(result.success for result in results):
        results.extend(install_privileged_systemctl_channel())

    return PlayerLogIngestInstallResult(
        normalized,
        service_name,
        timer_name,
        service_path,
        timer_path,
        tuple(results),
    )


def enable_player_log_ingest_timer(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> PlayerLogIngestActionResult:
    timer_name = player_log_ingest_timer_unit_name(instance)
    previous_status = get_systemd_unit_status(
        timer_name,
        unit_path=paths.SYSTEMD_DIR / timer_name,
    )
    rollback_on_start_failure = previous_status["unit_file_state"] in {
        "disabled",
        "missing",
    }
    results = [enable_service(timer_name)]
    if results[0].success:
        start_result = start_service(timer_name)
        results.append(start_result)
        if not start_result.success and rollback_on_start_failure:
            results.append(stop_service(timer_name))
            results.append(disable_service(timer_name))
    return PlayerLogIngestActionResult("enable", timer_name, tuple(results))


def disable_player_log_ingest_timer(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> PlayerLogIngestActionResult:
    timer_name = player_log_ingest_timer_unit_name(instance)
    results = [stop_service(timer_name), disable_service(timer_name)]
    return PlayerLogIngestActionResult("disable", timer_name, tuple(results))


def get_player_log_ingest_service_status(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
) -> PlayerLogIngestServiceStatus:
    normalized = paths.validate_instance_name(instance)
    service_name = player_log_ingest_service_unit_name(normalized)
    timer_name = player_log_ingest_timer_unit_name(normalized)
    service = get_systemd_unit_status(
        service_name,
        unit_path=paths.SYSTEMD_DIR / service_name,
    )
    timer = get_systemd_unit_status(
        timer_name,
        unit_path=paths.SYSTEMD_DIR / timer_name,
    )
    freshness = _read_ingest_status_safely(normalized, data_root=data_root)
    return PlayerLogIngestServiceStatus(
        instance=normalized,
        cadence_seconds=PLAYER_LOG_INGEST_TIMER_INTERVAL_SECONDS,
        service=service,
        timer=timer,
        freshness=freshness,
    )


def run_scheduled_player_log_ingest_once(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
) -> ScheduledPlayerLogIngestResult:
    normalized = paths.validate_instance_name(instance)
    before = _read_ingest_status_safely(normalized, data_root=data_root)
    ingest = run_player_log_ingest_once(normalized, data_root=data_root)
    if ingest.busy:
        return ScheduledPlayerLogIngestResult(
            ingest=ingest,
            skipped=True,
            transition="none",
            audit_state="not_needed",
        )

    after = _read_ingest_status_safely(normalized, data_root=data_root)
    transition = _scheduled_audit_transition(before, after, ingest)
    audit_state = "not_needed"
    if transition != "none":
        audit_state = _write_scheduled_transition_audit(
            normalized,
            data_root=data_root,
            before=before,
            after=after,
            ingest=ingest,
            transition=transition,
        )
    return ScheduledPlayerLogIngestResult(
        ingest=ingest,
        skipped=False,
        transition=transition,
        audit_state=audit_state,
    )


def format_scheduled_player_log_ingest_result(
    result: ScheduledPlayerLogIngestResult,
) -> str:
    values = result.to_dict()
    ordered_keys = (
        "outcome",
        "reason",
        "freshness_status",
        "files_considered",
        "files_selected",
        "files_scanned",
        "files_skipped",
        "lines_scanned",
        "parsed_events",
        "stored_events",
        "duplicate_events",
        "unmatched_lines",
        "skipped_lines",
        "error_count",
        "checkpoint_updated",
        "transition",
        "audit_state",
    )
    fields = [f"{key}={values[key]}" for key in ordered_keys if values[key] not in {"", None}]
    return "player_log_ingest " + " ".join(fields)


def _scheduled_audit_transition(
    before: PlayerLogIngestStatus,
    after: PlayerLogIngestStatus,
    ingest: PlayerLogIngestResult,
) -> str:
    previous = before.freshness_status
    current = after.freshness_status
    failed = player_registry.PLAYER_LOG_INGEST_STATUS_FAILED
    unavailable = player_registry.PLAYER_LOG_INGEST_STATUS_UNAVAILABLE

    if not ingest.success:
        if previous != failed and current == failed:
            return "failure"
        return "none"
    if previous == failed and current not in {failed, unavailable}:
        return "recovery"
    if previous != current and current != unavailable:
        return "freshness_transition"
    return "none"


def _write_scheduled_transition_audit(
    instance: str,
    *,
    data_root: Path,
    before: PlayerLogIngestStatus,
    after: PlayerLogIngestStatus,
    ingest: PlayerLogIngestResult,
    transition: str,
) -> str:
    message = {
        "failure": "Scheduled player log ingest entered failed freshness state.",
        "recovery": "Scheduled player log ingest freshness recovered.",
        "freshness_transition": "Scheduled player log ingest freshness state changed.",
    }[transition]
    details = {
        "phase": "scheduled_outcome",
        "runner": "systemd_timer",
        "transition": transition,
        "previous_freshness": before.freshness_status,
        "freshness_status": after.freshness_status,
        "outcome": ingest.outcome,
        "failure_code": ingest.failure_code,
        "files_considered": ingest.files_considered,
        "files_selected": ingest.files_selected_for_scan,
        "files_scanned": ingest.files_scanned,
        "files_skipped": ingest.files_skipped,
        "lines_scanned": ingest.scanned_lines,
        "parsed_events": ingest.parsed_events,
        "stored_events": ingest.stored_events,
        "duplicate_events": ingest.duplicate_events,
        "unmatched_lines": ingest.unmatched_lines,
        "skipped_lines": ingest.skipped_lines,
        "error_count": ingest.error_count,
        "checkpoint_updated": ingest.checkpoint_updated,
    }
    try:
        append_audit_event(
            paths.web_audit_log_file(data_root),
            username="player-log-ingest-scheduled",
            action="players.log-ingest.scheduled",
            instance=instance,
            target="players:log-ingest",
            success=ingest.success,
            message=message,
            exit_code=0 if ingest.success else ingest.exit_code,
            details=details,
        )
    except AuditLogError:
        return "write_failed"
    return "written"


def _read_ingest_status_safely(
    instance: str,
    *,
    data_root: Path,
) -> PlayerLogIngestStatus:
    try:
        return read_player_log_ingest_status(instance, data_root=data_root)
    except Exception:
        return PlayerLogIngestStatus(
            instance=instance,
            state="unavailable",
            reason="status_read_failed",
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


def _service_result_dict(result: ServiceResult) -> dict[str, Any]:
    return {
        "success": result.success,
        "message": result.message,
        "exit_code": result.exit_code,
    }
