"""Tests for service and timer helpers."""

from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired
from unittest.mock import patch

from armactl import paths
from armactl.restart_timing import RESTART_TIMING
from armactl.sat_admin_guard import SatAdminGuardError
from armactl.service_manager import (
    ServiceResult,
    _build_systemctl_command,
    _render_privileged_helper_script,
    _render_safe_restart_helper_script,
    _run_systemctl,
    _secure_privileged_channel_message,
    format_schedule_for_input,
    get_privileged_channel_user,
    get_service_status,
    get_timer_status,
    has_privileged_systemctl_channel,
    normalize_on_calendar,
    normalize_on_calendar_entries,
    resolve_linux_user,
    restart_service,
    restart_service_unit_name,
    service_unit_name,
    start_service,
    timer_unit_name,
    update_restart_timer_schedule,
)


def test_normalize_on_calendar_accepts_time_only() -> None:
    """Short HH:MM input should expand to a full OnCalendar expression."""
    assert normalize_on_calendar("8:00") == "*-*-* 08:00:00"
    assert normalize_on_calendar("05:30") == "*-*-* 05:30:00"
    assert normalize_on_calendar("05:30:10") == "*-*-* 05:30:10"


def test_normalize_on_calendar_entries_accepts_multiple_times() -> None:
    """Comma-separated times should become multiple OnCalendar entries."""
    assert normalize_on_calendar_entries("05:00, 13:30, 22:00") == [
        "*-*-* 05:00:00",
        "*-*-* 13:30:00",
        "*-*-* 22:00:00",
    ]


def test_normalize_on_calendar_entries_accepts_space_separated_times() -> None:
    """Space-separated times should also become multiple OnCalendar entries."""
    assert normalize_on_calendar_entries("06:00 18:00") == [
        "*-*-* 06:00:00",
        "*-*-* 18:00:00",
    ]


def test_format_schedule_for_input_compacts_daily_times() -> None:
    """Stored daily schedules should render back to user-friendly times."""
    assert format_schedule_for_input(
        ["*-*-* 05:00:00", "*-*-* 13:30:00", "*-*-* 22:00:00"]
    ) == "05:00, 13:30, 22:00"


def test_unit_name_helpers_respect_instances() -> None:
    """Service and timer helpers should derive names consistently."""
    assert service_unit_name() == paths.SERVICE_NAME
    assert timer_unit_name() == paths.TIMER_NAME
    assert service_unit_name("alpha") == "armareforger@alpha.service"
    assert restart_service_unit_name("alpha") == "armareforger-restart@alpha.service"
    assert timer_unit_name("alpha") == "armareforger-restart@alpha.timer"


def test_unit_name_helpers_reject_unsafe_instance_names() -> None:
    """Unsafe instance names must not turn into systemd paths."""
    for helper in (service_unit_name, restart_service_unit_name, timer_unit_name):
        try:
            helper("../../escape")
        except paths.InvalidInstanceNameError:
            continue
        raise AssertionError(f"{helper.__name__} should reject unsafe instance names")


def test_restart_timing_contract_keeps_helper_unit_and_caller_guards_aligned() -> None:
    """Restart helper, systemd unit, and caller guard must share one contract."""
    assert RESTART_TIMING.helper_state_window_seconds == (
        RESTART_TIMING.stop_grace_seconds
        + RESTART_TIMING.post_kill_grace_seconds
        + RESTART_TIMING.start_grace_seconds
        + RESTART_TIMING.stability_check_seconds
    )
    assert (
        RESTART_TIMING.restart_unit_timeout_seconds
        >= RESTART_TIMING.helper_worst_case_window_seconds
    )
    assert RESTART_TIMING.caller_timeout_seconds > RESTART_TIMING.restart_unit_timeout_seconds


