"""Tests for the explicit supervised player-log ingest service/timer foundation."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from subprocess import CompletedProcess

from click.testing import CliRunner

from armactl.cli import main
from armactl.player_log_ingest_service import (
    PLAYER_LOG_INGEST_RUNTIME_GUARD_SECONDS,
    PLAYER_LOG_INGEST_TIMER_INTERVAL_SECONDS,
    PlayerLogIngestActionResult,
    PlayerLogIngestInstallResult,
    PlayerLogIngestServiceStatus,
    ScheduledPlayerLogIngestResult,
)
from armactl.service_manager import ServiceResult
from armactl.web.services import player_registry
from armactl.web.services.audit import AuditLogError
from armactl.web.services.player_log_ingest import (
    PlayerLogIngestResult,
    PlayerLogIngestStatus,
)


def _copy_templates(project_root: Path) -> None:
    templates = project_root / "templates"
    templates.mkdir(parents=True)
    for name in (
        "armactl-player-log-ingest.service.j2",
        "armactl-player-log-ingest.timer.j2",
    ):
        source = Path("templates") / name
        (templates / name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


def _status(freshness: str) -> PlayerLogIngestStatus:
    return PlayerLogIngestStatus(
        instance="default",
        state="available",
        reason="ok",
        freshness_status=freshness,
    )


def _success_result(*, freshness: str = "fresh") -> PlayerLogIngestResult:
    return PlayerLogIngestResult(
        instance="default",
        files_considered=1,
        files_selected_for_scan=1,
        files_requested=1,
        files_scanned=1,
        scanned_lines=3,
        parsed_events=2,
        stored_events=1,
        duplicate_events=1,
        checkpoint_updated=True,
        freshness_status=freshness,
        collection_success=True,
    )


def _failure_result() -> PlayerLogIngestResult:
    return PlayerLogIngestResult(
        instance="default",
        outcome="failed",
        failure_code="collector_failed",
        error_count=1,
        freshness_status=player_registry.PLAYER_LOG_INGEST_STATUS_FAILED,
        collection_success=False,
    )


def test_unit_rendering_uses_direct_venv_oneshot_and_completion_cadence(
    tmp_path: Path,
) -> None:
    from armactl import player_log_ingest_service as service

    project_root = tmp_path / "project"
    data_root = tmp_path / "runtime"
    python_bin = project_root / ".venv" / "bin" / "python"
    home_dir = tmp_path / "home" / "operator"
    _copy_templates(project_root)

    service_unit = service.render_player_log_ingest_service_unit(
        instance="alpha",
        data_root=data_root,
        project_root=project_root,
        python_bin=python_bin,
        user="operator",
        home_dir=home_dir,
    )
    timer_unit = service.render_player_log_ingest_timer_unit(
        instance="alpha",
        service_name="armactl-player-log-ingest@alpha.service",
        project_root=project_root,
    )

    expected_command = (
        f"ExecStart={python_bin} -m armactl --instance alpha players log-ingest "
        f"run --once --scheduled --data-root {data_root}"
    )
    assert "Type=oneshot" in service_unit
    assert expected_command in service_unit
    assert f"TimeoutStartSec={PLAYER_LOG_INGEST_RUNTIME_GUARD_SECONDS}s" in service_unit
    assert "UMask=0077" in service_unit
    assert "Nice=10" in service_unit
    assert "IOSchedulingClass=idle" in service_unit
    assert "CPUWeight=20" in service_unit
    assert "IOWeight=20" in service_unit
    assert "StandardOutput=journal" in service_unit
    assert "Restart=" not in service_unit
    assert "sudo" not in service_unit
    assert "apt" not in service_unit
    assert "bootstrap" not in service_unit
    assert "armareforger.service" not in service_unit

    assert PLAYER_LOG_INGEST_TIMER_INTERVAL_SECONDS == 120
    assert "OnActiveSec=120s" in timer_unit
    assert "OnUnitInactiveSec=120s" in timer_unit
    assert "Unit=armactl-player-log-ingest@alpha.service" in timer_unit
    assert "Persistent=true" not in timer_unit
    assert "OnCalendar=" not in timer_unit


def test_install_is_idempotent_and_does_not_enable_start_or_restart(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from armactl import player_log_ingest_service as service

    project_root = tmp_path / "project"
    systemd_dir = tmp_path / "systemd"
    _copy_templates(project_root)
    installed_runs: list[dict[str, str]] = []
    current_run: dict[str, str] = {}
    forbidden_calls: list[str] = []

    def fake_install(source: Path, destination: Path, *, mode: str = "0644") -> ServiceResult:
        assert mode == "0644"
        current_run[destination.name] = source.read_text(encoding="utf-8")
        return ServiceResult(True, f"installed {destination.name}", 0)

    monkeypatch.setattr(service.paths, "SYSTEMD_DIR", systemd_dir)
    monkeypatch.setattr(
        service,
        "check_player_log_ingest_service_runtime",
        lambda project_root=None: ServiceResult(True, "runtime ready", 0),
    )
    monkeypatch.setattr(service, "resolve_linux_user", lambda: "operator")
    monkeypatch.setattr(service, "_home_directory_for_user", lambda user: tmp_path / "home")
    monkeypatch.setattr(service, "install_systemd_unit_file", fake_install)
    monkeypatch.setattr(service, "daemon_reload", lambda: ServiceResult(True, "reloaded", 0))
    monkeypatch.setattr(
        service,
        "install_privileged_systemctl_channel",
        lambda: [ServiceResult(True, "helper installed", 0)],
    )
    monkeypatch.setattr(
        service,
        "enable_service",
        lambda unit: forbidden_calls.append(f"enable:{unit}"),
    )
    monkeypatch.setattr(
        service,
        "start_service",
        lambda unit: forbidden_calls.append(f"start:{unit}"),
    )

    for _ in range(2):
        current_run = {}
        result = service.install_player_log_ingest_service(
            data_root=tmp_path / "data",
            project_root=project_root,
        )
        installed_runs.append(current_run)
        assert result.success is True

    assert installed_runs[0] == installed_runs[1]
    assert set(installed_runs[0]) == {
        "armactl-player-log-ingest.service",
        "armactl-player-log-ingest.timer",
    }
    assert forbidden_calls == []
    rendered = "\n".join(installed_runs[0].values())
    assert "armareforger.service" not in rendered


def test_enable_and_disable_only_manage_the_ingest_timer(monkeypatch) -> None:
    from armactl import player_log_ingest_service as service

    calls: list[tuple[str, str]] = []

    def record(action: str):
        def wrapped(unit: str) -> ServiceResult:
            calls.append((action, unit))
            return ServiceResult(True, f"{action} ok", 0)

        return wrapped

    monkeypatch.setattr(service, "enable_service", record("enable"))
    monkeypatch.setattr(service, "start_service", record("start"))
    monkeypatch.setattr(service, "stop_service", record("stop"))
    monkeypatch.setattr(service, "disable_service", record("disable"))

    enabled = service.enable_player_log_ingest_timer("alpha")
    disabled = service.disable_player_log_ingest_timer("alpha")

    assert enabled.success is True
    assert disabled.success is True
    assert calls == [
        ("enable", "armactl-player-log-ingest@alpha.timer"),
        ("start", "armactl-player-log-ingest@alpha.timer"),
        ("stop", "armactl-player-log-ingest@alpha.timer"),
        ("disable", "armactl-player-log-ingest@alpha.timer"),
    ]
    assert all("armareforger" not in unit for _, unit in calls)


def test_enable_rolls_back_new_enablement_when_timer_start_fails(monkeypatch) -> None:
    from armactl import player_log_ingest_service as service

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        service,
        "get_systemd_unit_status",
        lambda *args, **kwargs: {"unit_file_state": "disabled"},
    )

    def result(action: str, *, success: bool = True):
        def wrapped(unit: str) -> ServiceResult:
            calls.append((action, unit))
            return ServiceResult(success, f"{action} result", 0 if success else 1)

        return wrapped

    monkeypatch.setattr(service, "enable_service", result("enable"))
    monkeypatch.setattr(service, "start_service", result("start", success=False))
    monkeypatch.setattr(service, "stop_service", result("rollback-stop"))
    monkeypatch.setattr(service, "disable_service", result("rollback-disable"))

    enabled = service.enable_player_log_ingest_timer("alpha")

    assert enabled.success is False
    assert enabled.exit_code == 1
    assert calls == [
        ("enable", "armactl-player-log-ingest@alpha.timer"),
        ("start", "armactl-player-log-ingest@alpha.timer"),
        ("rollback-stop", "armactl-player-log-ingest@alpha.timer"),
        ("rollback-disable", "armactl-player-log-ingest@alpha.timer"),
    ]

def test_generic_unit_status_reports_last_result_and_next_trigger(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from armactl import service_manager

    unit_path = tmp_path / "armactl-player-log-ingest.timer"
    unit_path.write_text("[Timer]\nOnActiveSec=120s\n", encoding="utf-8")
    completed = CompletedProcess(
        args=["systemctl", "show"],
        returncode=0,
        stdout=(
            "LoadState=loaded\n"
            "ActiveState=active\n"
            "SubState=waiting\n"
            "UnitFileState=enabled\n"
            "Result=success\n"
            "ExecMainCode=1\n"
            "ExecMainStatus=0\n"
            "ExecMainExitTimestamp=Sat 2026-07-11 12:00:00 UTC\n"
            "NextElapseUSecRealtime=Sat 2026-07-11 12:02:00 UTC\n"
            "NextElapseUSecMonotonic=1d 2h 3min\n"
            "LastTriggerUSec=Sat 2026-07-11 12:00:00 UTC\n"
        ),
        stderr="",
    )
    monkeypatch.setattr(service_manager.subprocess, "run", lambda *args, **kwargs: completed)

    status = service_manager.get_systemd_unit_status(
        unit_path.name,
        unit_path=unit_path,
    )

    assert status["exists"] is True
    assert status["enabled"] is True
    assert status["active"] is True
    assert status["failed"] is False
    assert status["result"] == "success"
    assert status["exec_main_code"] == "exited"
    assert status["exec_main_status"] == 0
    assert status["next_trigger"] == "Sat 2026-07-11 12:02:00 UTC"
    assert status["next_trigger_kind"] == "realtime"


def test_generic_unit_status_marks_completion_relative_monotonic_trigger(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from armactl import service_manager

    unit_path = tmp_path / "armactl-player-log-ingest.timer"
    unit_path.write_text("[Timer]\nOnUnitInactiveSec=120s\n", encoding="utf-8")
    completed = CompletedProcess(
        args=["systemctl", "show"],
        returncode=0,
        stdout=(
            "LoadState=loaded\n"
            "ActiveState=active\n"
            "SubState=waiting\n"
            "UnitFileState=enabled\n"
            "NextElapseUSecRealtime=\n"
            "NextElapseUSecMonotonic=1d 2h 3min\n"
        ),
        stderr="",
    )
    monkeypatch.setattr(service_manager.subprocess, "run", lambda *args, **kwargs: completed)

    status = service_manager.get_systemd_unit_status(
        unit_path.name,
        unit_path=unit_path,
    )

    assert status["next_trigger"] == ""
    assert status["next_trigger_kind"] == "monotonic"


def test_cli_status_explains_monotonic_timer_and_numeric_process_code(
    monkeypatch,
) -> None:
    from armactl import player_log_ingest_service as service

    status = PlayerLogIngestServiceStatus(
        instance="default",
        cadence_seconds=120,
        service={
            "unit_name": "armactl-player-log-ingest.service",
            "exists": True,
            "active_state": "inactive",
            "sub_state": "dead",
            "failed": False,
            "result": "success",
            "exec_main_code": "exited",
            "exec_main_status": 0,
            "last_exit_at": "Sat 2026-07-11 18:10:22 UTC",
        },
        timer={
            "unit_name": "armactl-player-log-ingest.timer",
            "exists": True,
            "active": True,
            "enabled": True,
            "active_state": "active",
            "sub_state": "waiting",
            "failed": False,
            "next_trigger": "",
            "next_trigger_kind": "monotonic",
            "last_trigger": "Sat 2026-07-11 18:10:21 UTC",
        },
        freshness=PlayerLogIngestStatus(
            instance="default",
            state="available",
            reason="ok",
            coverage_started_at="2026-07-11T18:00:00+00:00",
        ),
    )
    monkeypatch.setattr(
        service,
        "get_player_log_ingest_service_status",
        lambda *args, **kwargs: status,
    )

    result = CliRunner().invoke(main, ["players", "log-ingest", "status"])

    assert result.exit_code == 0
    assert "Service process: exited; exit status 0" in result.output
    assert "Next trigger:    pending (120s after completion)" in result.output
    assert "Coverage starts: 2026-07-11T18:00:00+00:00" in result.output


def test_status_is_read_only_for_missing_units_and_players_db(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from armactl import player_log_ingest_service as service

    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()
    monkeypatch.setattr(service.paths, "SYSTEMD_DIR", systemd_dir)

    status = service.get_player_log_ingest_service_status(data_root=tmp_path / "data")

    assert status.service["exists"] is False
    assert status.service["active_state"] == "missing"
    assert status.timer["exists"] is False
    assert status.timer["enabled"] is False
    assert status.freshness.reason == "players_db_missing"
    payload = status.to_dict()
    assert payload["reason"] == "players_db_missing"
    assert payload["freshness"]["reason"] == "players_db_missing"
    assert payload["service"]["exists"] is False
    assert payload["timer"]["enabled"] is False
    assert not (tmp_path / "data" / "default" / "players.db").exists()


def test_scheduled_run_calls_shared_service_once_in_foreground_without_audit_spam(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from armactl import player_log_ingest_service as service

    calls: list[tuple[str, Path, int]] = []
    main_thread = threading.get_ident()
    statuses = iter(
        [
            _status(player_registry.PLAYER_LOG_INGEST_STATUS_FRESH),
            _status(player_registry.PLAYER_LOG_INGEST_STATUS_FRESH),
        ]
    )

    def fake_run(instance: str, *, data_root: Path) -> PlayerLogIngestResult:
        calls.append((instance, data_root, threading.get_ident()))
        return _success_result()

    monkeypatch.setattr(service, "run_player_log_ingest_once", fake_run)
    monkeypatch.setattr(
        service,
        "_read_ingest_status_safely",
        lambda *args, **kwargs: next(statuses),
    )
    monkeypatch.setattr(
        service,
        "append_audit_event",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no success audit spam")),
    )

    result = service.run_scheduled_player_log_ingest_once(data_root=tmp_path)

    assert result.exit_code == 0
    assert result.transition == "none"
    assert result.audit_state == "not_needed"
    assert calls == [("default", tmp_path, main_thread)]


def test_scheduled_busy_lock_is_a_controlled_skipped_cycle(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from armactl import player_log_ingest_service as service

    busy = PlayerLogIngestResult(
        instance="default",
        outcome="busy",
        failure_code="already_running",
    )
    monkeypatch.setattr(service, "run_player_log_ingest_once", lambda *args, **kwargs: busy)
    monkeypatch.setattr(
        service,
        "_read_ingest_status_safely",
        lambda *args, **kwargs: _status(player_registry.PLAYER_LOG_INGEST_STATUS_FRESH),
    )
    monkeypatch.setattr(
        service,
        "append_audit_event",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("busy is not audited")),
    )

    result = service.run_scheduled_player_log_ingest_once(data_root=tmp_path)
    output = service.format_scheduled_player_log_ingest_result(result)

    assert result.skipped is True
    assert result.exit_code == 0
    assert "outcome=skipped" in output
    assert "reason=already_running" in output


def test_failure_recovery_and_unchanged_success_have_bounded_sanitized_audit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from armactl import player_log_ingest_service as service

    fresh = player_registry.PLAYER_LOG_INGEST_STATUS_FRESH
    failed = player_registry.PLAYER_LOG_INGEST_STATUS_FAILED
    statuses = iter(
        [
            _status(fresh),
            _status(failed),
            _status(failed),
            _status(failed),
            _status(failed),
            _status(fresh),
            _status(fresh),
            _status(fresh),
        ]
    )
    runs = iter([_failure_result(), _failure_result(), _success_result(), _success_result()])
    audits: list[dict[str, object]] = []

    monkeypatch.setattr(
        service,
        "_read_ingest_status_safely",
        lambda *args, **kwargs: next(statuses),
    )
    monkeypatch.setattr(
        service,
        "run_player_log_ingest_once",
        lambda *args, **kwargs: next(runs),
    )
    monkeypatch.setattr(
        service,
        "append_audit_event",
        lambda *args, **kwargs: audits.append(kwargs),
    )

    first_failure = service.run_scheduled_player_log_ingest_once(data_root=tmp_path)
    repeated_failure = service.run_scheduled_player_log_ingest_once(data_root=tmp_path)
    recovery = service.run_scheduled_player_log_ingest_once(data_root=tmp_path)
    unchanged = service.run_scheduled_player_log_ingest_once(data_root=tmp_path)

    assert first_failure.exit_code == 1
    assert first_failure.ingest.freshness_status == failed
    assert first_failure.transition == "failure"
    assert repeated_failure.exit_code == 1
    assert repeated_failure.transition == "none"
    assert recovery.exit_code == 0
    assert recovery.transition == "recovery"
    assert unchanged.exit_code == 0
    assert unchanged.transition == "none"
    assert len(audits) == 2
    assert [event["message"] for event in audits] == [
        "Scheduled player log ingest entered failed freshness state.",
        "Scheduled player log ingest freshness recovered.",
    ]
    rendered = json.dumps(audits, sort_keys=True)
    assert str(tmp_path) not in rendered
    assert "198.51.100.77" not in rendered
    assert "token=" not in rendered
    assert "Traceback" not in rendered


def test_scheduled_audit_write_failure_is_a_nonzero_service_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from armactl import player_log_ingest_service as service

    statuses = iter(
        [
            _status(player_registry.PLAYER_LOG_INGEST_STATUS_UNAVAILABLE),
            _status(player_registry.PLAYER_LOG_INGEST_STATUS_FRESH),
        ]
    )
    monkeypatch.setattr(
        service,
        "_read_ingest_status_safely",
        lambda *args, **kwargs: next(statuses),
    )
    monkeypatch.setattr(
        service,
        "run_player_log_ingest_once",
        lambda *args, **kwargs: _success_result(),
    )
    monkeypatch.setattr(
        service,
        "append_audit_event",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AuditLogError("controlled audit failure")
        ),
    )

    result = service.run_scheduled_player_log_ingest_once(data_root=tmp_path)
    output = service.format_scheduled_player_log_ingest_result(result)

    assert result.transition == "freshness_transition"
    assert result.audit_state == "write_failed"
    assert result.exit_code == 1
    assert "audit_state=write_failed" in output

def test_cli_install_enable_disable_status_and_scheduled_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from armactl import player_log_ingest_service as service

    service_status = PlayerLogIngestServiceStatus(
        instance="default",
        cadence_seconds=120,
        service={
            "unit_name": "armactl-player-log-ingest.service",
            "exists": False,
            "active_state": "missing",
            "sub_state": "missing",
            "failed": False,
            "result": "",
            "exec_main_code": "exited",
            "exec_main_status": 0,
            "last_exit_at": "",
        },
        timer={
            "unit_name": "armactl-player-log-ingest.timer",
            "exists": False,
            "enabled": False,
            "active_state": "missing",
            "sub_state": "missing",
            "failed": False,
            "next_trigger": "",
            "last_trigger": "",
        },
        freshness=PlayerLogIngestStatus(
            instance="default",
            state="empty",
            reason="players_db_missing",
        ),
    )
    install_result = PlayerLogIngestInstallResult(
        instance="default",
        service_name="armactl-player-log-ingest.service",
        timer_name="armactl-player-log-ingest.timer",
        service_path=tmp_path / "service",
        timer_path=tmp_path / "timer",
        results=(ServiceResult(True, "installed", 0),),
    )
    action_result = PlayerLogIngestActionResult(
        action="enable",
        timer_name="armactl-player-log-ingest.timer",
        results=(ServiceResult(True, "ok", 0),),
    )
    scheduled_result = ScheduledPlayerLogIngestResult(
        ingest=_success_result(),
        skipped=False,
        transition="none",
        audit_state="not_needed",
    )

    monkeypatch.setattr(
        service,
        "install_player_log_ingest_service",
        lambda *args, **kwargs: install_result,
    )
    monkeypatch.setattr(
        service,
        "enable_player_log_ingest_timer",
        lambda *args, **kwargs: action_result,
    )
    monkeypatch.setattr(
        service,
        "disable_player_log_ingest_timer",
        lambda *args, **kwargs: PlayerLogIngestActionResult(
            action="disable",
            timer_name=action_result.timer_name,
            results=action_result.results,
        ),
    )
    monkeypatch.setattr(
        service,
        "get_player_log_ingest_service_status",
        lambda *args, **kwargs: service_status,
    )
    monkeypatch.setattr(
        service,
        "run_scheduled_player_log_ingest_once",
        lambda *args, **kwargs: scheduled_result,
    )

    runner = CliRunner()
    install = runner.invoke(main, ["players", "log-ingest", "install"])
    enable = runner.invoke(main, ["players", "log-ingest", "enable"])
    disable = runner.invoke(main, ["players", "log-ingest", "disable"])
    status = runner.invoke(main, ["players", "log-ingest", "status"])
    scheduled = runner.invoke(
        main,
        ["players", "log-ingest", "run", "--once", "--scheduled"],
    )

    assert install.exit_code == 0
    assert "Installation does not enable or start the timer" in install.output
    assert enable.exit_code == 0
    assert disable.exit_code == 0
    assert status.exit_code == 0
    assert "Service exists:  no" in status.output
    assert "Service process: exited; exit status 0" in status.output
    assert "Timer enabled:   no" in status.output
    assert "players_db_missing" in status.output
    assert scheduled.exit_code == 0
    assert scheduled.output.startswith("player_log_ingest outcome=completed")


def test_privileged_helper_allows_only_lifecycle_actions_for_ingest_units() -> None:
    from armactl.service_manager import _render_privileged_helper_script

    helper = _render_privileged_helper_script()

    assert '"armactl-player-log-ingest.service"' in helper
    assert '"armactl-player-log-ingest.timer"' in helper
    assert '"armactl-player-log-ingest@*.service"' in helper
    assert '"armactl-player-log-ingest@*.timer"' in helper
    allowed_timers = helper.split("ALLOWED_TIMERS = [", 1)[1].split("]", 1)[0]
    assert "player-log-ingest" not in allowed_timers


def test_service_foundation_has_no_daemon_route_or_session_scheduler_dependency() -> None:
    import armactl.player_log_ingest_service as service

    source = Path(service.__file__).read_text(encoding="utf-8")

    assert "threading" not in source
    assert "player_session_scheduler" not in source
    assert "web.routes" not in source
    assert "armareforger.service" not in source
    assert "time.sleep" not in source
