"""Contract tests for the synchronous supervised player-session pipeline."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from armactl import paths, player_log_events
from armactl.cli import _format_player_session_scheduler_run_result
from armactl.service_manager import ServiceResult
from armactl.web.runtime import ensure_web_db
from armactl.web.services import (
    player_live_session_scanner,
    player_registry,
    player_session_mutation,
)
from armactl.web.services import (
    player_session_scheduler_runner as runner,
)
from armactl.web.services.player_sources import CurrentPlayer, CurrentPlayerRoster

NOW = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)
PLAYER_ALPHA_ID = "11111111-1111-4111-8111-111111111111"
PLAYER_BRAVO_ID = "22222222-2222-4222-8222-222222222222"
PLAYER_CHARLIE_ID = "33333333-3333-4333-8333-333333333333"


def _players_db(data_root: Path) -> Path:
    return data_root / "default" / "players.db"


def _web_db(data_root: Path) -> Path:
    return data_root / "web" / "web.db"


def _event(reliable_id: str, observed_at: str, ref: str):
    event = player_log_events.parse_player_log_event(
        "BACKEND : Authenticated player: "
        f"rplIdentity=42 identityId={reliable_id} name=Player",
        observed_at=observed_at,
        raw_source_ref=ref,
    )
    assert event is not None
    return event


def _checkpoint(
    run_at: str,
    *,
    source_key: str = "console.log",
) -> player_registry.PlayerLogIngestCheckpoint:
    return player_registry.PlayerLogIngestCheckpoint(
        scope="instance_config_profile_console_logs",
        source_key=source_key,
        source_label="console",
        size_bytes=1,
        mtime_ns=1,
        fingerprint=source_key,
        status="scanned",
        last_scanned_at=run_at,
        updated_at=run_at,
        next_offset=1,
        coverage_started_at="2026-06-20T09:00:00+00:00",
    )


def _commit_generation(
    data_root: Path,
    events=(),
    *,
    run_at: str = "2026-06-20T11:59:00+00:00",
):
    event_rows = tuple(events)
    return player_registry.commit_player_log_ingest_generation(
        _players_db(data_root),
        event_batches=(event_rows,),
        checkpoints=(_checkpoint(run_at),),
        scope="instance_config_profile_console_logs",
        status=player_registry.PLAYER_LOG_INGEST_STATUS_FRESH,
        last_run_at=run_at,
        scanned_files=1,
        parsed_events=len(event_rows),
        skipped_files=0,
        coverage_started_at="2026-06-20T09:00:00+00:00",
    )


def _reliable_live_summary(count: int = 0):
    return player_live_session_scanner.LivePlayerSessionScanSummary(
        observed_count=count,
        reliable_evidence=True,
        success=True,
        scans_considered=1,
    )


def _maintenance_summary(
    *,
    closed: int = 0,
    deleted: int = 0,
) -> player_session_mutation.PlayerSessionMaintenanceSummary:
    return player_session_mutation.PlayerSessionMaintenanceSummary(
        stale_close=player_registry.PlayerSessionStaleCloseResult(
            sessions_closed=closed
        ),
        retention_cleanup=player_registry.PlayerSessionRetentionCleanupResult(
            sessions_deleted=deleted
        ),
    )


def _patch_successful_downstream(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        player_session_mutation,
        "run_live_player_session_scan",
        lambda *args, **kwargs: _reliable_live_summary(),
    )
    monkeypatch.setattr(
        player_session_mutation,
        "run_player_session_maintenance",
        lambda *args, **kwargs: _maintenance_summary(),
    )


def _table_count(db_path: Path, table: str) -> int:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])


def _session_count(data_root: Path) -> int:
    db_path = _players_db(data_root)
    with sqlite3.connect(db_path) as connection:
        row = connection.execute("SELECT COUNT(*) FROM player_sessions").fetchone()
    assert row is not None
    return int(row[0])


def test_scheduler_runs_synchronously_and_creates_no_automatic_web_jobs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    web_db = _web_db(tmp_path)
    ensure_web_db(web_db)
    _commit_generation(tmp_path)
    _patch_successful_downstream(monkeypatch)

    result = runner.run_player_session_scheduler_once(web_db, now=NOW)

    assert result.outcome == "completed"
    assert result.success is True
    assert result.generation_proven is True
    assert _table_count(web_db, "web_jobs") == 0
    state = player_registry.get_player_session_pipeline_state(_players_db(tmp_path))
    assert state.last_processed_ingest_generation == 1
    assert state.last_result == "completed"


def test_atomic_ingest_generation_high_water_race(tmp_path: Path) -> None:
    db_path = _players_db(tmp_path)
    player_registry.ensure_player_registry_db(db_path)
    barrier = threading.Barrier(2)
    results = []
    lock = threading.Lock()

    def commit(event) -> None:
        barrier.wait()
        result = player_registry.commit_player_log_ingest_generation(
            db_path,
            event_batches=((event,),),
            checkpoints=(
                _checkpoint(
                    "2026-06-20T12:00:00+00:00",
                    source_key=event.raw_source_ref,
                ),
            ),
            scope="instance_config_profile_console_logs",
            status=player_registry.PLAYER_LOG_INGEST_STATUS_FRESH,
            last_run_at="2026-06-20T12:00:00+00:00",
            scanned_files=1,
            parsed_events=1,
            skipped_files=0,
            coverage_started_at="2026-06-20T09:00:00+00:00",
        )
        with lock:
            results.append(
                (
                    result.freshness.completed_generation,
                    result.freshness.generation_max_event_id,
                )
            )

    threads = [
        threading.Thread(
            target=commit,
            args=(_event(PLAYER_ALPHA_ID, "2026-06-20T11:00:00+00:00", "a"),),
        ),
        threading.Thread(
            target=commit,
            args=(_event(PLAYER_BRAVO_ID, "2026-06-20T11:01:00+00:00", "b"),),
        ),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == [(1, 1), (2, 2)]
    freshness = player_registry.get_player_log_ingest_freshness(
        db_path,
        scope="instance_config_profile_console_logs",
    )
    assert freshness.completed_generation == 2
    assert freshness.generation_max_event_id == 2


def test_bounded_cursor_resume_blocks_downstream_until_backlog_clears(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = (
        _event(PLAYER_ALPHA_ID, "2026-06-20T10:00:00+00:00", "a"),
        _event(PLAYER_BRAVO_ID, "2026-06-20T10:01:00+00:00", "b"),
        _event(PLAYER_CHARLIE_ID, "2026-06-20T10:02:00+00:00", "c"),
    )
    _commit_generation(tmp_path, events)
    live_calls = []
    monkeypatch.setattr(
        player_session_mutation,
        "run_live_player_session_scan",
        lambda *args, **kwargs: live_calls.append(1) or _reliable_live_summary(),
    )
    monkeypatch.setattr(
        player_session_mutation,
        "run_player_session_maintenance",
        lambda *args, **kwargs: _maintenance_summary(),
    )

    first = runner.run_player_session_scheduler_once(
        instance="default",
        data_root=tmp_path,
        now=NOW,
        page_limit=1,
        max_pages=1,
    )
    first_state = player_registry.get_player_session_pipeline_state(_players_db(tmp_path))

    assert first.error_code == "backlog_remaining"
    assert first.failure_stage == "sessionize"
    assert first_state.last_sessionized_event_id == 1
    assert first_state.last_processed_ingest_generation == 0
    assert live_calls == []

    second = runner.run_player_session_scheduler_once(
        instance="default",
        data_root=tmp_path,
        now=NOW + timedelta(minutes=1),
        page_limit=1,
        max_pages=5,
    )
    final_state = player_registry.get_player_session_pipeline_state(_players_db(tmp_path))

    assert second.outcome == "completed"
    assert final_state.last_sessionized_event_id == 3
    assert final_state.last_processed_ingest_generation == 1
    assert _session_count(tmp_path) == 3
    assert live_calls == [1]


def test_late_event_fails_closed_before_applying_offending_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_successful_downstream(monkeypatch)
    _commit_generation(
        tmp_path,
        (_event(PLAYER_ALPHA_ID, "2026-06-20T10:00:00+00:00", "first"),),
    )
    assert runner.run_player_session_scheduler_once(
        data_root=tmp_path,
        now=NOW,
    ).outcome == "completed"
    assert _session_count(tmp_path) == 1

    _commit_generation(
        tmp_path,
        (_event(PLAYER_BRAVO_ID, "2026-06-20T09:00:00+00:00", "late"),),
        run_at="2026-06-20T12:01:00+00:00",
    )
    result = runner.run_player_session_scheduler_once(
        data_root=tmp_path,
        now=NOW + timedelta(minutes=2),
    )

    assert result.failure_stage == "sessionize"
    assert result.error_code == "historical_backfill_required"
    assert _session_count(tmp_path) == 1
    state = player_registry.get_player_session_pipeline_state(_players_db(tmp_path))
    assert state.last_processed_ingest_generation == 1
    assert state.last_sessionized_event_id == 1


@pytest.mark.parametrize(
    ("roster", "error_code"),
    [
        (
            CurrentPlayerRoster(
                available=True,
                players=(),
                total_count=4,
                source="a2s",
                status="available",
                observed_count=4,
                count_source="a2s",
                roster_available=False,
                roster_configured=True,
            ),
            "roster_unavailable",
        ),
        (
            CurrentPlayerRoster(
                available=True,
                players=(
                    CurrentPlayer(
                        display_name="Player",
                        reliable_id=PLAYER_ALPHA_ID,
                        admin_reference=PLAYER_ALPHA_ID,
                        source="a2s",
                    ),
                ),
                total_count=1,
                source="rcon.roster",
                status="available",
                observed_count=1,
                count_source="rcon",
                roster_available=True,
                roster_configured=True,
                rcon_status="ok",
                roster_source="rcon",
                query_attempt_count=1,
            ),
            "mixed_roster_source",
        ),
        (
            CurrentPlayerRoster(
                available=True,
                players=(
                    CurrentPlayer(
                        display_name="Player",
                        reliable_id=PLAYER_ALPHA_ID,
                        admin_reference=PLAYER_ALPHA_ID,
                        source="rcon.guid",
                    ),
                    CurrentPlayer(
                        display_name="Player",
                        reliable_id=PLAYER_ALPHA_ID,
                        admin_reference=PLAYER_ALPHA_ID,
                        source="rcon.guid",
                    ),
                ),
                total_count=2,
                source="rcon.roster",
                status="available",
                observed_count=2,
                count_source="rcon",
                roster_available=True,
                roster_configured=True,
                rcon_status="ok",
                roster_source="rcon",
                query_attempt_count=1,
            ),
            "duplicate_reliable_id",
        ),
        (
            CurrentPlayerRoster(
                available=False,
                players=(),
                total_count=0,
                source="unavailable",
                status="unavailable",
                roster_available=False,
                roster_configured=True,
            ),
            "roster_unavailable",
        ),
        (
            CurrentPlayerRoster(
                available=True,
                players=(),
                total_count=0,
                source="rcon.roster",
                status="available",
                observed_count=0,
                count_source="rcon",
                roster_available=True,
                roster_configured=True,
                rcon_status="failed",
                roster_source="rcon",
                query_attempt_count=1,
            ),
            "rcon_failed",
        ),
        (
            CurrentPlayerRoster(
                available=True,
                players=(
                    CurrentPlayer(
                        display_name="Player",
                        reliable_id=PLAYER_ALPHA_ID,
                        admin_reference=PLAYER_ALPHA_ID,
                        source="rcon.guid",
                    ),
                ),
                total_count=2,
                source="rcon.roster",
                status="available",
                observed_count=2,
                count_source="rcon",
                roster_available=True,
                roster_configured=True,
                rcon_status="ok",
                roster_source="rcon",
                query_attempt_count=1,
                count_mismatch=True,
            ),
            "count_mismatch",
        ),
    ],
)
def test_exact_reliable_roster_gate_rejects_unreliable_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    roster: CurrentPlayerRoster,
    error_code: str,
) -> None:
    from armactl.web.services import player_sources

    monkeypatch.setattr(player_sources, "load_current_player_roster", lambda instance: roster)
    result = player_live_session_scanner.scan_live_player_sessions_once(
        "default",
        data_root=tmp_path,
        observed_at="2026-06-20T12:00:00+00:00",
    )

    assert result.success is False
    assert result.reliable_evidence is False
    assert result.reliability_error_code == error_code
    assert not _players_db(tmp_path).exists()


def test_exact_reliable_roster_gate_accepts_one_rcon_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from armactl.web.services import player_sources

    roster = CurrentPlayerRoster(
        available=True,
        players=(
            CurrentPlayer(
                display_name="Player",
                reliable_id=PLAYER_ALPHA_ID,
                admin_reference=PLAYER_ALPHA_ID,
                source="rcon.guid",
            ),
        ),
        total_count=1,
        source="rcon.roster",
        status="available",
        observed_count=1,
        count_source="rcon",
        roster_available=True,
        roster_configured=True,
        rcon_status="ok",
        roster_source="rcon",
        query_attempt_count=1,
    )
    monkeypatch.setattr(player_sources, "load_current_player_roster", lambda instance: roster)

    result = player_live_session_scanner.scan_live_player_sessions_once(
        "default",
        data_root=tmp_path,
        observed_at="2026-06-20T12:00:00+00:00",
    )

    assert result.success is True
    assert result.reliable_evidence is True
    assert result.sessions_created == 1


def test_manual_and_automatic_mutations_share_nonblocking_lock(
    tmp_path: Path,
) -> None:
    _commit_generation(tmp_path)
    with player_session_mutation.acquire_player_session_mutation_lock(
        "default",
        data_root=tmp_path,
    ):
        automatic = runner.run_player_session_scheduler_once(
            data_root=tmp_path,
            now=NOW,
        )
        with pytest.raises(player_session_mutation.PlayerSessionMutationBusyError):
            player_session_mutation.run_player_log_sessionization(
                "default",
                data_root=tmp_path,
            )

    assert automatic.failure_stage == "lock"
    assert automatic.error_code == "db_contention"


def test_process_interruption_replays_from_durable_page_cursor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _commit_generation(
        tmp_path,
        (_event(PLAYER_ALPHA_ID, "2026-06-20T10:00:00+00:00", "one"),),
    )
    monkeypatch.setattr(
        player_session_mutation,
        "run_live_player_session_scan",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("interrupted")),
    )

    interrupted = runner.run_player_session_scheduler_once(
        data_root=tmp_path,
        now=NOW,
    )
    interrupted_state = player_registry.get_player_session_pipeline_state(
        _players_db(tmp_path)
    )

    assert interrupted.error_code == "live_scan_failed"
    assert interrupted_state.last_sessionized_event_id == 1
    assert interrupted_state.last_processed_ingest_generation == 0
    assert _session_count(tmp_path) == 1

    _patch_successful_downstream(monkeypatch)
    replay = runner.run_player_session_scheduler_once(
        data_root=tmp_path,
        now=NOW + timedelta(minutes=1),
    )

    assert replay.outcome == "completed"
    assert replay.session_events_scanned == 0
    assert _session_count(tmp_path) == 1


def test_maintenance_due_failure_retries_and_not_due_skips(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _commit_generation(tmp_path)
    monkeypatch.setattr(
        player_session_mutation,
        "run_live_player_session_scan",
        lambda *args, **kwargs: _reliable_live_summary(),
    )
    calls = []

    def maintenance(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("failed")
        return _maintenance_summary(closed=1, deleted=2)

    monkeypatch.setattr(
        player_session_mutation,
        "run_player_session_maintenance",
        maintenance,
    )

    failed = runner.run_player_session_scheduler_once(data_root=tmp_path, now=NOW)
    assert failed.failure_stage == "maintenance"
    state = player_registry.get_player_session_pipeline_state(_players_db(tmp_path))
    assert state.last_processed_ingest_generation == 0
    assert state.last_maintenance_at == ""

    retried = runner.run_player_session_scheduler_once(
        data_root=tmp_path,
        now=NOW + timedelta(minutes=1),
    )
    assert retried.outcome == "completed"
    assert retried.maintenance_performed is True
    assert retried.stale_sessions_closed == 1
    assert retried.retained_sessions_deleted == 2

    _commit_generation(
        tmp_path,
        run_at="2026-06-20T12:02:00+00:00",
    )
    not_due = runner.run_player_session_scheduler_once(
        data_root=tmp_path,
        now=NOW + timedelta(minutes=5),
    )
    assert not_due.outcome == "completed"
    assert not_due.maintenance_due is False
    assert calls == [1, 1]


def test_legacy_scheduler_timestamps_are_diagnostics_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    web_db = _web_db(tmp_path)
    ensure_web_db(web_db)
    with sqlite3.connect(web_db) as connection:
        connection.execute(
            """
            INSERT INTO web_player_session_scheduler_state(
                instance, job_kind, last_attempt_at, last_success_at,
                last_failure_at, next_due_at, failure_count, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "default",
                "players:scan-live-sessions",
                "2099-01-01T00:00:00+00:00",
                "2099-01-01T00:00:00+00:00",
                "",
                "2099-01-01T00:00:00+00:00",
                0,
                "2099-01-01T00:00:00+00:00",
            ),
        )
    _commit_generation(tmp_path)
    _patch_successful_downstream(monkeypatch)

    result = runner.run_player_session_scheduler_once(web_db, now=NOW)
    status = runner.read_player_session_scheduler_status(web_db, now=NOW)

    assert result.outcome == "completed"
    assert status.legacy_state_row_count == 1
    assert status.last_success_at == NOW.isoformat()
    assert status.last_success_at != "2099-01-01T00:00:00+00:00"


