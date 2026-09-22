"""Read-only systemd status queries and parsers."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

RunCommand = Callable[..., subprocess.CompletedProcess[str]]
StateProbe = Callable[[str], bool]

SYSTEMD_EXEC_MAIN_CODE_LABELS = {
    "0": "none",
    "1": "exited",
    "2": "killed",
    "3": "dumped",
    "4": "trapped",
    "5": "stopped",
    "6": "continued",
}


def parse_systemctl_show(output: str) -> dict[str, str]:
    """Parse ``systemctl show`` KEY=VALUE output into a dictionary."""
    parsed: dict[str, str] = {}
    for line in output.strip().splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        parsed[key] = value.strip()
    return parsed


def is_active(service_name: str, *, run: RunCommand) -> bool:
    """Return whether systemd reports the unit as active."""
    try:
        result = run(
            ["systemctl", "is-active", service_name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() == "active"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def is_enabled(service_name: str, *, run: RunCommand) -> bool:
    """Return whether systemd reports the unit as enabled."""
    try:
        result = run(
            ["systemctl", "is-enabled", service_name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() == "enabled"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def get_systemd_unit_status(
    unit_name: str,
    *,
    unit_path: Path,
    run: RunCommand,
) -> dict[str, Any]:
    """Return bounded read-only status for one generated systemd unit."""
    try:
        exists = unit_path.is_file()
    except OSError:
        exists = False

    status: dict[str, Any] = {
        "unit_name": unit_name,
        "exists": exists,
        "load_state": "unknown" if exists else "not-found",
        "active": False,
        "enabled": False,
        "failed": False,
        "active_state": "unknown" if exists else "missing",
        "sub_state": "unknown" if exists else "missing",
        "unit_file_state": "unknown" if exists else "missing",
        "result": "",
        "exec_main_code": "",
        "exec_main_status": None,
        "last_exit_at": "",
        "next_trigger": "",
        "next_trigger_kind": "",
        "last_trigger": "",
    }
    if not exists:
        return status

    properties = (
        "LoadState,ActiveState,SubState,UnitFileState,Result,ExecMainCode,"
        "ExecMainStatus,ExecMainExitTimestamp,NextElapseUSecRealtime,"
        "NextElapseUSecMonotonic,LastTriggerUSec"
    )
    try:
        result = run(
            ["systemctl", "show", unit_name, f"--property={properties}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return status

    for key, value in parse_systemctl_show(result.stdout).items():
        if key == "LoadState":
            status["load_state"] = value or "unknown"
        elif key == "ActiveState":
            status["active_state"] = value or "unknown"
        elif key == "SubState":
            status["sub_state"] = value or "unknown"
        elif key == "UnitFileState":
            status["unit_file_state"] = value or "unknown"
        elif key == "Result":
            status["result"] = "" if value == "n/a" else value
        elif key == "ExecMainCode":
            status["exec_main_code"] = (
                "" if value == "n/a" else SYSTEMD_EXEC_MAIN_CODE_LABELS.get(value, value)
            )
        elif key == "ExecMainStatus":
            try:
                status["exec_main_status"] = int(value)
            except ValueError:
                status["exec_main_status"] = None
        elif key == "ExecMainExitTimestamp":
            status["last_exit_at"] = "" if value == "n/a" else value
        elif key == "NextElapseUSecRealtime":
            status["next_trigger"] = "" if value in {"", "n/a"} else value
            if status["next_trigger"]:
                status["next_trigger_kind"] = "realtime"
        elif key == "NextElapseUSecMonotonic":
            if value not in {"", "n/a"} and not status["next_trigger"]:
                status["next_trigger_kind"] = "monotonic"
        elif key == "LastTriggerUSec":
            status["last_trigger"] = "" if value == "n/a" else value

    active_state = str(status["active_state"])
    unit_file_state = str(status["unit_file_state"])
    result_state = str(status["result"])
    status["active"] = active_state == "active"
    status["enabled"] = unit_file_state in {
        "enabled",
        "enabled-runtime",
        "linked",
        "linked-runtime",
        "alias",
    }
    status["failed"] = active_state == "failed" or result_state not in {"", "success"}
    return status


def get_service_status(
    service_name: str,
    *,
    run: RunCommand,
    active_probe: StateProbe,
    enabled_probe: StateProbe,
) -> dict[str, Any]:
    """Return detailed read-only status for one service."""
    active = active_probe(service_name)
    enabled = enabled_probe(service_name)
    fields: dict[str, Any] = {
        "active_state": "unknown",
        "sub_state": "unknown",
        "description": "",
        "user": "",
        "main_pid_raw": 0,
        "exec_main_pid": 0,
        "control_pid": 0,
        "memory_current_bytes": None,
        "cpu_usage_nsec": None,
        "exec_main_start_usec": None,
        "active_enter_usec": None,
        "n_restarts": 0,
        "result": "",
        "exec_main_code": "",
        "exec_main_status": None,
    }
    properties = (
        "ActiveState,SubState,Description,User,MainPID,ExecMainPID,ControlPID,"
        "MemoryCurrent,CPUUsageNSec,ExecMainStartTimestampMonotonic,"
        "ActiveEnterTimestampMonotonic,NRestarts,Result,ExecMainCode,ExecMainStatus"
    )
    try:
        result = run(
            ["systemctl", "show", service_name, f"--property={properties}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        parsed = parse_systemctl_show(result.stdout)
        fields["description"] = parsed.get("Description", "")
        fields["active_state"] = parsed.get("ActiveState", "unknown")
        fields["sub_state"] = parsed.get("SubState", "unknown")
        fields["user"] = parsed.get("User", "")
        for key, target in (
            ("MainPID", "main_pid_raw"),
            ("ExecMainPID", "exec_main_pid"),
            ("ControlPID", "control_pid"),
        ):
            try:
                fields[target] = int(parsed.get(key, "0"))
            except ValueError:
                pass
        for key, target, allow_zero in (
            ("MemoryCurrent", "memory_current_bytes", True),
            ("CPUUsageNSec", "cpu_usage_nsec", True),
            ("ExecMainStartTimestampMonotonic", "exec_main_start_usec", False),
            ("ActiveEnterTimestampMonotonic", "active_enter_usec", False),
        ):
            try:
                value = int(parsed.get(key, ""))
                if (allow_zero and value >= 0) or (not allow_zero and value > 0):
                    if target == "memory_current_bytes" and value >= 2**63:
                        continue
                    fields[target] = value
            except ValueError:
                pass
        try:
            fields["n_restarts"] = max(int(parsed.get("NRestarts", "0")), 0)
        except ValueError:
            pass
        result_state = parsed.get("Result", "")
        fields["result"] = "" if result_state == "n/a" else result_state
        exec_main_code = parsed.get("ExecMainCode", "")
        fields["exec_main_code"] = "" if exec_main_code == "n/a" else exec_main_code
        try:
            fields["exec_main_status"] = int(parsed.get("ExecMainStatus", ""))
        except ValueError:
            pass
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    resolved_pid = next(
        (
            pid
            for pid in (
                fields["main_pid_raw"],
                fields["exec_main_pid"],
                fields["control_pid"],
            )
            if isinstance(pid, int) and pid > 0
        ),
        0,
    )
    return {
        "service_name": service_name,
        "active": active,
        "enabled": enabled,
        "main_pid": resolved_pid,
        **fields,
    }
