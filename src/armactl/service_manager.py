"""Service manager - manage the Arma Reforger systemd service.

Uses service_name from state.json (default: armareforger.service).
All systemctl calls go through subprocess with proper error handling.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from armactl import paths
from armactl.i18n import _, tr

TIME_ONLY_RE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")
DAILY_TIME_RE = re.compile(r"^\*-\*-\* (\d{1,2}:\d{2}:\d{2})$")
SUDO_AUTH_ERROR_MARKERS = (
    "a terminal is required to read the password",
    "a password is required",
)


@dataclass
class ServiceResult:
    """Result of a systemctl operation."""

    success: bool
    message: str
    exit_code: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"success": self.success, "message": self.message, "exit_code": self.exit_code}


def _run_systemctl(
    action: str,
    service_name: str | None = None,
    use_sudo: bool = True,
) -> ServiceResult:
    """Run a systemctl command and return the result."""
    action_label = {
        "start": _("Systemctl action: start"),
        "stop": _("Systemctl action: stop"),
        "restart": _("Systemctl action: restart"),
        "enable": _("Systemctl action: enable"),
        "disable": _("Systemctl action: disable"),
        "daemon-reload": _("Systemctl action: daemon-reload"),
    }.get(action, action)
    cmd = _build_systemctl_command(action, service_name=service_name, use_sudo=use_sudo)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return ServiceResult(
                success=True,
                message=tr(
                    "{action} {service_name}: ok",
                    action=action_label,
                    service_name=service_name,
                ),
                exit_code=0,
            )
        else:
            stderr = result.stderr.strip()
            if use_sudo and _looks_like_sudo_auth_error(stderr):
                return ServiceResult(
                    success=False,
                    message=_(
                        "Secure privileged control is not configured yet. "
                        "Install/update the bot service or re-run install/repair "
                        "from the TUI to install the secure sudo helper."
                    ),
                    exit_code=result.returncode,
                )
            return ServiceResult(
                success=False,
                message=tr(
                    "{action} {service_name} failed: {stderr}",
                    action=action_label,
                    service_name=service_name,
                    stderr=stderr,
                ),
                exit_code=result.returncode,
            )
    except subprocess.TimeoutExpired:
        return ServiceResult(
            success=False,
            message=tr(
                "{action} {service_name}: timed out after 30s",
                action=action_label,
                service_name=service_name,
            ),
            exit_code=1,
        )
    except FileNotFoundError:
        return ServiceResult(
            success=False,
            message=_("systemctl not found - is systemd installed?"),
            exit_code=1,
        )
    except OSError as e:
        return ServiceResult(
            success=False,
            message=tr(
                "{action} {service_name}: {error}",
                action=action_label,
                service_name=service_name,
                error=e,
            ),
            exit_code=1,
        )


def _resolve_systemctl_binary() -> str:
    """Return the systemctl binary path used by the privileged helper."""
    return shutil.which("systemctl") or "/usr/bin/systemctl"


def _resolve_install_binary() -> str:
    """Return the install binary path used for root-owned file placement."""
    return shutil.which("install") or "/usr/bin/install"


def _resolve_helper_python_binary() -> str:
    """Return the Python interpreter used for the privileged helper."""
    return shutil.which("python3") or sys.executable or "/usr/bin/python3"


def has_privileged_systemctl_channel() -> bool:
    """Return whether the narrow passwordless helper channel is installed."""
    return (
        paths.privileged_helper_file().is_file()
        and paths.privileged_sudoers_file().is_file()
    )


def _looks_like_sudo_auth_error(stderr: str) -> bool:
    """Detect sudo failures caused by non-interactive password prompts."""
    lowered = stderr.lower()
    return any(marker in lowered for marker in SUDO_AUTH_ERROR_MARKERS)


def _build_systemctl_command(
    action: str,
    service_name: str | None = None,
    *,
    use_sudo: bool = True,
) -> list[str]:
    """Build the safest available systemctl invocation for the current context."""
    if not use_sudo:
        cmd = [_resolve_systemctl_binary(), action]
        if service_name:
            cmd.append(service_name)
        return cmd

    if has_privileged_systemctl_channel():
        cmd = ["sudo", "-n", str(paths.privileged_helper_file()), action]
        if service_name:
            cmd.append(service_name)
        return cmd

    cmd = ["sudo"]
    if not sys.stdin.isatty():
        cmd.append("-n")
    cmd.extend([_resolve_systemctl_binary(), action])
    if service_name:
        cmd.append(service_name)
    return cmd


def _systemctl_helper_user() -> str:
    """Best-effort current Linux username for helper/sudoers installation."""
    user = os.getenv("USER", "root")
    try:
        if user == "root" and os.getlogin():
            user = os.getlogin()
    except OSError:
        pass
    return user


def _templates_dir() -> Path:
    """Return the repo templates directory used for systemd/helper files."""
    return Path(__file__).resolve().parents[2] / "templates"


def _template_environment() -> Environment:
    """Build the Jinja environment for armactl templates."""
    return Environment(loader=FileSystemLoader(str(_templates_dir())))


def _normalize_generated_text(text: str) -> str:
    """Normalize generated helper/unit text to Unix newlines."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized if normalized.endswith("\n") else f"{normalized}\n"


