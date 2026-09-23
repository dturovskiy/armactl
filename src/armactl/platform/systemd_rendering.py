"""Rendering helpers for generated game-server scripts and systemd units."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from armactl.i18n import _
from armactl.platform.restart_timer import (
    INVALID_RESTART_TIME_MESSAGE,
    has_schedule_input,
    normalize_on_calendar,
    normalize_on_calendar_entries,
)
from armactl.restart_timing import RestartTimingContract
from armactl.runtime_settings import normalize_max_fps_profile


def template_environment(templates_dir: Path) -> Environment:
    """Build the Jinja environment for shared armactl templates."""
    return Environment(loader=FileSystemLoader(str(templates_dir)))


def normalize_generated_text(text: str) -> str:
    """Normalize generated helper/unit text to Unix newlines."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized if normalized.endswith("\n") else f"{normalized}\n"


def render_start_script(
    *,
    templates_dir: Path,
    instance_root: Path | str,
    server_dir: Path | str,
    config_dir: Path | str,
    config_file: Path | str,
    python_executable: Path | str,
    log_stats_interval_ms: int = 10000,
    max_fps: int = 60,
) -> str:
    """Render the generated Arma Reforger launch script."""
    rendered = template_environment(templates_dir).get_template(
        "start-armareforger.sh.j2"
    ).render(
        instance_root=str(instance_root),
        server_dir=str(server_dir),
        config_dir=str(config_dir),
        config_file=str(config_file),
        python_executable=str(python_executable),
        log_stats_interval_ms=log_stats_interval_ms,
        max_fps=normalize_max_fps_profile(max_fps),
    )
    return normalize_generated_text(rendered)


def render_game_service_unit(
    *,
    templates_dir: Path,
    user: str,
    instance_root: Path | str,
    server_dir: Path | str,
    start_script: Path | str,
) -> str:
    """Render the main game-server systemd unit."""
    return template_environment(templates_dir).get_template(
        "armareforger.service.j2"
    ).render(
        user=user,
        instance_root=str(instance_root),
        server_dir=str(server_dir),
        start_script=str(start_script),
    )


def render_restart_service_unit(
    *,
    templates_dir: Path,
    instance: str,
    restart_timing: RestartTimingContract,
    service_name: str,
    restart_helper: Path | str,
) -> str:
    """Render the bounded restart helper systemd unit."""
    return template_environment(templates_dir).get_template(
        "armareforger-restart.service.j2"
    ).render(
        instance=instance,
        restart_timing=restart_timing,
        service_name=service_name,
        restart_helper=str(restart_helper),
    )


def render_restart_timer_unit(
    on_calendar: str | list[str],
    *,
    templates_dir: Path,
) -> str:
    """Render the restart timer with one or more OnCalendar entries."""
    on_calendar_entries = normalize_on_calendar_entries(on_calendar)
    if not on_calendar_entries:
        if has_schedule_input(on_calendar):
            raise ValueError(_(INVALID_RESTART_TIME_MESSAGE))
        on_calendar_entries = [normalize_on_calendar("*-*-* 06:00:00")]

    return template_environment(templates_dir).get_template(
        "armareforger-restart.timer.j2"
    ).render(on_calendar_entries=on_calendar_entries)


def render_privileged_helper_script(
    *,
    templates_dir: Path,
    install_binary: Path | str,
    systemctl_binary: Path | str,
) -> str:
    """Render the root-owned narrow privileged helper."""
    rendered = template_environment(templates_dir).get_template(
        "armactl-systemctl-helper.py.j2"
    ).render(
        install_bin=str(install_binary),
        systemctl_bin=str(systemctl_binary),
    )
    return normalize_generated_text(rendered)


def render_safe_restart_helper_script(
    *,
    templates_dir: Path,
    restart_timing: RestartTimingContract,
    systemctl_binary: Path | str,
) -> str:
    """Render the bounded game-service restart helper."""
    rendered = template_environment(templates_dir).get_template(
        "armactl-safe-restart.py.j2"
    ).render(
        restart_timing=restart_timing,
        systemctl_bin=str(systemctl_binary),
    )
    return normalize_generated_text(rendered)


def render_privileged_sudoers(
    *,
    templates_dir: Path,
    user: str,
    helper_path: Path | str,
) -> str:
    """Render the sudoers drop-in for the narrow privileged helper."""
    rendered = template_environment(templates_dir).get_template(
        "armactl-systemctl-helper.sudoers.j2"
    ).render(
        user=user,
        helper_path=str(helper_path),
    )
    return normalize_generated_text(rendered)
