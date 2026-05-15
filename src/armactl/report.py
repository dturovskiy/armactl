"""Diagnostic report collection for support/debugging output."""

from __future__ import annotations

import json
import os
import platform
import subprocess
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from armactl import __version__, paths
from armactl.discovery import discover
from armactl.redaction import redact_sensitive_text
from armactl.service_manager import (
    get_service_status,
    get_timer_status,
    service_unit_name,
    timer_unit_name,
)

CommandRunner = Callable[[list[str], int], str]


SENSITIVE_FILE_NAMES = {".env", "config.json"}
FPS_LINE_MARKERS = ("FPS:", "frame time")


def _section(title: str, body: str) -> str:
    """Render a report section with a stable plain-text header."""
    clean_body = redact_sensitive_text(body).rstrip()
    if not clean_body:
        clean_body = "(no output)"
    return f"== {title} ==\n{clean_body}\n"


def _run_command(cmd: list[str], timeout: int = 10) -> str:
    """Run a read-only diagnostic command and return redacted output."""
    try:
        result = subprocess.run(  # noqa: S603 - diagnostic command argv is fixed by caller
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return f"$ {' '.join(cmd)}\ncommand not found"
    except subprocess.TimeoutExpired:
        return f"$ {' '.join(cmd)}\ntimed out after {timeout}s"
    except OSError as error:
        return f"$ {' '.join(cmd)}\nfailed: {error}"

    output_parts = [f"$ {' '.join(cmd)}"]
    if result.stdout.strip():
        output_parts.append(result.stdout.rstrip())
    if result.stderr.strip():
        output_parts.append("stderr:")
        output_parts.append(result.stderr.rstrip())
    if result.returncode != 0:
        output_parts.append(f"exit_code: {result.returncode}")
    return redact_sensitive_text("\n".join(output_parts))


def _read_text_file(path: Path, *, max_bytes: int = 64_000) -> str:
    """Read a small diagnostic text file safely."""
    if not path.exists():
        return f"{path}: missing"
    if not path.is_file():
        return f"{path}: not a file"
    try:
        data = path.read_bytes()
    except OSError as error:
        return f"{path}: failed to read: {error}"

    truncated = len(data) > max_bytes
    if truncated:
        data = data[-max_bytes:]
    text = data.decode("utf-8", errors="replace")
    if truncated:
        text = f"... truncated to last {max_bytes} bytes ...\n{text}"
    return redact_sensitive_text(text)


def _file_exists_line(label: str, path: Path) -> str:
    """Return a compact file presence line."""
    try:
        exists = path.exists()
        is_file = path.is_file()
    except OSError as error:
        return f"{label}: {path} (error: {error})"
    status = "file" if is_file else "exists" if exists else "missing"
    return f"{label}: {path} ({status})"


def _latest_console_log(config_dir: Path) -> Path | None:
    """Return the newest runtime console.log under the profile logs directory."""
    logs_dir = config_dir / "logs"
    try:
        candidates = list(logs_dir.glob("*/console.log"))
    except OSError:
        return None
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _tail_matching_lines(path: Path, markers: tuple[str, ...], limit: int = 10) -> str:
    """Return the last lines containing any marker from a text file."""
    if not path.exists():
        return f"{path}: missing"
    matches: list[str] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if any(marker in line for marker in markers):
                    matches.append(line.rstrip())
    except OSError as error:
        return f"{path}: failed to read: {error}"
    if not matches:
        return f"{path}: no matching lines"
    return "\n".join(matches[-limit:])


def _json_block(value: object) -> str:
    """Render a JSON block with secret redaction."""
    return redact_sensitive_text(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def build_report(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    lines: int = 120,
    include_journal: bool = True,
    command_runner: CommandRunner = _run_command,
) -> str:
    """Build a copy-paste friendly diagnostic report for an armactl instance."""
    lines = max(int(lines), 1)
    sections: list[str] = []
    project_root = paths.project_root()
    instance_root = paths.instance_root(instance)
    config_dir = paths.config_dir(instance)
    config_file = paths.config_file(instance)
    start_script = paths.start_script(instance)
    server_binary = paths.server_binary(instance)
    bot_env_file = paths.bot_env_file(instance)
    state_file = paths.state_file(instance)
    service_name = service_unit_name(instance)
    timer_name = timer_unit_name(instance)
    bot_service_name = paths.BOT_SERVICE_NAME

    sections.append(
        _section(
            "armactl report",
            "\n".join(
                [
                    f"timestamp_utc: {datetime.now(timezone.utc).isoformat()}",
                    f"armactl_version: {__version__}",
                    f"instance: {instance}",
                    f"python: {platform.python_version()}",
                    f"platform: {platform.platform()}",
                    f"cwd: {Path.cwd()}",
                    f"project_root: {project_root}",
                    f"user: {os.environ.get('USER', 'unknown')}",
                ]
            ),
        )
    )

    sections.append(
        _section(
            "git",
            "\n".join(
                [
                    command_runner(["git", "-C", str(project_root), "branch", "--show-current"], 5),
                    command_runner(
                        ["git", "-C", str(project_root), "rev-parse", "--short", "HEAD"],
                        5,
                    ),
                    command_runner(["git", "-C", str(project_root), "status", "--short"], 5),
                ]
            ),
        )
    )

    try:
        state = discover(instance=instance, save=False)
        sections.append(_section("discovery", _json_block(state.to_dict())))
    except Exception as error:  # noqa: BLE001 - reports should not fail on diagnostics
        state = None
        sections.append(_section("discovery", f"failed: {error}"))

    try:
        service_status = get_service_status(service_name)
    except Exception as error:  # noqa: BLE001
        service_status = {"error": str(error)}
    try:
        timer_status = get_timer_status(timer_name)
    except Exception as error:  # noqa: BLE001
        timer_status = {"error": str(error)}

    sections.append(
        _section(
            "runtime paths",
            "\n".join(
                [
                    f"instance_root: {instance_root}",
                    _file_exists_line("server_binary", server_binary),
                    _file_exists_line("config_file", config_file),
                    _file_exists_line("state_file", state_file),
                    _file_exists_line("start_script", start_script),
                    _file_exists_line("bot_env", bot_env_file),
                ]
            ),
        )
    )

    sections.append(_section("service status dict", _json_block(service_status)))
    sections.append(_section("timer status dict", _json_block(timer_status)))
    sections.append(_section("process", command_runner(["pgrep", "-af", "ArmaReforgerServer"], 5)))
    sections.append(_section("start script", _read_text_file(start_script)))

    latest_log = _latest_console_log(config_dir)
    fps_body = [f"latest_console_log: {latest_log or '(none)'}"]
    if latest_log is not None:
        fps_body.append(_tail_matching_lines(latest_log, FPS_LINE_MARKERS, limit=10))
    sections.append(_section("server FPS telemetry", "\n".join(fps_body)))

    service_names = [service_name, timer_name, bot_service_name]
    sections.append(
        _section(
            "systemctl status",
            "\n\n".join(
                command_runner(["systemctl", "status", name, "--no-pager"], 10)
                for name in service_names
            ),
        )
    )

    if include_journal:
        sections.append(
            _section(
                "journalctl server",
                command_runner(
                    ["journalctl", "-u", service_name, "-n", str(lines), "--no-pager"],
                    15,
                ),
            )
        )
        sections.append(
            _section(
                "journalctl bot",
                command_runner(
                    ["journalctl", "-u", bot_service_name, "-n", str(lines), "--no-pager"],
                    15,
                ),
            )
        )

    port_patterns: list[str] = []
    if state is not None:
        for port in (state.ports.game, state.ports.a2s, state.ports.rcon):
            if port:
                port_patterns.append(f":{port}")
    if port_patterns:
        sections.append(
            _section(
                "listening UDP ports",
                "\n".join(
                    [
                        command_runner(["ss", "-lunp"], 10),
                        f"expected_port_markers: {', '.join(port_patterns)}",
                    ]
                ),
            )
        )
    else:
        sections.append(_section("listening UDP ports", command_runner(["ss", "-lunp"], 10)))

    return "\n".join(sections).rstrip() + "\n"