def test_status_creates_and_migrates_nothing(tmp_path: Path) -> None:
    missing_root = tmp_path / "missing"
    status = runner.read_player_session_scheduler_status(data_root=missing_root, now=NOW)
    assert status.reason == "players_db_missing"
    assert not missing_root.exists()

    db_path = _players_db(tmp_path)
    player_registry.ensure_player_registry_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE player_registry_schema_meta SET value = '11' "
            "WHERE key = 'schema_version'"
        )
        connection.execute("DROP TABLE player_session_pipeline_state")
    status = runner.read_player_session_scheduler_status(data_root=tmp_path, now=NOW)
    with sqlite3.connect(db_path) as connection:
        version = connection.execute(
            "SELECT value FROM player_registry_schema_meta "
            "WHERE key = 'schema_version'"
        ).fetchone()
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='player_session_pipeline_state'"
        ).fetchone()

    assert status.reason == "pipeline_state_missing"
    assert version == ("11",)
    assert table is None


def test_generated_unit_install_is_disabled_first_and_preserves_reinstall_enablement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from armactl import player_session_pipeline_service as service

    ok = ServiceResult(True, "ok", 0)
    calls = []
    statuses = iter(
        (
            {"exists": False, "enabled": False, "active": False},
            {"exists": True, "enabled": True, "active": True},
        )
    )
    monkeypatch.setattr(service, "get_systemd_unit_status", lambda *a, **k: next(statuses))
    monkeypatch.setattr(
        service.player_log_ingest_service,
        "get_player_log_ingest_service_status",
        lambda *a, **k: SimpleNamespace(
            timer={"exists": True, "enabled": True},
            freshness=SimpleNamespace(freshness_status="fresh"),
        ),
    )
    monkeypatch.setattr(service, "check_player_session_pipeline_runtime", lambda *a: ok)
    monkeypatch.setattr(service, "resolve_linux_user", lambda: "deus")
    monkeypatch.setattr(service, "install_systemd_unit_file", lambda *a, **k: ok)
    monkeypatch.setattr(service, "daemon_reload", lambda: ok)
    monkeypatch.setattr(service, "install_privileged_systemctl_channel", lambda: (ok,))
    monkeypatch.setattr(
        service,
        "stop_service",
        lambda name: calls.append(("stop", name)) or ok,
    )
    monkeypatch.setattr(
        service,
        "disable_service",
        lambda name: calls.append(("disable", name)) or ok,
    )
    monkeypatch.setattr(
        service,
        "enable_service",
        lambda name: calls.append(("enable", name)) or ok,
    )
    monkeypatch.setattr(
        service,
        "start_service",
        lambda name: calls.append(("start", name)) or ok,
    )

    first = service.install_player_session_pipeline_service()
    first_calls = tuple(calls)
    calls.clear()
    reinstall = service.install_player_session_pipeline_service()

    assert first.success is True
    assert any(action == "stop" for action, _name in first_calls)
    assert any(action == "disable" for action, _name in first_calls)
    assert all(action not in {"enable", "start"} for action, _name in first_calls)
    assert reinstall.success is True
    assert reinstall.preserved_enabled is True
    assert calls == []

    enabled = service.enable_player_session_pipeline_timer()
    assert enabled.success is True
    assert [action for action, _name in calls] == ["enable", "start"]


