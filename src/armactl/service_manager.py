"""Service manager - manage the Arma Reforger systemd service.

Uses service_name from state.json (default: armareforger.service).
All systemctl calls go through subprocess with proper error handling.
"""

from __future__ import annotations

import getpass
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from armactl import paths
from armactl.i18n import _, tr
from armactl.redaction import redact_sensitive_text, safe_subprocess_error
from armactl.restart_timing import RESTART_TIMING
from armactl.runtime_settings import (
    RuntimeSettingsError,
    load_max_fps_profile,
    normalize_max_fps_profile,
    read_max_fps_status,
    save_max_fps_profile,
)

TIME_ONLY_RE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")
DAILY_TIME_RE = re.compile(r"^\*-\*-\* (\d{1,2}:\d{2}:\d{2})$")
SUDO_AUTH_ERROR_MARKERS = (
    "a terminal is required to read the password",
    "a password is required",
)
SUDOERS_USER_RE = re.compile(r"^\s*([A-Za-z0-9._-]+)\s+ALL=\(root\)\s+NOPASSWD:")
INSTANCE_SERVICE_RE = re.compile(r"^armareforger@([A-Za-z0-9_.-]+)\.service$")
RESTART_INSTANCE_SERVICE_RE = re.compile(
    r"^armareforger-restart@([A-Za-z0-9_.-]+)\.service$"
)
SYSTEMCTL_TIMEOUT_SECONDS = 30


@dataclass
class ServiceResult:
    """Result of a systemctl operation."""

    success: bool
    message: str
    exit_code: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"success": self.success, "message": self.message, "exit_code": self.exit_code}


def _secure_privileged_channel_message() -> str:
    """Return the user-facing guidance for missing or stale sudo-helper access."""
    return _(
        "Secure privileged control is not configured for this Linux user yet. "
        "Install/update the bot service or re-run install/repair from the TUI "
        "to refresh the secure sudo helper."
    )