def _render_privileged_helper_script() -> str:
    """Render the root-owned helper script text."""
    env = _template_environment()
    rendered = env.get_template("armactl-systemctl-helper.py.j2").render(
        install_bin=_resolve_install_binary(),
        systemctl_bin=_resolve_systemctl_binary(),
    )
    return _normalize_generated_text(rendered)


def _render_privileged_sudoers(user: str) -> str:
    """Render the sudoers drop-in text for the current Linux user."""
    env = _template_environment()
    rendered = env.get_template("armactl-systemctl-helper.sudoers.j2").render(
        user=user,
        helper_path=str(paths.privileged_helper_file()),
    )
    return _normalize_generated_text(rendered)


def install_privileged_systemctl_channel() -> list[ServiceResult]:
    """Install the narrow helper + sudoers rule used for bot/TUI service actions."""
    results: list[ServiceResult] = []
    user = _systemctl_helper_user()

    try:
        helper_text = _render_privileged_helper_script()
        sudoers_text = _render_privileged_sudoers(user)

        with tempfile.TemporaryDirectory() as tempd:
            temp_dir = Path(tempd)
            helper_temp = temp_dir / paths.PRIVILEGED_HELPER_NAME
            sudoers_temp = temp_dir / paths.PRIVILEGED_SUDOERS_NAME
            helper_temp.write_text(helper_text, encoding="utf-8")
            sudoers_temp.write_text(sudoers_text, encoding="utf-8")

            python_bin = _resolve_helper_python_binary()
            if Path(python_bin).exists():
                validation = subprocess.run(
                    [python_bin, "-m", "py_compile", str(helper_temp)],
                    capture_output=True,
                    text=True,
                )
                if validation.returncode != 0:
                    error_text = validation.stderr.strip() or validation.stdout.strip()
                    return [
                        ServiceResult(
                            False,
                            tr(
                                "Failed to validate privileged helper {path}: {error}",
                                path=helper_temp,
                                error=error_text,
                            ),
                            validation.returncode,
                        )
                    ]

            visudo_bin = shutil.which("visudo") or "/usr/sbin/visudo"
            if Path(visudo_bin).exists():
                validation = subprocess.run(
                    [visudo_bin, "-cf", str(sudoers_temp)],
                    capture_output=True,
                    text=True,
                )
                if validation.returncode != 0:
                    error_text = validation.stderr.strip() or validation.stdout.strip()
                    return [
                        ServiceResult(
                            False,
                            tr(
                                "Failed to validate sudoers file {path}: {error}",
                                path=sudoers_temp,
                                error=error_text,
                            ),
                            validation.returncode,
                        )
                    ]

            install_steps = [
                (
                    helper_temp,
                    paths.privileged_helper_file(),
                    "0755",
                ),
                (
                    sudoers_temp,
                    paths.privileged_sudoers_file(),
                    "0440",
                ),
            ]
            for source, dest, mode in install_steps:
                install_result = subprocess.run(
                    [
                        "sudo",
                        _resolve_install_binary(),
                        "-D",
                        "-o",
                        "root",
                        "-g",
                        "root",
                        "-m",
                        mode,
                        str(source),
                        str(dest),
                    ],
                    capture_output=True,
                    text=True,
                )
                if install_result.returncode != 0:
                    return [
                        ServiceResult(
                            False,
                            tr(
                                "Failed to install {name}: {error}",
                                name=dest.name,
                                error=install_result.stderr.strip(),
                            ),
                            install_result.returncode,
                        )
                    ]
                results.append(
                    ServiceResult(
                        True,
                        tr("Installed {name} to {path}", name=dest.name, path=dest.parent),
                    )
                )
    except Exception as e:
        return [
            ServiceResult(
                False,
                tr("Secure privileged control install failed: {error}", error=e),
                1,
            )
        ]

    return results