def test_get_timer_status_falls_back_to_timer_file_schedule(tmp_path: Path) -> None:
    """Timer status should read OnCalendar from the unit file when needed."""
    timer_name = "armareforger-restart@test.timer"
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()
    (systemd_dir / timer_name).write_text(
        "[Timer]\nOnCalendar=*-*-* 05:30:00\nOnCalendar=*-*-* 13:45:00\n",
        encoding="utf-8",
    )
    completed = CompletedProcess(
        args=["systemctl", "show", timer_name],
        returncode=0,
        stdout=(
            "ActiveState=active\n"
            "SubState=waiting\n"
            "Description=Scheduled restart\n"
            "UnitFileState=enabled\n"
            "NextElapseUSecRealtime=Mon 2026-03-30 05:30:00 UTC\n"
            "LastTriggerUSec=n/a\n"
            "TimersCalendar=\n"
        ),
        stderr="",
    )

    with (
        patch("armactl.service_manager.paths.SYSTEMD_DIR", systemd_dir),
        patch("armactl.service_manager.subprocess.run", return_value=completed),
    ):
        status = get_timer_status(timer_name)

    assert status["exists"] is True
    assert status["active"] is True
    assert status["enabled"] is True
    assert status["schedule_entries"] == ["*-*-* 05:30:00", "*-*-* 13:45:00"]
    assert status["schedule"] == "05:30, 13:45"
    assert status["next_run"] == "Mon 2026-03-30 05:30:00 UTC"


def test_get_service_status_falls_back_to_exec_main_pid() -> None:
    completed = CompletedProcess(
        args=["systemctl", "show", "armareforger.service"],
        returncode=0,
        stdout=(
            "ActiveState=active\n"
            "SubState=running\n"
            "Description=Arma Reforger Dedicated Server\n"
            "User=defenders88\n"
            "MainPID=0\n"
            "ExecMainPID=4321\n"
            "ControlPID=0\n"
            "MemoryCurrent=268435456\n"
            "CPUUsageNSec=5000000000\n"
            "ExecMainStartTimestampMonotonic=5000000\n"
            "ActiveEnterTimestampMonotonic=4000000\n"
        ),
        stderr="",
    )

    with (
        patch("armactl.service_manager.is_active", return_value=True),
        patch("armactl.service_manager.is_enabled", return_value=True),
        patch("armactl.service_manager.subprocess.run", return_value=completed),
    ):
        status = get_service_status("armareforger.service")

    assert status["active"] is True
    assert status["enabled"] is True
    assert status["active_state"] == "active"
    assert status["sub_state"] == "running"
    assert status["user"] == "defenders88"
    assert status["main_pid"] == 4321
    assert status["exec_main_pid"] == 4321
    assert status["memory_current_bytes"] == 268435456
    assert status["cpu_usage_nsec"] == 5000000000
    assert status["exec_main_start_usec"] == 5000000
    assert status["active_enter_usec"] == 4000000


def test_get_service_status_keeps_zero_memory_current() -> None:
    completed = CompletedProcess(
        args=["systemctl", "show", "armareforger.service"],
        returncode=0,
        stdout=(
            "ActiveState=active\n"
            "SubState=running\n"
            "Description=Arma Reforger Dedicated Server\n"
            "MainPID=4321\n"
            "ExecMainPID=4321\n"
            "ControlPID=0\n"
            "MemoryCurrent=0\n"
        ),
        stderr="",
    )

    with (
        patch("armactl.service_manager.is_active", return_value=True),
        patch("armactl.service_manager.is_enabled", return_value=True),
        patch("armactl.service_manager.subprocess.run", return_value=completed),
    ):
        status = get_service_status("armareforger.service")

    assert status["memory_current_bytes"] == 0


def test_has_privileged_systemctl_channel_requires_helper_and_sudoers(tmp_path: Path) -> None:
    helper_path = tmp_path / "libexec" / "armactl-systemctl-helper"
    sudoers_path = tmp_path / "sudoers.d" / "armactl-systemctl-helper"
    helper_path.parent.mkdir()
    sudoers_path.parent.mkdir()

    with (
        patch("armactl.service_manager.paths.privileged_helper_file", return_value=helper_path),
        patch("armactl.service_manager.paths.privileged_sudoers_file", return_value=sudoers_path),
    ):
        assert has_privileged_systemctl_channel() is False
        helper_path.write_text("helper", encoding="utf-8")
        assert has_privileged_systemctl_channel() is False
        sudoers_path.write_text("sudoers", encoding="utf-8")
        assert has_privileged_systemctl_channel() is True