def _run_systemctl(
    action: str,
    service_name: str | None = None,
    use_sudo: bool = True,
    timeout_seconds: int = SYSTEMCTL_TIMEOUT_SECONDS,
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
        else:
            stderr = safe_subprocess_error(result.stderr, result.stdout)
            if use_sudo and _looks_like_sudo_auth_error(stderr):
                return ServiceResult(
                    success=False,
                    message=_secure_privileged_channel_message(),
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
    except OSError as e:
        return ServiceResult(
            success=False,
            message=tr(
                "{action} {service_name}: {error}",
                action=action_label,
                service_name=service_name,
                error=redact_sensitive_text(e),
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


def get_privileged_channel_user() -> str | None:
    """Return the Linux user currently granted access to the secure helper."""
    sudoers_path = paths.privileged_sudoers_file()
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
    return resolve_linux_user()


def resolve_linux_user(default: str = "root") -> str:
    """Resolve the non-root Linux user armactl should target for services/helpers."""
    sudo_user = (os.getenv("SUDO_USER") or "").strip()
    if sudo_user and sudo_user != "root":
        return sudo_user

    try:
        login_user = (os.getlogin() or "").strip()
    except OSError:
        login_user = ""

    env_logname = (os.getenv("LOGNAME") or "").strip()
    env_user = (os.getenv("USER") or "").strip()
    try:
        getpass_user = (getpass.getuser() or "").strip()
    except Exception:
        getpass_user = ""

    for candidate in (login_user, env_logname, env_user, getpass_user):
        if candidate and candidate != "root":
            return candidate

    for candidate in (login_user, env_user, env_logname, getpass_user):
        if candidate:
            return candidate

    return default


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


def render_start_script(
    *,
    instance_root: Path | str,
    server_dir: Path | str,
    config_dir: Path | str,
    config_file: Path | str,
    log_stats_interval_ms: int = 10000,
    max_fps: int = 60,
) -> str:
    """Render the generated Arma Reforger launch script from the current template."""
    max_fps = normalize_max_fps_profile(max_fps)
    env = _template_environment()
    rendered = env.get_template("start-armareforger.sh.j2").render(
        instance_root=str(instance_root),
        server_dir=str(server_dir),
        config_dir=str(config_dir),
        config_file=str(config_file),
        python_executable=sys.executable,
        log_stats_interval_ms=log_stats_interval_ms,
        max_fps=max_fps,
    )
    return _normalize_generated_text(rendered)


def _runtime_settings_failure(error: object) -> ServiceResult:
    return ServiceResult(
        False,
        tr(
            "Runtime max FPS setting is invalid: {error}",
            error=redact_sensitive_text(error),
        ),
        1,
    )


def get_max_fps_profile_status(instance: str = paths.DEFAULT_INSTANCE_NAME):
    """Return the configured/generated max FPS status for one instance."""
    return read_max_fps_status(instance)


def _backup_generated_start_script(start_sh: Path, instance: str) -> bool:
    if not start_sh.is_file():
        return False
    backup_dir = paths.backups_dir(instance)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"{start_sh.name}.{stamp}.bak"
    counter = 1
    while backup_path.exists():
        backup_path = backup_dir / f"{start_sh.name}.{stamp}.{counter}.bak"
        counter += 1
    shutil.copy2(start_sh, backup_path)
    backup_path.chmod(0o600)
    return True


def _fsync_directory(directory: Path) -> None:
    """Best-effort fsync for a directory after publishing a file."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _replace_generated_start_script(
    start_sh: Path,
    rendered: str,
    *,
    instance: str,
    backup_existing: bool = True,
) -> None:
    start_sh.parent.mkdir(parents=True, exist_ok=True)
    if backup_existing:
        _backup_generated_start_script(start_sh, instance)
    temp_path = start_sh.with_name(f".{start_sh.name}.tmp")
    try:
        with temp_path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, start_sh)
        start_sh.chmod(0o755)
        _fsync_directory(start_sh.parent)
    except OSError:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

def _instance_from_service_name(service_name: str) -> str | None:
    if service_name in {paths.SERVICE_NAME, paths.RESTART_SERVICE_NAME}:
        return paths.DEFAULT_INSTANCE_NAME
    match = INSTANCE_SERVICE_RE.fullmatch(service_name) or RESTART_INSTANCE_SERVICE_RE.fullmatch(
        service_name
    )
    if not match:
        return None
    try:
        return paths.validate_instance_name(match.group(1))
    except paths.InvalidInstanceNameError:
        return None


def _restart_unit_for_game_service(service_name: str) -> str | None:
    if service_name == paths.SERVICE_NAME:
        return paths.RESTART_SERVICE_NAME

    match = INSTANCE_SERVICE_RE.fullmatch(service_name)
    if not match:
        return None

    try:
        instance = paths.validate_instance_name(match.group(1))
    except paths.InvalidInstanceNameError:
        return None
    return restart_service_unit_name(instance)


def _run_pre_start_guards(service_name: str) -> ServiceResult | None:
    instance = _instance_from_service_name(service_name)
    if instance is None:
        return None

    config_path = paths.config_file(instance)
    if not config_path.is_file():
        return None

    try:
        from armactl.sat_admin_guard import SatAdminGuardError, guard_sat_admin_config

        guard_sat_admin_config(config_path)
    except SatAdminGuardError as error:
        return ServiceResult(
            False,
            tr(
                "ServerAdminTools admin guard failed: {error}",
                error=redact_sensitive_text(error),
            ),
            1,
        )
    return None


def sync_generated_start_script(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> ServiceResult:
    """Refresh the per-instance generated start script without touching services.

    This is intentionally lighter than repair:
    - no SteamCMD validate
    - no service stop/start
    - no systemd unit installation
    - safe to run while the server is online

    The refreshed script takes effect on the next service restart.
    """
    try:
        server_dir = paths.validate_server_install_dir(
            paths.server_dir(instance),
            instance=instance,
        )
        instance_root = server_dir.parent
        config_dir = paths.config_dir(instance)
        config_file = paths.config_file(instance)
        start_sh = paths.start_script(instance)
        max_fps = load_max_fps_profile(instance)

        if not instance_root.exists():
            return ServiceResult(
                False,
                tr("Instance root not found: {path}", path=instance_root),
                1,
            )

        rendered = render_start_script(
            instance_root=instance_root,
            server_dir=server_dir,
            config_dir=config_dir,
            config_file=config_file,
            log_stats_interval_ms=10000,
            max_fps=max_fps,
        )

        try:
            current = start_sh.read_text(encoding="utf-8")
        except FileNotFoundError:
            current = ""
        except OSError as error:
            return ServiceResult(
                False,
                tr(
                    "Failed to read generated start script {path}: {error}",
                    path=start_sh,
                    error=redact_sensitive_text(error),
                ),
                1,
            )

        if current == rendered:
            try:
                start_sh.chmod(0o755)
            except OSError:
                pass
            return ServiceResult(
                True,
                tr("Generated start script is up to date: {path}", path=start_sh),
            )

        _replace_generated_start_script(
            start_sh,
            rendered,
            instance=instance,
            backup_existing=True,
        )

        return ServiceResult(
            True,
            tr(
                "Updated generated start script: {path}. "
                "Restart the server to apply launch changes.",
                path=start_sh,
            ),
        )
    except RuntimeSettingsError as error:
        return _runtime_settings_failure(error)
    except paths.UnsafeServerInstallDirError as error:
        return ServiceResult(False, str(error), 1)
    except OSError as error:
        return ServiceResult(
            False,
            tr(
                "Generated start script sync failed: {error}",
                error=redact_sensitive_text(error),
            ),
            1,
        )


def _restore_runtime_settings_file(
    settings_path: Path,
    *,
    existed: bool,
    previous_content: bytes,
    previous_mode: int | None,
) -> None:
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    if not existed:
        settings_path.unlink(missing_ok=True)
        _fsync_directory(settings_path.parent)
        return

    temp_path = settings_path.with_suffix(settings_path.suffix + ".rollback")
    try:
        with temp_path.open("wb") as handle:
            handle.write(previous_content)
            handle.flush()
            os.fsync(handle.fileno())
        if previous_mode is not None:
            temp_path.chmod(previous_mode)
        os.replace(temp_path, settings_path)
        if previous_mode is not None:
            settings_path.chmod(previous_mode)
        _fsync_directory(settings_path.parent)
    except OSError:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _snapshot_runtime_settings_file(settings_path: Path) -> tuple[bool, bytes, int | None]:
    try:
        stat_result = settings_path.stat()
        return True, settings_path.read_bytes(), stat_result.st_mode & 0o777
    except FileNotFoundError:
        return False, b"", None


def update_max_fps_profile(
    instance: str,
    max_fps: int | str,
) -> ServiceResult:
    """Persist max FPS and regenerate the generated start script safely."""
    settings_path = paths.runtime_settings_file(instance)
    try:
        profile = normalize_max_fps_profile(max_fps)
        existed, previous_content, previous_mode = _snapshot_runtime_settings_file(settings_path)
        save_max_fps_profile(instance, profile)
    except RuntimeSettingsError as error:
        return _runtime_settings_failure(error)
    except OSError:
        return ServiceResult(
            False,
            _("Runtime max FPS setting could not be saved."),
            1,
        )

    sync_result = sync_generated_start_script(instance)
    if not sync_result.success:
        try:
            _restore_runtime_settings_file(
                settings_path,
                existed=existed,
                previous_content=previous_content,
                previous_mode=previous_mode,
            )
        except OSError:
            return ServiceResult(
                False,
                _(
                    "Max FPS profile was not applied and rollback failed. "
                    "Service action was not run."
                ),
                1,
            )
        return ServiceResult(
            False,
            _(
                "Max FPS profile was not applied because generated launch script "
                "refresh failed. Service action was not run."
            ),
            sync_result.exit_code,
        )
    return ServiceResult(
        True,
        tr(
            "Max FPS profile set to {max_fps}. Generated launch script refreshed.",
            max_fps=profile,
        ),
        0,
    )

def _render_privileged_helper_script() -> str:
    """Render the root-owned helper script text."""
    env = _template_environment()
    rendered = env.get_template("armactl-systemctl-helper.py.j2").render(
        install_bin=_resolve_install_binary(),
        systemctl_bin=_resolve_systemctl_binary(),
    )
    return _normalize_generated_text(rendered)


def _render_safe_restart_helper_script() -> str:
    """Render the root-owned bounded restart helper script text."""
    env = _template_environment()
    rendered = env.get_template("armactl-safe-restart.py.j2").render(
        restart_timing=RESTART_TIMING,
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
            sudoers_temp = temp_dir / f"{paths.PRIVILEGED_HELPER_NAME}.sudoers"
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
                    error_text = safe_subprocess_error(validation.stderr, validation.stdout)
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
                    error_text = safe_subprocess_error(validation.stderr, validation.stdout)
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
                                error=safe_subprocess_error(
                                    install_result.stderr,
                                    install_result.stdout,
                                ),
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
                tr(
                    "Secure privileged control install failed: {error}",
                    error=redact_sensitive_text(e),
                ),
                1,
            )
        ]

    return results


def install_systemd_unit_file(
    source: Path,
    destination: Path,
    *,
    mode: str = "0644",
) -> ServiceResult:
    """Install a rendered root-owned systemd/helper file with standard sudo."""
    command = [
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
        str(destination),
    ]

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode == 0:
        return ServiceResult(
            True,
            tr("Installed {name} to {path}", name=destination.name, path=destination.parent),
        )

    stderr = safe_subprocess_error(result.stderr, result.stdout)
    if _looks_like_sudo_auth_error(stderr):
        return ServiceResult(
            False,
            _secure_privileged_channel_message(),
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
                stderr = safe_subprocess_error(update_result.stderr, update_result.stdout)
                if _looks_like_sudo_auth_error(stderr):
                    return [
                        ServiceResult(
                            False,
                            _secure_privileged_channel_message(),
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
                tr(
                    "Restart timer schedule update failed: {error}",
                    error=redact_sensitive_text(e),
                ),
                1,
            )
        ]

    return results


def start_service(service_name: str = "armareforger.service") -> ServiceResult:
    """Start the server service."""
    guard_result = _run_pre_start_guards(service_name)
    if guard_result is not None:
        return guard_result
    return _run_systemctl("start", service_name)


def stop_service(service_name: str = "armareforger.service") -> ServiceResult:
    """Stop the server service."""
    return _run_systemctl("stop", service_name)


def restart_service(
    service_name: str = "armareforger.service",
    *,
    timeout_seconds: int | None = None,
) -> ServiceResult:
    """Restart the server service."""
    guard_result = _run_pre_start_guards(service_name)
    if guard_result is not None:
        return guard_result
    restart_unit = _restart_unit_for_game_service(service_name)
    if restart_unit is not None:
        return _run_systemctl(
            "start",
            restart_unit,
            timeout_seconds=RESTART_TIMING.caller_timeout_seconds,
        )
    if timeout_seconds is None:
        return _run_systemctl("restart", service_name)
    return _run_systemctl("restart", service_name, timeout_seconds=timeout_seconds)


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
    user = ""
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
                "--property=ActiveState,SubState,Description,User,MainPID,"
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
            elif key == "User":
                user = val
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
                    if 0 <= parsed < 2**63:
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
        "user": user,
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
    instance = paths.validate_instance_name(instance)
    if instance != paths.DEFAULT_INSTANCE_NAME:
        return f"armareforger@{instance}.service"
    return paths.SERVICE_NAME


def restart_service_unit_name(instance: str = paths.DEFAULT_INSTANCE_NAME) -> str:
    """Return the helper restart service unit name for an instance."""
    instance = paths.validate_instance_name(instance)
    if instance != paths.DEFAULT_INSTANCE_NAME:
        return f"armareforger-restart@{instance}.service"
    return paths.RESTART_SERVICE_NAME


def timer_unit_name(instance: str = paths.DEFAULT_INSTANCE_NAME) -> str:
    """Return the timer unit name for an instance."""
    instance = paths.validate_instance_name(instance)
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
    user = resolve_linux_user()

    try:
        server_dir = paths.validate_server_install_dir(
            paths.server_dir(instance),
            instance=instance,
        )
    except paths.UnsafeServerInstallDirError as e:
        return [ServiceResult(False, str(e), 1)]

    inst_root = server_dir.parent

    # Check if instance actually exists contextually
    if not inst_root.exists():
        inst_root.mkdir(parents=True, exist_ok=True)

    start_sh = inst_root / "start-armareforger.sh"
    config_dir = inst_root / "config"
    config_file = config_dir / "config.json"

    service_name = service_unit_name(instance)
    restart_service_name = restart_service_unit_name(instance)
    timer_name = timer_unit_name(instance)
    on_calendar_entries = normalize_on_calendar_entries(on_calendar)
    if not on_calendar_entries:
        on_calendar_entries = [normalize_on_calendar("*-*-* 06:00:00")]

    service_path = paths.SYSTEMD_DIR / service_name
    restart_service_path = paths.SYSTEMD_DIR / restart_service_name
    timer_path = paths.SYSTEMD_DIR / timer_name
    safe_restart_helper_path = paths.safe_restart_helper_file()

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
        max_fps = load_max_fps_profile(instance)
        start_sh_render = render_start_script(
            instance_root=inst_root,
            server_dir=server_dir,
            config_dir=config_dir,
            config_file=config_file,
            log_stats_interval_ms=10000,
            max_fps=max_fps,
        )
        service_render = env.get_template("armareforger.service.j2").render(
            user=user,
            instance_root=str(inst_root),
            server_dir=str(server_dir),
            start_script=str(start_sh),
        )
        safe_restart_helper_render = _render_safe_restart_helper_script()
        restart_service_render = env.get_template("armareforger-restart.service.j2").render(
            instance=instance,
            restart_timing=RESTART_TIMING,
            service_name=service_name,
            restart_helper=str(safe_restart_helper_path),
        )

        timer_render = render_restart_timer_unit(on_calendar_entries)

        # 3. Write start script (no sudo needed, it is in the instance root)
        _replace_generated_start_script(
            start_sh,
            start_sh_render,
            instance=instance,
            backup_existing=True,
        )
        results.append(ServiceResult(True, tr("Generated {path}", path=start_sh)))

        # 4. Write systemd files to temp and sudo mv them
        with tempfile.TemporaryDirectory() as tempd:
            temp_dir = Path(tempd)

            tservice = temp_dir / service_name
            trestart = temp_dir / restart_service_name
            ttimer = temp_dir / timer_name
            thelper = temp_dir / safe_restart_helper_path.name

            with open(tservice, "w") as f:
                f.write(service_render)
            with open(trestart, "w") as f:
                f.write(restart_service_render)
            with open(ttimer, "w") as f:
                f.write(timer_render)
            with open(thelper, "w") as f:
                f.write(safe_restart_helper_render)

            for tmp_file, dest_file in [
                (thelper, safe_restart_helper_path),
                (tservice, service_path),
                (trestart, restart_service_path),
                (ttimer, timer_path),
            ]:
                install_result = install_systemd_unit_file(
                    tmp_file,
                    dest_file,
                    mode="0755" if tmp_file == thelper else "0644",
                )
                results.append(install_result)
                if not install_result.success:
                    return results

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

    except RuntimeSettingsError as e:
        results.append(_runtime_settings_failure(e))

    except Exception as e:
        results.append(
            ServiceResult(False, tr("Service generation failed: {error}", error=e), 1)
        )

    return results