def install_systemd_unit_file(source: Path, destination: Path) -> ServiceResult:
    """Install a rendered systemd unit file with standard interactive sudo."""
    command = [
        "sudo",
        _resolve_install_binary(),
        "-D",
        "-o",
        "root",
        "-g",
        "root",
        "-m",
        "0644",
        str(source),
        str(destination),
    ]

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode == 0:
        return ServiceResult(
            True,
            tr("Installed {name} to {path}", name=destination.name, path=destination.parent),
        )

    stderr = result.stderr.strip()
    if _looks_like_sudo_auth_error(stderr):
        return ServiceResult(
            False,
            _(
                "Secure privileged control is not configured yet. "
                "Install/update the bot service or re-run install/repair "
                "from the TUI to install the secure sudo helper."
            ),
            result.returncode,
        )

    return ServiceResult(
        False,
        tr("Failed to install {name}: {error}", name=destination.name, error=stderr),
        result.returncode,
    )


def render_restart_timer_unit(on_calendar: str | list[str]) -> str:
    """Render the restart timer unit with one or more OnCalendar entries."""
    on_calendar_entries = normalize_on_calendar_entries(on_calendar)
    if not on_calendar_entries:
        on_calendar_entries = [normalize_on_calendar("*-*-* 06:00:00")]

    env = _template_environment()
    return env.get_template("armareforger-restart.timer.j2").render(
        on_calendar_entries=on_calendar_entries,
    )


def update_restart_timer_schedule(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    on_calendar: str | list[str] = "*-*-* 06:00:00",
) -> list[ServiceResult]:
    """Update only the restart timer schedule for an existing instance."""
    results: list[ServiceResult] = []
    timer_name = timer_unit_name(instance)
    timer_path = paths.SYSTEMD_DIR / timer_name
    schedule_entries = normalize_on_calendar_entries(on_calendar)
    if not schedule_entries:
        return [ServiceResult(False, _("At least one restart time is required."), 1)]

    try:
        if has_privileged_systemctl_channel():
            command = [
                "sudo",
                "-n",
                str(paths.privileged_helper_file()),
                "update-timer",
                timer_name,
                *schedule_entries,
            ]
            update_result = subprocess.run(command, capture_output=True, text=True)
            if update_result.returncode != 0:
                stderr = update_result.stderr.strip() or update_result.stdout.strip()
                if _looks_like_sudo_auth_error(stderr):
                    return [
                        ServiceResult(
                            False,
                            _(
                                "Secure privileged control is not configured yet. "
                                "Install/update the bot service or re-run install/repair "
                                "from the TUI to install the secure sudo helper."
                            ),
                            update_result.returncode,
                        )
                    ]
                return [
                    ServiceResult(
                        False,
                        tr("Failed to install {name}: {error}", name=timer_name, error=stderr),
                        update_result.returncode,
                    )
                ]
            results.append(
                ServiceResult(
                    True,
                    tr("Installed {name} to {path}", name=timer_name, path=timer_path.parent),
                )
            )
        else:
            with tempfile.TemporaryDirectory() as tempd:
                temp_timer = Path(tempd) / timer_name
                temp_timer.write_text(
                    render_restart_timer_unit(schedule_entries),
                    encoding="utf-8",
                )
                install_result = install_systemd_unit_file(temp_timer, timer_path)
                if not install_result.success:
                    return [install_result]
                results.append(install_result)

        reload_result = daemon_reload()
        results.append(
            ServiceResult(
                reload_result.success,
                (
                    _("Systemd daemon reloaded")
                    if reload_result.success
                    else tr("Daemon reload failed: {message}", message=reload_result.message)
                ),
                reload_result.exit_code,
            )
        )

        timer_restart = _run_systemctl("restart", timer_name)
        results.append(timer_restart)
    except Exception as e:
        return [
            ServiceResult(
                False,
                tr("Restart timer schedule update failed: {error}", error=e),
                1,
            )
        ]

    return results


def start_service(service_name: str = "armareforger.service") -> ServiceResult:
    """Start the server service."""
    return _run_systemctl("start", service_name)


def stop_service(service_name: str = "armareforger.service") -> ServiceResult:
    """Stop the server service."""
    return _run_systemctl("stop", service_name)