def test_render_safe_restart_helper_is_bounded_to_armareforger_services() -> None:
    helper = _render_safe_restart_helper_script()

    compile(helper, "armactl-safe-restart", "exec")
    namespace: dict[str, object] = {"__name__": "armactl_safe_restart_test"}
    exec(compile(helper, "armactl-safe-restart", "exec"), namespace)
    assert namespace["STOP_GRACE_SECONDS"] == RESTART_TIMING.stop_grace_seconds
    assert namespace["POST_KILL_GRACE_SECONDS"] == RESTART_TIMING.post_kill_grace_seconds
    assert namespace["START_GRACE_SECONDS"] == RESTART_TIMING.start_grace_seconds
    assert namespace["STABLE_SECONDS"] == RESTART_TIMING.stability_check_seconds
    assert namespace["POLL_SECONDS"] == RESTART_TIMING.poll_seconds
    assert '"kill", "--kill-who=all", "--signal=SIGKILL", unit' in helper
    assert 'ALLOWED_UNIT_RE = re.compile(r"^armareforger' in helper
    assert "armactl-web.service" not in helper


def test_get_privileged_channel_user_parses_sudoers_dropin(tmp_path: Path) -> None:
    sudoers_path = tmp_path / "sudoers.d" / "armactl-systemctl-helper"
    sudoers_path.parent.mkdir()
    sudoers_path.write_text(
        "defenders88 ALL=(root) NOPASSWD: /usr/local/libexec/armactl-systemctl-helper, "
        "/usr/local/libexec/armactl-systemctl-helper *\n",
        encoding="utf-8",
    )

    with patch("armactl.service_manager.paths.privileged_sudoers_file", return_value=sudoers_path):
        assert get_privileged_channel_user() == "defenders88"


def test_resolve_linux_user_prefers_sudo_user() -> None:
    with (
        patch.dict(
            "os.environ",
            {"SUDO_USER": "defenders88", "USER": "root", "LOGNAME": "root"},
            clear=True,
        ),
        patch("armactl.service_manager.os.getlogin", side_effect=OSError),
        patch("armactl.service_manager.getpass.getuser", return_value="root"),
    ):
        assert resolve_linux_user() == "defenders88"


def test_resolve_linux_user_falls_back_to_root_when_needed() -> None:
    with (
        patch.dict("os.environ", {"USER": "root", "LOGNAME": "root"}, clear=True),
        patch("armactl.service_manager.os.getlogin", side_effect=OSError),
        patch("armactl.service_manager.getpass.getuser", return_value="root"),
    ):
        assert resolve_linux_user() == "root"


def test_render_privileged_helper_script_uses_python_and_lf_newlines() -> None:
    rendered = _render_privileged_helper_script()

    assert rendered.startswith("#!/usr/bin/env python3\n")
    assert "\r" not in rendered
    assert "def main(argv: list[str]) -> int:" in rendered
    assert "armactl-bot.service" in rendered
    assert "armactl-discord-stats.service" in rendered
    assert "armactl-web.service" in rendered


def test_build_systemctl_command_prefers_secure_helper_channel() -> None:
    helper_path = Path("/usr/local/libexec/armactl-systemctl-helper")

    with (
        patch("armactl.service_manager.has_privileged_systemctl_channel", return_value=True),
        patch("armactl.service_manager.paths.privileged_helper_file", return_value=helper_path),
    ):
        command = _build_systemctl_command("restart", "armareforger.service")

    assert command == [
        "sudo",
        "-n",
        str(helper_path),
        "restart",
        "armareforger.service",
    ]


def test_build_systemctl_command_uses_noninteractive_sudo_without_tty() -> None:
    with (
        patch("armactl.service_manager.has_privileged_systemctl_channel", return_value=False),
        patch("armactl.service_manager.sys.stdin.isatty", return_value=False),
        patch(
            "armactl.service_manager._resolve_systemctl_binary",
            return_value="/usr/bin/systemctl",
        ),
    ):
        command = _build_systemctl_command("stop", "armareforger.service")

    assert command == [
        "sudo",
        "-n",
        "/usr/bin/systemctl",
        "stop",
        "armareforger.service",
    ]


