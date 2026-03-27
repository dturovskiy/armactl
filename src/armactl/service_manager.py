"""Service manager — manage the Arma Reforger systemd service.

Uses service_name from state.json (default: armareforger.service).
All systemctl calls go through subprocess with proper error handling.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from typing import Any


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
    service_name: str,
    use_sudo: bool = True,
) -> ServiceResult:
    """Run a systemctl command and return the result."""
    cmd = []
    if use_sudo:
        cmd.append("sudo")
    cmd.extend(["systemctl", action, service_name])

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
            message="systemctl not found — is systemd installed?",
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