def test_generated_units_are_completion_relative_and_do_not_restart_game_or_web() -> None:
    from armactl import player_session_pipeline_service as service

    project_root = paths.project_root()
    service_text = service.render_player_session_pipeline_service_unit(
        instance="default",
        data_root=paths.DEFAULT_DATA_ROOT,
        project_root=project_root,
        python_bin=project_root / ".venv" / "bin" / "python",
        user="deus",
        home_dir=Path("/home/deus"),
    )
    timer_text = service.render_player_session_pipeline_timer_unit(
        instance="default",
        service_name=paths.PLAYER_SESSION_PIPELINE_SERVICE_NAME,
        project_root=project_root,
    )

    assert "OnActiveSec=150s" in timer_text
    assert "OnUnitInactiveSec=120s" in timer_text
    assert "TimeoutStartSec=240s" in service_text
    assert "players sessions scheduler run --once --scheduled" in service_text
    encoded = (service_text + timer_text).casefold()
    assert "armareforger" not in encoded
    assert "armactl-web.service" not in encoded
    assert "restart" not in encoded


def test_exact_reliable_empty_rcon_roster_passes_without_fake_players(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from armactl.web.services import player_sources

    roster = CurrentPlayerRoster(
        available=True,
        players=(),
        total_count=0,
        source="rcon.roster",
        status="available",
        observed_count=0,
        count_source="rcon",
        roster_available=True,
        roster_configured=True,
        rcon_status="ok",
        roster_source="rcon",
        query_attempt_count=1,
    )
    monkeypatch.setattr(player_sources, "load_current_player_roster", lambda instance: roster)

    result = player_live_session_scanner.scan_live_player_sessions_once(
        "default",
        data_root=tmp_path,
        observed_at="2026-06-20T12:00:00+00:00",
    )

    assert result.success is True
    assert result.reliable_evidence is True
    assert result.observed_count == 0
    assert result.sessions_created == 0
    assert not _players_db(tmp_path).exists()


def test_status_reports_busy_interrupted_state_read_only(tmp_path: Path) -> None:
    db_path = _players_db(tmp_path)
    player_registry.ensure_player_registry_db(db_path)
    player_registry.upsert_player_session_pipeline_state(
        db_path,
        player_registry.PlayerSessionPipelineState(
            instance="default",
            last_attempt_at=NOW.isoformat(),
            last_started_at=NOW.isoformat(),
            current_generation_max_event_id=7,
            last_result="sessionizing",
            interrupted=True,
            updated_at=NOW.isoformat(),
        ),
    )

    with player_session_mutation.acquire_player_session_mutation_lock(
        "default",
        data_root=tmp_path,
    ):
        status = runner.read_player_session_scheduler_status(
            data_root=tmp_path,
            now=NOW + timedelta(minutes=1),
        )

    assert status.state == "degraded"
    assert status.reason == "interrupted"
    assert status.interrupted is True
    assert status.lock_state == "busy"
    assert status.current_generation_max_event_id == 7


def test_no_new_generation_is_a_successful_noop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_successful_downstream(monkeypatch)
    _commit_generation(tmp_path)
    first = runner.run_player_session_scheduler_once(data_root=tmp_path, now=NOW)
    second = runner.run_player_session_scheduler_once(
        data_root=tmp_path,
        now=NOW + timedelta(minutes=1),
    )

    assert first.outcome == "completed"
    assert second.outcome == "no_new_generation"
    assert second.success is True
    assert second.exit_code == 0
    assert second.to_dict()["success"] is True


def test_status_reports_last_failed_cycle_as_failed(tmp_path: Path) -> None:
    db_path = _players_db(tmp_path)
    player_registry.ensure_player_registry_db(db_path)
    player_registry.upsert_player_session_pipeline_state(
        db_path,
        player_registry.PlayerSessionPipelineState(
            instance="default",
            last_attempt_at=NOW.isoformat(),
            last_completed_at=NOW.isoformat(),
            last_failure_at=NOW.isoformat(),
            consecutive_failures=2,
            last_failure_stage="live_scan",
            last_result="failed",
            last_error_code="rcon_failed",
            updated_at=NOW.isoformat(),
        ),
    )

    status = runner.read_player_session_scheduler_status(
        data_root=tmp_path,
        now=NOW + timedelta(minutes=1),
    )

    assert status.state == "failed"
    assert status.reason == "rcon_failed"
    assert status.consecutive_failures == 2


def test_scheduled_summary_includes_bounded_failure_diagnostics() -> None:
    from armactl.cli import _format_player_session_scheduler_run_result

    result = runner.PlayerSessionSchedulerRunResult(
        instance="default",
        checked_at=NOW.isoformat(),
        outcome="failed",
        failure_stage="live_scan",
        error_code="rcon_failed",
    )

    summary = _format_player_session_scheduler_run_result(result)

    assert "outcome=failed" in summary
    assert "failure_stage=live_scan" in summary
    assert "error_code=rcon_failed" in summary


@pytest.mark.parametrize(
    ("timer", "freshness_status"),
    [
        ({"exists": False, "enabled": False}, "fresh"),
        ({"exists": True, "enabled": False}, "fresh"),
        ({"exists": True, "enabled": True}, "failed"),
    ],
)
def test_enable_refuses_when_ingest_readiness_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    timer: dict[str, bool],
    freshness_status: str,
) -> None:
    from armactl import player_session_pipeline_service as service

    calls = []
    monkeypatch.setattr(
        service.player_log_ingest_service,
        "get_player_log_ingest_service_status",
        lambda *a, **k: SimpleNamespace(
            timer=timer,
            freshness=SimpleNamespace(freshness_status=freshness_status),
        ),
    )
    monkeypatch.setattr(
        service,
        "enable_service",
        lambda name: calls.append(("enable", name))
        or ServiceResult(True, "ok", 0),
    )
    monkeypatch.setattr(
        service,
        "start_service",
        lambda name: calls.append(("start", name))
        or ServiceResult(True, "ok", 0),
    )

    result = service.enable_player_session_pipeline_timer(data_root=Path("/tmp/data"))

    assert result.success is False
    assert result.exit_code == 1
    assert calls == []


