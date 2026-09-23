"""Narrow privileged operations for generated systemd files and timers."""

from __future__ import annotations

import re
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

from armactl.i18n import tr
from armactl.platform.systemd_execution import (
    ServiceResult,
    looks_like_sudo_auth_error,
    secure_privileged_channel_message,
)
from armactl.redaction import redact_sensitive_text, safe_subprocess_error

RunCommand = Callable[..., subprocess.CompletedProcess[str]]
WhichCommand = Callable[[str], str | None]
MessageFactory = Callable[[], str]

SUDOERS_USER_RE = re.compile(r"^\s*([A-Za-z0-9._-]+)\s+ALL=\(root\)\s+NOPASSWD:")


def resolve_install_binary(*, which: WhichCommand) -> str:
    """Return the resolved install binary or its standard absolute path."""
    return which("install") or "/usr/bin/install"


def resolve_helper_python_binary(
    *,
    which: WhichCommand,
    current_executable: str,
) -> str:
    """Return the Python interpreter used to validate the privileged helper."""
    return which("python3") or current_executable or "/usr/bin/python3"


def has_privileged_channel(helper_path: Path, sudoers_path: Path) -> bool:
    """Return whether both sides of the narrow privileged channel exist."""
    return helper_path.is_file() and sudoers_path.is_file()


def get_privileged_channel_user(sudoers_path: Path) -> str | None:
    """Read the Linux user granted access by one armactl sudoers drop-in."""
    try:
        if not sudoers_path.is_file():
            return None
        for raw_line in sudoers_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            match = SUDOERS_USER_RE.match(line)
            if match:
                return match.group(1)
    except OSError:
        return None
    return None


def install_root_owned_file(
    source: Path,
    destination: Path,
    *,
    mode: str,
    install_binary: str,
    run: RunCommand,
    privileged_channel_message: MessageFactory = secure_privileged_channel_message,
) -> ServiceResult:
    """Install one generated root-owned file through standard sudo."""
    command = [
        "sudo",
        install_binary,
        "-D",
        "-o",
        "root",
        "-g",
        "root",
        "-m",
        mode,
        str(source),
        str(destination),
    ]
    result = run(command, capture_output=True, text=True)
    if result.returncode == 0:
        return ServiceResult(
            True,
            tr(
                "Installed {name} to {path}",
                name=destination.name,
                path=destination.parent,
            ),
        )

    error = safe_subprocess_error(result.stderr, result.stdout)
    if looks_like_sudo_auth_error(error):
        return ServiceResult(False, privileged_channel_message(), result.returncode)
    return ServiceResult(
        False,
        tr("Failed to install {name}: {error}", name=destination.name, error=error),
        result.returncode,
    )


def install_privileged_channel(
    *,
    helper_text: str,
    sudoers_text: str,
    helper_name: str,
    helper_path: Path,
    sudoers_path: Path,
    python_binary: str,
    visudo_binary: str,
    install_binary: str,
    run: RunCommand,
) -> list[ServiceResult]:
    """Validate and install the narrow helper and its sudoers drop-in."""
    results: list[ServiceResult] = []
    try:
        with tempfile.TemporaryDirectory() as tempd:
            temp_dir = Path(tempd)
            helper_temp = temp_dir / helper_name
            sudoers_temp = temp_dir / f"{helper_name}.sudoers"
            helper_temp.write_text(helper_text, encoding="utf-8")
            sudoers_temp.write_text(sudoers_text, encoding="utf-8")

            if Path(python_binary).exists():
                validation = run(
                    [python_binary, "-m", "py_compile", str(helper_temp)],
                    capture_output=True,
                    text=True,
                )
                if validation.returncode != 0:
                    return [
                        ServiceResult(
                            False,
                            tr(
                                "Failed to validate privileged helper {path}: {error}",
                                path=helper_temp,
                                error=safe_subprocess_error(
                                    validation.stderr,
                                    validation.stdout,
                                ),
                            ),
                            validation.returncode,
                        )
                    ]

            if Path(visudo_binary).exists():
                validation = run(
                    [visudo_binary, "-cf", str(sudoers_temp)],
                    capture_output=True,
                    text=True,
                )
                if validation.returncode != 0:
                    return [
                        ServiceResult(
                            False,
                            tr(
                                "Failed to validate sudoers file {path}: {error}",
                                path=sudoers_temp,
                                error=safe_subprocess_error(
                                    validation.stderr,
                                    validation.stdout,
                                ),
                            ),
                            validation.returncode,
                        )
                    ]

            for source, destination, mode in (
                (helper_temp, helper_path, "0755"),
                (sudoers_temp, sudoers_path, "0440"),
            ):
                result = run(
                    [
                        "sudo",
                        install_binary,
                        "-D",
                        "-o",
                        "root",
                        "-g",
                        "root",
                        "-m",
                        mode,
                        str(source),
                        str(destination),
                    ],
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    return [
                        ServiceResult(
                            False,
                            tr(
                                "Failed to install {name}: {error}",
                                name=destination.name,
                                error=safe_subprocess_error(
                                    result.stderr,
                                    result.stdout,
                                ),
                            ),
                            result.returncode,
                        )
                    ]
                results.append(
                    ServiceResult(
                        True,
                        tr(
                            "Installed {name} to {path}",
                            name=destination.name,
                            path=destination.parent,
                        ),
                    )
                )
    except Exception as error:
        return [
            ServiceResult(
                False,
                tr(
                    "Secure privileged control install failed: {error}",
                    error=redact_sensitive_text(error),
                ),
                1,
            )
        ]
    return results


def update_timer_with_helper(
    *,
    helper_path: Path,
    timer_name: str,
    schedule_entries: Sequence[str],
    timer_directory: Path,
    run: RunCommand,
    privileged_channel_message: MessageFactory = secure_privileged_channel_message,
) -> ServiceResult:
    """Replace one generated restart timer through the narrow helper."""
    command = [
        "sudo",
        "-n",
        str(helper_path),
        "update-timer",
        timer_name,
        *schedule_entries,
    ]
    result = run(command, capture_output=True, text=True)
    if result.returncode == 0:
        return ServiceResult(
            True,
            tr("Installed {name} to {path}", name=timer_name, path=timer_directory),
        )

    error = safe_subprocess_error(result.stderr, result.stdout)
    if looks_like_sudo_auth_error(error):
        return ServiceResult(False, privileged_channel_message(), result.returncode)
    return ServiceResult(
        False,
        tr("Failed to install {name}: {error}", name=timer_name, error=error),
        result.returncode,
    )
