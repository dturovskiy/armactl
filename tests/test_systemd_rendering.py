"""Tests for the narrow game-systemd rendering boundary."""

from pathlib import Path

import pytest

from armactl import paths
from armactl.platform.systemd_rendering import (
    normalize_generated_text,
    render_game_service_unit,
    render_privileged_helper_script,
    render_privileged_sudoers,
    render_restart_service_unit,
    render_restart_timer_unit,
    render_safe_restart_helper_script,
    render_start_script,
)
from armactl.restart_timing import RESTART_TIMING


def test_normalize_generated_text_uses_lf_and_final_newline() -> None:
    assert normalize_generated_text("one\r\ntwo\rthree") == "one\ntwo\nthree\n"


def test_render_start_script_uses_explicit_runtime_context() -> None:
    rendered = render_start_script(
        templates_dir=paths.templates_dir(),
        instance_root="/srv/instance",
        server_dir="/srv/instance/server",
        config_dir="/srv/instance/config",
        config_file="/srv/instance/config/config.json",
        python_executable="/opt/armactl/bin/python",
        log_stats_interval_ms=5000,
        max_fps=120,
    )

    assert rendered.startswith("#!/usr/bin/env bash\n")
    assert 'SERVER_DIR="/srv/instance/server"' in rendered
    assert 'SAT_GUARD_PYTHON="/opt/armactl/bin/python"' in rendered
    assert "-maxFPS 120" in rendered
    assert "-logStats 5000" in rendered


def test_render_game_and_restart_units() -> None:
    templates_dir = paths.templates_dir()
    game = render_game_service_unit(
        templates_dir=templates_dir,
        user="gameserver",
        instance_root="/srv/instance",
        server_dir="/srv/instance/server",
        start_script="/srv/instance/start-armareforger.sh",
    )
    restart = render_restart_service_unit(
        templates_dir=templates_dir,
        instance="default",
        restart_timing=RESTART_TIMING,
        service_name="armareforger.service",
        restart_helper="/usr/local/libexec/armactl-safe-restart",
    )

    assert "User=gameserver" in game
    assert "LimitCORE=infinity" in game
    assert "ExecStart=/srv/instance/start-armareforger.sh" in game
    assert "armactl-safe-restart armareforger.service" in restart
    assert f"TimeoutStartSec={RESTART_TIMING.restart_unit_timeout_start_sec}" in restart


def test_render_restart_timer_validates_and_renders_all_entries() -> None:
    rendered = render_restart_timer_unit(
        ["*-*-* 05:00:00", "*-*-* 17:00:00"],
        templates_dir=paths.templates_dir(),
    )

    assert "OnCalendar=*-*-* 05:00:00" in rendered
    assert "OnCalendar=*-*-* 17:00:00" in rendered
    with pytest.raises(ValueError, match="hours 0-23"):
        render_restart_timer_unit("24:00", templates_dir=paths.templates_dir())


def test_render_privileged_files_uses_explicit_paths() -> None:
    templates_dir = paths.templates_dir()
    helper = render_privileged_helper_script(
        templates_dir=templates_dir,
        install_binary="/bin/install",
        systemctl_binary="/bin/systemctl",
    )
    restart_helper = render_safe_restart_helper_script(
        templates_dir=templates_dir,
        restart_timing=RESTART_TIMING,
        systemctl_binary="/bin/systemctl",
    )
    sudoers = render_privileged_sudoers(
        templates_dir=templates_dir,
        user="operator",
        helper_path=Path("/usr/local/libexec/armactl-systemctl-helper"),
    )

    assert 'INSTALL_BIN = "/bin/install"' in helper
    assert 'SYSTEMCTL_BIN = "/bin/systemctl"' in helper
    assert f"STOP_GRACE_SECONDS = {RESTART_TIMING.stop_grace_seconds}" in restart_helper
    assert "/bin/systemctl" in restart_helper
    assert sudoers == (
        "operator ALL=(root) NOPASSWD: "
        "/usr/local/libexec/armactl-systemctl-helper, "
        "/usr/local/libexec/armactl-systemctl-helper *\n"
    )