def test_start_service_runs_sat_admin_guard_before_systemctl(tmp_path: Path) -> None:
    config_path = tmp_path / "default" / "config" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("{}", encoding="utf-8")
    calls: list[Path] = []

    def guard(config: Path) -> None:
        calls.append(config)

    with (
        patch("armactl.service_manager.paths.config_file", return_value=config_path),
        patch("armactl.sat_admin_guard.guard_sat_admin_config", side_effect=guard),
        patch(
            "armactl.service_manager._run_systemctl",
            return_value=ServiceResult(True, "started"),
        ) as systemctl_mock,
    ):
        result = start_service("armareforger.service")

    assert result.success is True
    assert calls == [config_path]
    systemctl_mock.assert_called_once_with("start", "armareforger.service")


def test_restart_service_uses_bounded_restart_helper_unit(tmp_path: Path) -> None:
    config_path = tmp_path / "default" / "config" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("{}", encoding="utf-8")

    with (
        patch("armactl.service_manager.paths.config_file", return_value=config_path),
        patch("armactl.sat_admin_guard.guard_sat_admin_config"),
        patch(
            "armactl.service_manager._run_systemctl",
            return_value=ServiceResult(True, "restart helper started"),
        ) as systemctl_mock,
    ):
        result = restart_service("armareforger.service")

    assert result.success is True
    systemctl_mock.assert_called_once_with(
        "start",
        "armareforger-restart.service",
        timeout_seconds=RESTART_TIMING.caller_timeout_seconds,
    )


def test_restart_service_uses_instance_bounded_restart_helper_unit(tmp_path: Path) -> None:
    config_path = tmp_path / "alpha" / "config" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("{}", encoding="utf-8")

    with (
        patch("armactl.service_manager.paths.config_file", return_value=config_path),
        patch("armactl.sat_admin_guard.guard_sat_admin_config"),
        patch(
            "armactl.service_manager._run_systemctl",
            return_value=ServiceResult(True, "restart helper started"),
        ) as systemctl_mock,
    ):
        result = restart_service("armareforger@alpha.service")

    assert result.success is True
    systemctl_mock.assert_called_once_with(
        "start",
        "armareforger-restart@alpha.service",
        timeout_seconds=RESTART_TIMING.caller_timeout_seconds,
    )


def test_restart_service_keeps_non_game_service_restart_path() -> None:
    with patch(
        "armactl.service_manager._run_systemctl",
        return_value=ServiceResult(True, "web restarted"),
    ) as systemctl_mock:
        result = restart_service("armactl-web.service")

    assert result.success is True
    systemctl_mock.assert_called_once_with("restart", "armactl-web.service")


def test_restart_helper_service_runs_sat_admin_guard_for_instance(tmp_path: Path) -> None:
    config_path = tmp_path / "alpha" / "config" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("{}", encoding="utf-8")
    calls: list[Path] = []

    def guard(config: Path) -> None:
        calls.append(config)

    with (
        patch("armactl.service_manager.paths.config_file", return_value=config_path) as config_mock,
        patch("armactl.sat_admin_guard.guard_sat_admin_config", side_effect=guard),
        patch(
            "armactl.service_manager._run_systemctl",
            return_value=ServiceResult(True, "restart helper started"),
        ) as systemctl_mock,
    ):
        result = start_service("armareforger-restart@alpha.service")

    assert result.success is True
    config_mock.assert_called_once_with("alpha")
    assert calls == [config_path]
    systemctl_mock.assert_called_once_with("start", "armareforger-restart@alpha.service")


def test_start_service_stops_when_sat_admin_guard_fails(tmp_path: Path) -> None:
    config_path = tmp_path / "default" / "config" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("{}", encoding="utf-8")

    with (
        patch("armactl.service_manager.paths.config_file", return_value=config_path),
        patch(
            "armactl.sat_admin_guard.guard_sat_admin_config",
            side_effect=SatAdminGuardError("broken SAT config"),
        ),
        patch("armactl.service_manager._run_systemctl") as systemctl_mock,
    ):
        result = start_service("armareforger.service")

    assert result.success is False
    assert "ServerAdminTools admin guard failed" in result.message
    systemctl_mock.assert_not_called()


