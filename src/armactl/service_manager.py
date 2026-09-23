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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import armactl.platform.systemd_execution as systemd_execution
import armactl.platform.systemd_privileged as systemd_privileged
import armactl.platform.systemd_rendering as systemd_rendering
import armactl.platform.systemd_status as systemd_status
from armactl import paths
from armactl.i18n import _, tr
from armactl.platform.restart_timer import (
    INVALID_RESTART_TIME_MESSAGE,
    normalize_on_calendar,
    normalize_on_calendar_entries,
)
from armactl.platform.restart_timer import (
    format_schedule_for_input as _format_schedule_for_input,
)
from armactl.platform.restart_timer import (
    has_schedule_input as _has_schedule_input,
)
from armactl.redaction import redact_sensitive_text
from armactl.restart_timing import RESTART_TIMING
from armactl.runtime_settings import (
    RuntimeSettingsError,
    load_max_fps_profile,
    normalize_max_fps_profile,
    read_max_fps_status,
    save_max_fps_profile,
)

SUDO_AUTH_ERROR_MARKERS = systemd_execution.SUDO_AUTH_ERROR_MARKERS
SYSTEMCTL_TIMEOUT_SECONDS = systemd_execution.SYSTEMCTL_TIMEOUT_SECONDS
ServiceResult = systemd_execution.ServiceResult
SYSTEMD_EXEC_MAIN_CODE_LABELS = systemd_status.SYSTEMD_EXEC_MAIN_CODE_LABELS
_parse_systemctl_show = systemd_status.parse_systemctl_show
_read_timer_schedule_entries = systemd_status.read_timer_schedule_entries
format_schedule_for_input = _format_schedule_for_input

SUDOERS_USER_RE = systemd_privileged.SUDOERS_USER_RE
INSTANCE_SERVICE_RE = re.compile(r"^armareforger@([A-Za-z0-9_.-]+)\.service$")
RESTART_INSTANCE_SERVICE_RE = re.compile(r"^armareforger-restart@([A-Za-z0-9_.-]+)\.service$")


def _secure_privileged_channel_message() -> str:
    """Return the user-facing guidance for missing or stale sudo-helper access."""
    return systemd_execution.secure_privileged_channel_message()


def _run_systemctl(
    action: str,
    service_name: str | None = None,
    use_sudo: bool = True,
    timeout_seconds: int = SYSTEMCTL_TIMEOUT_SECONDS,
) -> ServiceResult:
    """Run a systemctl command and return the result."""
    command = _build_systemctl_command(
        action,
        service_name=service_name,
        use_sudo=use_sudo,
    )
    return systemd_execution.execute_systemctl_command(
        action,
        service_name,
        command=command,
        use_sudo=use_sudo,
        timeout_seconds=timeout_seconds,
        run=subprocess.run,
        privileged_channel_message=_secure_privileged_channel_message,
    )


def _resolve_systemctl_binary() -> str:
    """Return the systemctl binary path used by the privileged helper."""
    return systemd_execution.resolve_systemctl_binary(which=shutil.which)


def _resolve_install_binary() -> str:
    """Return the install binary path used for root-owned file placement."""
    return systemd_privileged.resolve_install_binary(which=shutil.which)


def _resolve_helper_python_binary() -> str:
    """Return the Python interpreter used for the privileged helper."""
    return systemd_privileged.resolve_helper_python_binary(
        which=shutil.which,
        current_executable=sys.executable,
    )


def has_privileged_systemctl_channel() -> bool:
    """Return whether the narrow passwordless helper channel is installed."""
    return systemd_privileged.has_privileged_channel(
        paths.privileged_helper_file(),
        paths.privileged_sudoers_file(),
    )


def get_privileged_channel_user() -> str | None:
    """Return the Linux user currently granted access to the secure helper."""
    return systemd_privileged.get_privileged_channel_user(paths.privileged_sudoers_file())


def _looks_like_sudo_auth_error(stderr: str) -> bool:
    """Detect sudo failures caused by non-interactive password prompts."""
    return systemd_execution.looks_like_sudo_auth_error(stderr)