def restart_service(service_name: str = "armareforger.service") -> ServiceResult:
    """Restart the server service."""
    return _run_systemctl("restart", service_name)


def is_active(service_name: str = "armareforger.service") -> bool:
    """Check if the service is currently active."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", service_name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() == "active"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def is_enabled(service_name: str = "armareforger.service") -> bool:
    """Check if the service is enabled (starts on boot)."""
    try:
        result = subprocess.run(
            ["systemctl", "is-enabled", service_name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() == "enabled"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def get_service_status(service_name: str = "armareforger.service") -> dict[str, Any]:
    """Get detailed service status as a dict.

    Returns a structured dict suitable for both human display and JSON output.
    """
    active = is_active(service_name)
    enabled = is_enabled(service_name)

    # Get uptime / status line from systemctl
    description = ""
    active_state = "unknown"
    sub_state = "unknown"
    main_pid = 0
    exec_main_pid = 0
    control_pid = 0
    memory_current_bytes: int | None = None
    cpu_usage_nsec: int | None = None
    exec_main_start_usec: int | None = None
    active_enter_usec: int | None = None
    try:
        result = subprocess.run(
            [
                "systemctl",
                "show",
                service_name,
                "--property=ActiveState,SubState,Description,MainPID,"
                "ExecMainPID,ControlPID,MemoryCurrent,CPUUsageNSec,"
                "ExecMainStartTimestampMonotonic,ActiveEnterTimestampMonotonic",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in result.stdout.strip().splitlines():
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            if key == "Description":
                description = val
            elif key == "ActiveState":
                active_state = val
            elif key == "SubState":
                sub_state = val
            elif key == "MainPID":
                try:
                    main_pid = int(val)
                except ValueError:
                    pass
            elif key == "ExecMainPID":
                try:
                    exec_main_pid = int(val)
                except ValueError:
                    pass
            elif key == "ControlPID":
                try:
                    control_pid = int(val)
                except ValueError:
                    pass
            elif key == "MemoryCurrent":
                try:
                    parsed = int(val)
                    if 0 < parsed < 2**63:
                        memory_current_bytes = parsed
                except ValueError:
                    pass
            elif key == "CPUUsageNSec":
                try:
                    parsed = int(val)
                    if parsed >= 0:
                        cpu_usage_nsec = parsed
                except ValueError:
                    pass
            elif key == "ExecMainStartTimestampMonotonic":
                try:
                    parsed = int(val)
                    if parsed > 0:
                        exec_main_start_usec = parsed
                except ValueError:
                    pass
            elif key == "ActiveEnterTimestampMonotonic":
                try:
                    parsed = int(val)
                    if parsed > 0:
                        active_enter_usec = parsed
                except ValueError:
                    pass
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    resolved_pid = next(
        (pid for pid in (main_pid, exec_main_pid, control_pid) if pid > 0),
        0,
    )

    return {
        "service_name": service_name,
        "active": active,
        "enabled": enabled,
        "active_state": active_state,
        "sub_state": sub_state,
        "description": description,
        "main_pid": resolved_pid,
        "main_pid_raw": main_pid,
        "exec_main_pid": exec_main_pid,
        "control_pid": control_pid,
        "memory_current_bytes": memory_current_bytes,
        "cpu_usage_nsec": cpu_usage_nsec,
        "exec_main_start_usec": exec_main_start_usec,
        "active_enter_usec": active_enter_usec,
    }


def enable_service(service_name: str) -> ServiceResult:
    """Enable a systemd service."""
    return _run_systemctl("enable", service_name)


def disable_service(service_name: str) -> ServiceResult:
    """Disable a systemd service."""
    return _run_systemctl("disable", service_name)


def daemon_reload() -> ServiceResult:
    """Reload systemd manager configuration."""
    return _run_systemctl("daemon-reload", "", use_sudo=True)


def service_unit_name(instance: str = paths.DEFAULT_INSTANCE_NAME) -> str:
    """Return the main service unit name for an instance."""
    if instance != paths.DEFAULT_INSTANCE_NAME:
        return f"armareforger@{instance}.service"
    return paths.SERVICE_NAME


def restart_service_unit_name(instance: str = paths.DEFAULT_INSTANCE_NAME) -> str:
    """Return the helper restart service unit name for an instance."""
    if instance != paths.DEFAULT_INSTANCE_NAME:
        return f"armareforger-restart@{instance}.service"
    return paths.RESTART_SERVICE_NAME


def timer_unit_name(instance: str = paths.DEFAULT_INSTANCE_NAME) -> str:
    """Return the timer unit name for an instance."""
    if instance != paths.DEFAULT_INSTANCE_NAME:
        return f"armareforger-restart@{instance}.timer"
    return paths.TIMER_NAME


def normalize_on_calendar(on_calendar: str) -> str:
    """Normalize friendly time-only input into a full systemd OnCalendar value."""
    value = on_calendar.strip()
    if TIME_ONLY_RE.match(value):
        parts = value.split(":")
        hour = int(parts[0])
        minute = int(parts[1])
        second = int(parts[2]) if len(parts) == 3 else 0
        return f"*-*-* {hour:02d}:{minute:02d}:{second:02d}"
    return value


def normalize_on_calendar_entries(on_calendar: str | list[str]) -> list[str]:
    """Normalize one or more schedule entries into systemd OnCalendar expressions."""
    if isinstance(on_calendar, list):
        raw_entries = on_calendar
    else:
        value = on_calendar.strip()
        if not value:
            return []
        if "\n" in value:
            raw_entries = value.splitlines()
        elif ";" in value:
            raw_entries = value.split(";")
        elif "," in value:
            comma_entries = [entry.strip() for entry in value.split(",") if entry.strip()]
            if comma_entries and all(TIME_ONLY_RE.match(entry) for entry in comma_entries):
                raw_entries = comma_entries
            else:
                raw_entries = [value]
        elif " " in value:
            space_entries = [entry.strip() for entry in value.split() if entry.strip()]
            if len(space_entries) > 1 and all(TIME_ONLY_RE.match(entry) for entry in space_entries):
                raw_entries = space_entries
            else:
                raw_entries = [value]
        else:
            raw_entries = [value]

    normalized: list[str] = []
    seen: set[str] = set()
    for entry in raw_entries:
        cleaned = entry.strip()
        if not cleaned:
            continue
        normalized_entry = normalize_on_calendar(cleaned)
        if normalized_entry in seen:
            continue
        seen.add(normalized_entry)
        normalized.append(normalized_entry)
    return normalized


def format_schedule_for_input(schedule_entries: list[str]) -> str:
    """Convert stored OnCalendar entries into a friendly input string for TUI/CLI."""
    if not schedule_entries:
        return ""

    display_times: list[str] = []
    for entry in schedule_entries:
        match = DAILY_TIME_RE.fullmatch(entry.strip())
        if not match:
            return "; ".join(schedule_entries)
        time_value = match.group(1)
        if time_value.endswith(":00"):
            time_value = time_value[:-3]
        display_times.append(time_value)

    return ", ".join(display_times)


def _parse_systemctl_show(output: str) -> dict[str, str]:
    """Parse `systemctl show` KEY=VALUE output into a dictionary."""
    parsed: dict[str, str] = {}
    for line in output.strip().splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        parsed[key] = value.strip()
    return parsed


def _read_timer_schedule_entries(timer_path: Path) -> list[str]:
    """Read all OnCalendar entries from a timer unit file."""
    entries: list[str] = []
    try:
        for line in timer_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("OnCalendar="):
                value = line.split("=", 1)[1].strip()
                if value:
                    entries.append(value)
    except OSError:
        return []
    return entries


def get_timer_status(timer_name: str = paths.TIMER_NAME) -> dict[str, Any]:
    """Return structured timer state suitable for CLI and TUI display."""
    timer_path = paths.SYSTEMD_DIR / timer_name
    schedule_entries = _read_timer_schedule_entries(timer_path)
    status: dict[str, Any] = {
        "timer_name": timer_name,
        "exists": timer_path.is_file(),
        "active": False,
        "enabled": False,
        "active_state": "unknown",
        "sub_state": "unknown",
        "unit_file_state": "unknown",
        "description": "",
        "schedule_entries": schedule_entries,
        "schedule": format_schedule_for_input(schedule_entries),
        "next_run": "",
        "last_trigger": "",
    }
    try:
        result = subprocess.run(
            [
                "systemctl",
                "show",
                timer_name,
                "--property=ActiveState,SubState,Description,UnitFileState,"
                "NextElapseUSecRealtime,LastTriggerUSec,TimersCalendar",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return status

    if result.returncode != 0:
        return status

    parsed = _parse_systemctl_show(result.stdout)
    active_state = parsed.get("ActiveState", "unknown")
    unit_file_state = parsed.get("UnitFileState", "unknown")
    if not schedule_entries:
        raw_schedule = parsed.get("TimersCalendar", "").strip()
        if raw_schedule:
            schedule_entries = [raw_schedule]

    status.update(
        active=active_state == "active",
        enabled=unit_file_state.startswith("enabled"),
        active_state=active_state,
        sub_state=parsed.get("SubState", "unknown"),
        unit_file_state=unit_file_state,
        description=parsed.get("Description", ""),
        schedule_entries=schedule_entries,
        schedule=format_schedule_for_input(schedule_entries),
        next_run=parsed.get("NextElapseUSecRealtime", ""),
        last_trigger=parsed.get("LastTriggerUSec", ""),
    )
    return status


def generate_services(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    on_calendar: str | list[str] = "*-*-* 06:00:00",
) -> list[ServiceResult]:
    """Generate and install all systemd service and timer files for the given instance."""
    results = []

    # 1. Paths and Variables
    user = os.getenv("USER", "root")
    try:
        if user == "root" and os.getlogin():
            user = os.getlogin()
    except OSError:
        pass

    inst_root = paths.instance_root(instance)

    # Check if instance actually exists contextually
    if not inst_root.exists():
        inst_root.mkdir(parents=True, exist_ok=True)

    start_sh = paths.start_script(instance)

    service_name = service_unit_name(instance)
    restart_service_name = restart_service_unit_name(instance)
    timer_name = timer_unit_name(instance)
    on_calendar_entries = normalize_on_calendar_entries(on_calendar)
    if not on_calendar_entries:
        on_calendar_entries = [normalize_on_calendar("*-*-* 06:00:00")]

    service_path = paths.SYSTEMD_DIR / service_name
    restart_service_path = paths.SYSTEMD_DIR / restart_service_name
    timer_path = paths.SYSTEMD_DIR / timer_name

    project_root = Path(__file__).parent.parent.parent
    templates_dir = project_root / "templates"

    if not templates_dir.exists():
        return [
            ServiceResult(
                False,
                tr("Templates directory not found at {path}", path=templates_dir),
                1,
            )
        ]

    env = Environment(loader=FileSystemLoader(str(templates_dir)))

    # 2. Render templates
    try:
        start_sh_render = env.get_template("start-armareforger.sh.j2").render(
            instance_root=str(inst_root),
            max_fps=60,
        )
        service_render = env.get_template("armareforger.service.j2").render(
            user=user,
            instance_root=str(inst_root),
        )
        restart_service_render = (
            "[Unit]\n"
            f"Description=Restart Arma Reforger Dedicated Server ({instance})\n\n"
            "[Service]\n"
            "Type=oneshot\n"
            f"ExecStart=/usr/bin/systemctl restart {service_name}\n"
        )

        timer_render = render_restart_timer_unit(on_calendar_entries)

        # 3. Write start script (no sudo needed, it's in user's home)
        with open(start_sh, "w") as f:
            f.write(start_sh_render)
        start_sh.chmod(0o755)
        results.append(ServiceResult(True, tr("Generated {path}", path=start_sh)))

        # 4. Write systemd files to temp and sudo mv them
        with tempfile.TemporaryDirectory() as tempd:
            temp_dir = Path(tempd)

            tservice = temp_dir / service_name
            trestart = temp_dir / restart_service_name
            ttimer = temp_dir / timer_name

            with open(tservice, "w") as f:
                f.write(service_render)
            with open(trestart, "w") as f:
                f.write(restart_service_render)
            with open(ttimer, "w") as f:
                f.write(timer_render)

            for tmp_file, dest_file in [
                (tservice, service_path),
                (trestart, restart_service_path),
                (ttimer, timer_path),
            ]:
                results.append(install_systemd_unit_file(tmp_file, dest_file))

        dr_res = daemon_reload()
        results.append(
            ServiceResult(
                dr_res.success,
                (
                    _("Systemd daemon reloaded")
                    if dr_res.success
                    else tr("Daemon reload failed: {message}", message=dr_res.message)
                ),
            )
        )

        # Restart the timer to apply new schedule immediately
        tr_res = _run_systemctl("restart", timer_name)
        if tr_res.success:
            results.append(
                ServiceResult(
                    True,
                    tr("Timer {timer_name} restarted to apply schedule", timer_name=timer_name),
                )
            )

    except Exception as e:
        results.append(
            ServiceResult(False, tr("Service generation failed: {error}", error=e), 1)
        )

    return results

