"""Service manager - manage the Arma Reforger systemd service.

Uses service_name from state.json (default: armareforger.service).
All systemctl calls go through subprocess with proper error handling.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from armactl import paths


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
                message=f"{action} {service_name}: ok",
                exit_code=0,
            )
        else:
            stderr = result.stderr.strip()
            return ServiceResult(
                success=False,
                message=f"{action} {service_name} failed: {stderr}",
                exit_code=result.returncode,
            )
    except subprocess.TimeoutExpired:
        return ServiceResult(
            success=False,
            message=f"{action} {service_name}: timed out after 30s",
            exit_code=1,
        )
    except FileNotFoundError:
        return ServiceResult(
            success=False,
            message="systemctl not found - is systemd installed?",
            exit_code=1,
        )
    except OSError as e:
        return ServiceResult(
            success=False,
            message=f"{action} {service_name}: {e}",
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


def generate_services(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    on_calendar: str = "*-*-* 06:00:00",
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

    service_name = (
        f"armareforger@{instance}.service"
        if instance != "default"
        else paths.SERVICE_NAME
    )
    restart_service_name = (
        f"armareforger-restart@{instance}.service"
        if instance != "default"
        else paths.RESTART_SERVICE_NAME
    )
    timer_name = (
        f"armareforger-restart@{instance}.timer"
        if instance != "default"
        else paths.TIMER_NAME
    )

    service_path = paths.SYSTEMD_DIR / service_name
    restart_service_path = paths.SYSTEMD_DIR / restart_service_name
    timer_path = paths.SYSTEMD_DIR / timer_name

    project_root = Path(__file__).parent.parent.parent
    templates_dir = project_root / "templates"

    if not templates_dir.exists():
        return [ServiceResult(False, f"Templates directory not found at {templates_dir}", 1)]

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
            on_calendar=on_calendar,
        )

        # 3. Write start script (no sudo needed, it's in user's home)
        with open(start_sh, "w") as f:
            f.write(start_sh_render)
        start_sh.chmod(0o755)
        results.append(ServiceResult(True, f"Generated {start_sh}"))

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
                            f"Failed to install {dest_file.name}: {ans.stderr.strip()}",
                            ans.returncode,
                        )
                    )
                else:
                    results.append(
                        ServiceResult(
                            True,
                            f"Installed {dest_file.name} to {dest_file.parent}",
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
                    "Systemd daemon reloaded"
                    if dr_res.success
                    else f"Daemon reload failed: {dr_res.message}"
                ),
            )
        )

        # Restart the timer to apply new schedule immediately
        tr_res = _run_systemctl("restart", timer_name)
        if tr_res.success:
            results.append(
                ServiceResult(True, f"Timer {timer_name} restarted to apply schedule")
            )

    except Exception as e:
        results.append(ServiceResult(False, f"Service generation failed: {e}", 1))

    return results

