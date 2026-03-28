"""Service manager - manage the Arma Reforger systemd service.

Uses service_name from state.json (default: armareforger.service).
All systemctl calls go through subprocess with proper error handling.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from armactl import paths
from armactl.i18n import _, tr

TIME_ONLY_RE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")
DAILY_TIME_RE = re.compile(r"^\*-\*-\* (\d{1,2}:\d{2}:\d{2})$")


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
    cmd = []
    if use_sudo:
        cmd.append("sudo")
    cmd.extend(["systemctl", action])
    if service_name:
        cmd.append(service_name)
    action_label = {
        "start": _("Systemctl action: start"),
        "stop": _("Systemctl action: stop"),
        "restart": _("Systemctl action: restart"),
        "enable": _("Systemctl action: enable"),
        "disable": _("Systemctl action: disable"),
        "daemon-reload": _("Systemctl action: daemon-reload"),
    }.get(action, action)

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
    main_pid = 0
    try:
        result = subprocess.run(
            ["systemctl", "show", service_name,
             "--property=ActiveState,SubState,Description,MainPID"],
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
            elif key == "MainPID":
                try:
                    main_pid = int(val)
                except ValueError:
                    pass
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    return {
        "service_name": service_name,
        "active": active,
        "enabled": enabled,
        "active_state": active_state,
        "description": description,
        "main_pid": main_pid,
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
        if value.count(":") == 1:
            value += ":00"
        return f"*-*-* {value}"
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

        timer_render = env.get_template("armareforger-restart.timer.j2").render(
            on_calendar_entries=on_calendar_entries,
        )

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
                cmd = ["sudo", "mv", str(tmp_file), str(dest_file)]
                ans = subprocess.run(cmd, capture_output=True, text=True)
                if ans.returncode != 0:
                    results.append(
                        ServiceResult(
                            False,
                            tr(
                                "Failed to install {name}: {error}",
                                name=dest_file.name,
                                error=ans.stderr.strip(),
                            ),
                            ans.returncode,
                        )
                    )
                else:
                    results.append(
                        ServiceResult(
                            True,
                            tr(
                                "Installed {name} to {path}",
                                name=dest_file.name,
                                path=dest_file.parent,
                            ),
                        )
                    )

        # Sudo chown
            subprocess.run(
                [
                    "sudo",
                    "chown",
                    "root:root",
                    str(service_path),
                    str(restart_service_path),
                    str(timer_path),
                ]
            )

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