def _build_systemctl_command(
    action: str,
    service_name: str | None = None,
    *,
    use_sudo: bool = True,
) -> list[str]:
    """Build the safest available systemctl invocation for the current context."""
    if not use_sudo:
        return systemd_execution.build_systemctl_command(
            action,
            service_name,
            use_sudo=False,
            systemctl_binary=_resolve_systemctl_binary(),
            privileged_helper=None,
            stdin_isatty=True,
        )

    privileged_helper = (
        paths.privileged_helper_file() if has_privileged_systemctl_channel() else None
    )
    return systemd_execution.build_systemctl_command(
        action,
        service_name,
        use_sudo=True,
        systemctl_binary=("" if privileged_helper is not None else _resolve_systemctl_binary()),
        privileged_helper=privileged_helper,
        stdin_isatty=True if privileged_helper is not None else sys.stdin.isatty(),
    )


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
    return paths.templates_dir()


def _template_environment():
    """Build the Jinja environment for armactl templates."""
    return systemd_rendering.template_environment(_templates_dir())


def _normalize_generated_text(text: str) -> str:
    """Normalize generated helper/unit text to Unix newlines."""
    return systemd_rendering.normalize_generated_text(text)


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
    return systemd_rendering.render_start_script(
        templates_dir=_templates_dir(),
        instance_root=instance_root,
        server_dir=server_dir,
        config_dir=config_dir,
        config_file=config_file,
        python_executable=sys.executable,
        log_stats_interval_ms=log_stats_interval_ms,
        max_fps=max_fps,
    )


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
    return systemd_rendering.render_privileged_helper_script(
        templates_dir=_templates_dir(),
        install_binary=_resolve_install_binary(),
        systemctl_binary=_resolve_systemctl_binary(),
    )


def _render_safe_restart_helper_script() -> str:
    """Render the root-owned bounded restart helper script text."""
    return systemd_rendering.render_safe_restart_helper_script(
        templates_dir=_templates_dir(),
        restart_timing=RESTART_TIMING,
        systemctl_binary=_resolve_systemctl_binary(),
    )


def _render_privileged_sudoers(user: str) -> str:
    """Render the sudoers drop-in text for the current Linux user."""
    return systemd_rendering.render_privileged_sudoers(
        templates_dir=_templates_dir(),
        user=user,
        helper_path=paths.privileged_helper_file(),
    )