def test_sessionizer_hard_caps_each_pass_at_five_thousand_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from armactl.web.services import player_sessionizer

    total_events = 5001

    def list_page(
        db_path,
        *,
        after_event_time="",
        after_event_id=0,
        through_event_id=None,
        limit=None,
    ):
        del db_path, after_event_time
        high_water = through_event_id or total_events
        remaining = max(0, high_water - after_event_id)
        count = min(remaining, limit or remaining)
        return [
            SimpleNamespace(event_id=after_event_id + index + 1)
            for index in range(count)
        ]

    monkeypatch.setattr(
        player_sessionizer.player_registry,
        "list_player_log_events_for_sessionization",
        list_page,
    )
    monkeypatch.setattr(
        player_sessionizer.player_registry,
        "has_late_player_log_event_for_sessionization",
        lambda *a, **k: False,
    )
    monkeypatch.setattr(
        player_sessionizer,
        "_validate_event_page",
        lambda events, **kwargs: (
            f"2026-06-20T{events[-1].event_id // 3600:02d}:00:00+00:00",
            events[-1].event_id,
        ),
    )
    monkeypatch.setattr(
        player_sessionizer,
        "_sessionize_event_page",
        lambda db_path, events: player_sessionizer.PlayerLogSessionizationSummary(
            events_scanned=len(events),
            pages_completed=1,
        ),
    )

    result = player_sessionizer.sessionize_stored_player_log_events(
        tmp_path / "players.db",
        through_event_id=total_events,
        page_limit=1000,
        max_pages=10,
    )

    assert result.events_scanned == 5000
    assert result.pages_completed == 5
    assert result.last_event_id == 5000
    assert result.backlog_remaining is True


def test_scheduler_journal_output_is_counts_only() -> None:
    result = runner.PlayerSessionSchedulerRunResult(
        instance="secret-instance",
        checked_at="2026-06-20T12:00:00+00:00",
        outcome="failed",
        failure_stage="live_scan token=raw-secret",
        error_code="token=raw-secret 198.51.100.9 /home/deus/private.log",
        session_events_scanned=2,
        live_observed_count=1,
    )

    output = _format_player_session_scheduler_run_result(result)

    assert "secret-instance" not in output
    assert "raw-secret" not in output
    assert "198.51.100.9" not in output
    assert "/home/deus" not in output
    assert "live_scan" not in output
    assert "failure_stage=unknown" in output
    assert "error_code=unknown" in output
    assert "session_events=2" in output
    assert "live_observed=1" in output