def test_update_restart_timer_schedule_uses_secure_helper_channel() -> None:
    helper_path = Path("/usr/local/libexec/armactl-systemctl-helper")
    update_completed = CompletedProcess(
        args=["sudo", "-n", str(helper_path), "update-timer"],
        returncode=0,
        stdout="",
        stderr="",
    )
    reload_completed = CompletedProcess(
        args=["sudo", "-n", str(helper_path), "daemon-reload"],
        returncode=0,
        stdout="",
        stderr="",
    )
    restart_completed = CompletedProcess(
        args=["sudo", "-n", str(helper_path), "restart", paths.TIMER_NAME],
        returncode=0,
        stdout="",
        stderr="",
    )

    with (
        patch("armactl.service_manager.has_privileged_systemctl_channel", return_value=True),
        patch("armactl.service_manager.paths.privileged_helper_file", return_value=helper_path),
        patch(
            "armactl.service_manager.subprocess.run",
            side_effect=[update_completed, reload_completed, restart_completed],
        ) as run_mock,
    ):
        results = update_restart_timer_schedule("default", ["*-*-* 08:00:00"])

    assert [result.success for result in results] == [True, True, True]
    run_mock.assert_any_call(
        [
            "sudo",
            "-n",
            str(helper_path),
            "update-timer",
            paths.TIMER_NAME,
            "*-*-* 08:00:00",
        ],
        capture_output=True,
        text=True,
    )


def test_run_systemctl_rewrites_noninteractive_sudo_error() -> None:
    completed = CompletedProcess(
        args=["sudo", "-n", "/usr/bin/systemctl", "stop", "armareforger.service"],
        returncode=1,
        stdout="",
        stderr=(
            "sudo: a terminal is required to read the password; "
            "either use the -S option to read from standard input or configure an askpass helper\n"
            "sudo: a password is required"
        ),
    )

    with patch("armactl.service_manager.subprocess.run", return_value=completed):
        result = _run_systemctl("stop", "armareforger.service")

    assert result.success is False
    assert _secure_privileged_channel_message() == result.message


def test_run_systemctl_uses_configured_timeout_in_subprocess_and_message() -> None:
    with patch(
        "armactl.service_manager.subprocess.run",
        side_effect=TimeoutExpired(["systemctl", "start", "unit.service"], timeout=75),
    ) as run_mock:
        result = _run_systemctl("start", "unit.service", timeout_seconds=75)

    assert run_mock.call_args.kwargs["timeout"] == 75
    assert result.success is False
    assert "timed out after 75s" in result.message


def test_run_systemctl_redacts_secret_values_in_stderr() -> None:
    completed = CompletedProcess(
        args=["sudo", "-n", "/usr/bin/systemctl", "restart", "armareforger.service"],
        returncode=1,
        stdout="",
        stderr="passwordAdmin=super-secret",
    )

    with patch("armactl.service_manager.subprocess.run", return_value=completed):
        result = _run_systemctl("restart", "armareforger.service")

    assert result.success is False
    assert "super-secret" not in result.message
    assert "passwordAdmin=***" in result.message


def test_replace_generated_start_script_fsyncs_temp_and_parent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import armactl.service_manager as service_manager_module

    start_script = tmp_path / "start-armareforger.sh"
    start_script.write_text("old", encoding="utf-8")
    backups_dir = tmp_path / "backups"
    fsync_calls: list[int] = []

    monkeypatch.setattr(
        service_manager_module.paths,
        "backups_dir",
        lambda instance: backups_dir,
    )
    monkeypatch.setattr(
        service_manager_module.os,
        "fsync",
        lambda fd: fsync_calls.append(fd),
    )

    service_manager_module._replace_generated_start_script(
        start_script,
        "new\n",
        instance="alpha",
        backup_existing=True,
    )

    assert start_script.read_text(encoding="utf-8") == "new\n"
    assert start_script.stat().st_mode & 0o777 == 0o755
    assert not (tmp_path / ".start-armareforger.sh.tmp").exists()
    assert len(list(backups_dir.glob("start-armareforger.sh.*.bak"))) == 1
    assert len(fsync_calls) >= 2