def install_privileged_systemctl_channel() -> list[ServiceResult]:
    """Install the narrow helper + sudoers rule used for bot/TUI service actions."""
    user = _systemctl_helper_user()
    try:
        helper_text = _render_privileged_helper_script()
        sudoers_text = _render_privileged_sudoers(user)
        return systemd_privileged.install_privileged_channel(
            helper_text=helper_text,
            sudoers_text=sudoers_text,
            helper_name=paths.PRIVILEGED_HELPER_NAME,
            helper_path=paths.privileged_helper_file(),
            sudoers_path=paths.privileged_sudoers_file(),
            python_binary=_resolve_helper_python_binary(),
            visudo_binary=shutil.which("visudo") or "/usr/sbin/visudo",
            install_binary=_resolve_install_binary(),
            run=subprocess.run,
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


def install_systemd_unit_file(
    source: Path,
    destination: Path,
    *,
    mode: str = "0644",
) -> ServiceResult:
    """Install a rendered root-owned systemd/helper file with standard sudo."""
    return systemd_privileged.install_root_owned_file(
        source,
        destination,
        mode=mode,
        install_binary=_resolve_install_binary(),
        run=subprocess.run,
        privileged_channel_message=_secure_privileged_channel_message,
    )


def render_restart_timer_unit(on_calendar: str | list[str]) -> str:
    """Render the restart timer unit with one or more OnCalendar entries."""
    return systemd_rendering.render_restart_timer_unit(
        on_calendar,
        templates_dir=_templates_dir(),
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
        message = (
            _(INVALID_RESTART_TIME_MESSAGE)
            if _has_schedule_input(on_calendar)
            else _("At least one restart time is required.")
        )
        return [ServiceResult(False, message, 1)]

    timer_was_active = is_active(timer_name)
    if timer_was_active:
        stop_result = _run_systemctl("stop", timer_name)
        results.append(stop_result)
        if not stop_result.success:
            return results

    clean_result = _run_systemctl("clean-timer-state", timer_name)
    results.append(clean_result)
    if not clean_result.success:
        if timer_was_active:
            results.append(_run_systemctl("start", timer_name))
        return results

    try:
        if has_privileged_systemctl_channel():
            update_result = systemd_privileged.update_timer_with_helper(
                helper_path=paths.privileged_helper_file(),
                timer_name=timer_name,
                schedule_entries=schedule_entries,
                timer_directory=timer_path.parent,
                run=subprocess.run,
                privileged_channel_message=_secure_privileged_channel_message,
            )
            results.append(update_result)
            if not update_result.success:
                if timer_was_active:
                    results.append(_run_systemctl("start", timer_name))
                return results
        else:
            with tempfile.TemporaryDirectory() as tempd:
                temp_timer = Path(tempd) / timer_name
                temp_timer.write_text(
                    render_restart_timer_unit(schedule_entries),
                    encoding="utf-8",
                )
                install_result = install_systemd_unit_file(temp_timer, timer_path)
                if not install_result.success:
                    results.append(install_result)
                    if timer_was_active:
                        results.append(_run_systemctl("start", timer_name))
                    return results
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
        if not reload_result.success:
            if timer_was_active:
                results.append(_run_systemctl("start", timer_name))
            return results

        if timer_was_active:
            timer_start = _run_systemctl("start", timer_name)
            results.append(timer_start)
    except Exception as e:
        results.append(
            ServiceResult(
                False,
                tr(
                    "Restart timer schedule update failed: {error}",
                    error=redact_sensitive_text(e),
                ),
                1,
            )
        )
        if timer_was_active:
            results.append(_run_systemctl("start", timer_name))
        return results

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
    return systemd_status.is_active(service_name, run=subprocess.run)


def is_enabled(service_name: str = "armareforger.service") -> bool:
    """Check if the service is enabled (starts on boot)."""
    return systemd_status.is_enabled(service_name, run=subprocess.run)


def get_systemd_unit_status(
    unit_name: str,
    *,
    unit_path: Path | None = None,
) -> dict[str, Any]:
    """Return bounded read-only status for one generated systemd unit."""
    return systemd_status.get_systemd_unit_status(
        unit_name,
        unit_path=unit_path or (paths.SYSTEMD_DIR / unit_name),
        run=subprocess.run,
    )


def get_service_status(service_name: str = "armareforger.service") -> dict[str, Any]:
    """Get detailed service status as a dict.

    Returns a structured dict suitable for both human display and JSON output.
    """
    return systemd_status.get_service_status(
        service_name,
        run=subprocess.run,
        active_probe=is_active,
        enabled_probe=is_enabled,
    )


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


def get_timer_status(timer_name: str = paths.TIMER_NAME) -> dict[str, Any]:
    """Return structured timer state suitable for CLI and TUI display."""
    return systemd_status.get_timer_status(
        timer_name,
        timer_path=paths.SYSTEMD_DIR / timer_name,
        run=subprocess.run,
    )


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
        if _has_schedule_input(on_calendar):
            return [ServiceResult(False, _(INVALID_RESTART_TIME_MESSAGE), 1)]
        on_calendar_entries = [normalize_on_calendar("*-*-* 06:00:00")]

    service_path = paths.SYSTEMD_DIR / service_name
    restart_service_path = paths.SYSTEMD_DIR / restart_service_name
    timer_path = paths.SYSTEMD_DIR / timer_name
    safe_restart_helper_path = paths.safe_restart_helper_file()

    templates_dir = _templates_dir()

    if not templates_dir.exists():
        return [
            ServiceResult(
                False,
                tr("Templates directory not found at {path}", path=templates_dir),
                1,
            )
        ]

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
        service_render = systemd_rendering.render_game_service_unit(
            templates_dir=templates_dir,
            user=user,
            instance_root=inst_root,
            server_dir=server_dir,
            start_script=start_sh,
        )
        safe_restart_helper_render = _render_safe_restart_helper_script()
        restart_service_render = systemd_rendering.render_restart_service_unit(
            templates_dir=templates_dir,
            instance=instance,
            restart_timing=RESTART_TIMING,
            service_name=service_name,
            restart_helper=safe_restart_helper_path,
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
        results.append(ServiceResult(False, tr("Service generation failed: {error}", error=e), 1))

    return results
