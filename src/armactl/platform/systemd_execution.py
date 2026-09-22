"""Construction and execution of bounded systemctl commands."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from armactl.i18n import _, tr
from armactl.redaction import redact_sensitive_text, safe_subprocess_error

RunCommand = Callable[..., subprocess.CompletedProcess[str]]
WhichCommand = Callable[[str], str | None]
MessageFactory = Callable[[], str]

SUDO_AUTH_ERROR_MARKERS = (
    "a terminal is required to read the password",
    "a password is required",
)
SYSTEMCTL_TIMEOUT_SECONDS = 30


@dataclass
class ServiceResult:
    """Result of a systemctl operation."""

    success: bool
    message: str
    exit_code: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "message": self.message,
            "exit_code": self.exit_code,
        }


def secure_privileged_channel_message() -> str:
    """Return guidance for missing or stale sudo-helper access."""
    return _(
        "Secure privileged control is not configured for this Linux user yet. "
        "Install/update the bot service or re-run install/repair from the TUI "
        "to refresh the secure sudo helper."
    )


def looks_like_sudo_auth_error(stderr: str) -> bool:
    """Detect sudo failures caused by non-interactive password prompts."""
    lowered = stderr.lower()
    return any(marker in lowered for marker in SUDO_AUTH_ERROR_MARKERS)


def resolve_systemctl_binary(*, which: WhichCommand) -> str:
    """Return the resolved systemctl binary or its standard absolute path."""
    return which("systemctl") or "/usr/bin/systemctl"


def build_systemctl_command(
    action: str,
    service_name: str | None = None,
    *,
    use_sudo: bool,
    systemctl_binary: str,
    privileged_helper: Path | None,
    stdin_isatty: bool,
) -> list[str]:
    """Build a systemctl or narrow privileged-helper invocation."""
    if not use_sudo:
        command = [systemctl_binary]
    elif privileged_helper is not None:
        command = ["sudo", "-n", str(privileged_helper), action]
        if service_name:
            command.append(service_name)
        return command
    else:
        command = ["sudo"]
        if not stdin_isatty:
            command.append("-n")
        command.append(systemctl_binary)

    if action == "clean-timer-state":
        command.extend(["clean", "--what=state"])
    else:
        command.append(action)
    if service_name:
        command.append(service_name)
    return command


def execute_systemctl_command(
    action: str,
    service_name: str | None,
    *,
    command: list[str],
    use_sudo: bool,
    timeout_seconds: int,
    run: RunCommand,
    privileged_channel_message: MessageFactory = secure_privileged_channel_message,
) -> ServiceResult:
    """Execute one prepared systemctl command with bounded, redacted errors."""
    action_label = {
        "start": _("Systemctl action: start"),
        "stop": _("Systemctl action: stop"),
        "restart": _("Systemctl action: restart"),
        "enable": _("Systemctl action: enable"),
        "disable": _("Systemctl action: disable"),
        "daemon-reload": _("Systemctl action: daemon-reload"),
        "clean-timer-state": _("Systemctl action: clear timer state"),
    }.get(action, action)

    try:
        result = run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
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

        stderr = safe_subprocess_error(result.stderr, result.stdout)
        if use_sudo and looks_like_sudo_auth_error(stderr):
            return ServiceResult(
                success=False,
                message=privileged_channel_message(),
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
                "{action} {service_name}: timed out after {timeout_seconds}s",
                action=action_label,
                service_name=service_name,
                timeout_seconds=timeout_seconds,
            ),
            exit_code=1,
        )
    except FileNotFoundError:
        return ServiceResult(
            success=False,
            message=_("systemctl not found - is systemd installed?"),
            exit_code=1,
        )
    except OSError as error:
        return ServiceResult(
            success=False,
            message=tr(
                "{action} {service_name}: {error}",
                action=action_label,
                service_name=service_name,
                error=redact_sensitive_text(error),
            ),
            exit_code=1,
        )
