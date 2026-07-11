"""Tests for the shared synchronous player-log ingest and foreground CLI."""

from __future__ import annotations

import json
import multiprocessing
import os
import sqlite3
import threading
from pathlib import Path

import pytest
from click.testing import CliRunner

from armactl import paths
from armactl.cli import main
from armactl.web.services import (
    player_current_enrichment,
    player_log_ingest,
    player_registry,
)

PLAYER_ALPHA_ID = "11111111-1111-4111-8111-111111111111"
PLAYER_BRAVO_ID = "22222222-2222-4222-8222-222222222222"


def _write_console_log(
    data_root: Path,
    lines: list[str],
    *,
    run: str = "2026-07-10-run",
) -> Path:
    path = data_root / "default" / "config" / "logs" / run / "console.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _auth_line(player_id: str, name: str, *, rpl_identity: int) -> str:
    return (
        "12:00:00.000 BACKEND : Authenticated player: "
        f"rplIdentity={rpl_identity} identityId={player_id} name={name}"
    )


def _kill_line() -> str:
    return (
        "12:05:00.000 SCRIPT : INFO: KILL ENEMY: Bravo Two "
        f"(playerID = 8 | UUID = {PLAYER_BRAVO_ID}) from FIA faction "
        "at <10 20 30> was killed by Alpha One "
        f"(playerID = 7 | UUID = {PLAYER_ALPHA_ID}) from US faction "
        "who was at that time at <40 50 60> [64.5m away from the corpse]. "
        "With last inflicted damage type Projectile to the 'Head' hit zone"
    )


def _event_count(db_path: Path) -> int:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute("SELECT COUNT(*) FROM player_log_events").fetchone()
    assert row is not None
    return int(row[0])


def _invoke(*args: str):
    return CliRunner().invoke(main, list(args))


def _abrupt_lock_owner(
    data_root: str,
    ready,
    exit_now,
) -> None:
    with player_log_ingest.acquire_player_log_ingest_lock(
        data_root=Path(data_root)
    ):
        ready.set()
        exit_now.wait(timeout=5)
        os._exit(0)


def test_shared_service_skips_unchanged_and_ingests_appended_evidence(
    tmp_path: Path,
) -> None:
    log_path = _write_console_log(
        tmp_path,
        [_auth_line(PLAYER_ALPHA_ID, "Alpha One", rpl_identity=42)],
    )
    db_path = tmp_path / "default" / "players.db"

    first = player_log_ingest.run_player_log_ingest_once(data_root=tmp_path)
    unchanged = player_log_ingest.run_player_log_ingest_once(data_root=tmp_path)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(_auth_line(PLAYER_BRAVO_ID, "Bravo Two", rpl_identity=43) + "\n")
    appended = player_log_ingest.run_player_log_ingest_once(data_root=tmp_path)

    assert first.success is True
    assert first.files_scanned == 1
    assert first.stored_events == 1
    assert first.checkpoint_updated is True
    assert unchanged.success is True
    assert unchanged.files_selected_for_scan == 0
    assert unchanged.stored_events == 0
    assert unchanged.skipped_reason_counts == {"unchanged": 1}
    assert unchanged.checkpoint_updated is False
    assert appended.success is True
    assert appended.files_scanned == 1
    assert appended.parsed_events == 2
    assert appended.stored_events == 1
    assert appended.duplicate_events == 1
    assert _event_count(db_path) == 2


def test_shared_service_controls_missing_and_oversized_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_console_log(
        tmp_path,
        [_auth_line(PLAYER_ALPHA_ID, "Alpha One", rpl_identity=42)],
        run="allowed",
    )
    oversized = _write_console_log(tmp_path, ["oversized"], run="oversized")
    max_bytes = player_log_ingest.player_log_collector.DEFAULT_MAX_FILE_BYTES
    oversized.write_bytes(b"x" * (max_bytes + 1))

    partial = player_log_ingest.run_player_log_ingest_once(data_root=tmp_path)

    assert partial.success is True
    assert partial.freshness_status == player_registry.PLAYER_LOG_INGEST_STATUS_PARTIAL
    assert partial.files_scanned == 1
    assert partial.skipped_reason_counts["file_too_large"] == 1

    missing = tmp_path / "default" / "config" / "logs" / "missing" / "console.log"
    monkeypatch.setattr(
        player_log_ingest,
        "resolve_allowlisted_player_log_paths",
        lambda *args, **kwargs: (missing,),
    )

    missing_result = player_log_ingest.run_player_log_ingest_once(data_root=tmp_path)

    assert missing_result.completed is True
    assert missing_result.freshness_status == player_registry.PLAYER_LOG_INGEST_STATUS_PARTIAL
    assert missing_result.files_selected_for_scan == 0
    assert missing_result.skipped_reason_counts == {"missing_file": 1}
    assert str(missing) not in json.dumps(missing_result.to_dict())


def test_uncontrolled_failure_does_not_mark_fresh_or_leak_details(
    tmp_path: Path,
    monkeypatch,
) -> None:
    log_path = _write_console_log(
        tmp_path,
        [_auth_line(PLAYER_ALPHA_ID, "Alpha One", rpl_identity=42)],
    )
    first = player_log_ingest.run_player_log_ingest_once(data_root=tmp_path)
    assert first.success is True
    previous_success_at = first.freshness_at

    with log_path.open("a", encoding="utf-8") as handle:
        handle.write("DEFAULT : changed\n")
    raw_failure = f"failed at {log_path} 198.51.100.77 token=secret-value"

    def fail_collect(*args, **kwargs):
        raise RuntimeError(raw_failure)

    monkeypatch.setattr(
        player_log_ingest.player_log_collector,
        "collect_player_log_events",
        fail_collect,
    )

    failed = player_log_ingest.run_player_log_ingest_once(data_root=tmp_path)
    status = player_log_ingest.read_player_log_ingest_status(data_root=tmp_path)
    rendered = json.dumps(failed.to_dict(), sort_keys=True)

    assert failed.outcome == "failed"
    assert failed.failure_code == "collector_failed"
    assert failed.exit_code == 1
    assert failed.checkpoint_updated is False
    assert status.freshness_status == player_registry.PLAYER_LOG_INGEST_STATUS_FAILED
    assert status.last_success_at == previous_success_at
    assert raw_failure not in rendered
    assert str(log_path) not in rendered
    assert "198.51.100.77" not in rendered
    assert "secret-value" not in rendered


def test_concurrent_invocation_is_rejected_and_process_death_recovers_lock(
    tmp_path: Path,
) -> None:
    context = multiprocessing.get_context("fork")
    ready = context.Event()
    exit_now = context.Event()
    owner = context.Process(
        target=_abrupt_lock_owner,
        args=(str(tmp_path), ready, exit_now),
    )
    owner.start()
    assert ready.wait(timeout=5)

    busy = player_log_ingest.run_player_log_ingest_once(data_root=tmp_path)

    assert busy.busy is True
    assert busy.failure_code == "already_running"
    assert not (tmp_path / "default" / "players.db").exists()
    assert player_log_ingest.player_log_ingest_lock_path(data_root=tmp_path).exists()

    exit_now.set()
    owner.join(timeout=5)
    assert owner.is_alive() is False
    assert owner.exitcode == 0
    owner.close()

    recovered = player_log_ingest.run_player_log_ingest_once(data_root=tmp_path)

    assert recovered.completed is True
    assert recovered.freshness_status == player_registry.PLAYER_LOG_INGEST_STATUS_NO_LOGS


def test_status_is_read_only_and_missing_db_does_not_create_state(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "default" / "players.db"

    missing = player_log_ingest.read_player_log_ingest_status(data_root=tmp_path)
    missing_cli = _invoke(
        "players",
        "log-ingest",
        "status",
        "--data-root",
        str(tmp_path),
    )

    assert missing.state == "empty"
    assert missing.reason == "players_db_missing"
    assert missing_cli.exit_code == 0
    assert "players_db_missing" in missing_cli.output
    assert not db_path.exists()

    run = player_log_ingest.run_player_log_ingest_once(data_root=tmp_path)
    assert run.completed is True
    before = db_path.read_bytes()
    before_mtime_ns = db_path.stat().st_mtime_ns

    status_cli = _invoke(
        "players",
        "log-ingest",
        "status",
        "--data-root",
        str(tmp_path),
    )
    status = player_log_ingest.read_player_log_ingest_status(data_root=tmp_path)

    assert status_cli.exit_code == 0
    assert status.state == "available"
    assert status.freshness_status == player_registry.PLAYER_LOG_INGEST_STATUS_NO_LOGS
    assert db_path.read_bytes() == before
    assert db_path.stat().st_mtime_ns == before_mtime_ns

def test_invalid_instance_fails_closed_without_touching_default(
    tmp_path: Path,
) -> None:
    result = player_log_ingest.run_player_log_ingest_once(
        "../default",
        data_root=tmp_path,
    )
    status = player_log_ingest.read_player_log_ingest_status(
        "../default",
        data_root=tmp_path,
    )

    assert result.instance == "invalid"
    assert result.outcome == "failed"
    assert result.failure_code == "invalid_instance"
    assert result.exit_code == 1
    assert status.instance == "invalid"
    assert status.state == "invalid"
    assert status.reason == "invalid_instance"
    assert list(tmp_path.iterdir()) == []

    with pytest.raises(paths.InvalidInstanceNameError):
        player_log_ingest.player_log_ingest_lock_path(
            "../default",
            data_root=tmp_path,
        )



def test_manual_job_and_cli_use_the_same_synchronous_service(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from armactl.web.jobs import get_job, player_logs

    calls: list[tuple[str, Path, int]] = []
    main_thread_id = threading.get_ident()

    def fake_run(instance="default", *, data_root, **kwargs):
        calls.append((instance, Path(data_root), threading.get_ident()))
        return player_log_ingest.PlayerLogIngestResult(
            instance=instance,
            files_considered=1,
            files_selected_for_scan=1,
            files_requested=1,
            files_scanned=1,
            scanned_lines=2,
            parsed_events=1,
            stored_events=1,
            checkpoint_updated=True,
            freshness_status=player_registry.PLAYER_LOG_INGEST_STATUS_FRESH,
            freshness_at="2026-07-11T12:00:00+00:00",
            collection_success=True,
        )

    monkeypatch.setattr(player_log_ingest, "run_player_log_ingest_once", fake_run)
    monkeypatch.setattr(
        player_logs,
        "start_player_log_collection_worker",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("CLI must not start the web daemon worker")
        ),
    )

    web_db = tmp_path / "web" / "web.db"
    queued = player_logs.ensure_player_log_collection_job(
        web_db,
        requested_by_username="owner",
    )
    dispatch = player_logs.dispatch_player_log_collection_job(web_db, queued[0].id)
    job = get_job(web_db, queued[0].id)

    cli = _invoke(
        "players",
        "log-ingest",
        "run",
        "--once",
        "--data-root",
        str(tmp_path),
    )

    assert dispatch.ran is True
    assert job is not None and job.status == "succeeded"
    assert cli.exit_code == 0
    assert len(calls) == 2
    assert calls[0][:2] == ("default", tmp_path)
    assert calls[1][:2] == ("default", tmp_path)
    assert calls[1][2] == main_thread_id
    assert "synchronous --once" in cli.output


def test_cli_requires_explicit_once_flag(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        player_log_ingest,
        "run_player_log_ingest_once",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("ingest must not start without --once")
        ),
    )

    result = _invoke(
        "players",
        "log-ingest",
        "run",
        "--data-root",
        str(tmp_path),
    )

    assert result.exit_code == 1
    assert "Only --once is supported" in result.output
    assert not (tmp_path / "default" / "players.db").exists()


def test_cli_waits_for_service_completion_without_starting_daemon(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from armactl.web.jobs import player_logs

    entered = threading.Event()
    release = threading.Event()
    invocation_done = threading.Event()
    results = []

    def blocking_run(instance="default", *, data_root, **kwargs):
        entered.set()
        assert release.wait(timeout=5)
        return player_log_ingest.PlayerLogIngestResult(
            instance=instance,
            freshness_status=player_registry.PLAYER_LOG_INGEST_STATUS_NO_LOGS,
            freshness_at="2026-07-11T12:00:00+00:00",
            collection_success=True,
        )

    monkeypatch.setattr(player_log_ingest, "run_player_log_ingest_once", blocking_run)
    monkeypatch.setattr(
        player_logs,
        "start_player_log_collection_worker",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("foreground CLI must not start a daemon worker")
        ),
    )

    def invoke_cli() -> None:
        results.append(
            _invoke(
                "players",
                "log-ingest",
                "run",
                "--once",
                "--data-root",
                str(tmp_path),
            )
        )
        invocation_done.set()

    thread = threading.Thread(target=invoke_cli)
    thread.start()
    assert entered.wait(timeout=5)
    assert invocation_done.is_set() is False
    release.set()
    thread.join(timeout=5)

    assert thread.is_alive() is False
    assert invocation_done.is_set() is True
    assert results[0].exit_code == 0


def test_cli_failure_output_and_audit_are_sanitized(
    tmp_path: Path,
    monkeypatch,
) -> None:
    log_path = _write_console_log(tmp_path, ["DEFAULT : changed"])
    raw_failure = f"boom {log_path} 203.0.113.44 token=private-token"

    def fail_collect(*args, **kwargs):
        raise RuntimeError(raw_failure)

    monkeypatch.setattr(
        player_log_ingest.player_log_collector,
        "collect_player_log_events",
        fail_collect,
    )

    result = _invoke(
        "players",
        "log-ingest",
        "run",
        "--once",
        "--data-root",
        str(tmp_path),
    )
    audit_text = (tmp_path / "logs" / "web" / "audit.log").read_text(
        encoding="utf-8"
    )
    rendered = result.output + audit_text

    assert result.exit_code == 1
    assert "failure_code=collector_failed" not in result.output
    assert "Failure code:    collector_failed" in result.output
    assert raw_failure not in rendered
    assert str(log_path) not in rendered
    assert "203.0.113.44" not in rendered
    assert "private-token" not in rendered


def test_successful_one_shot_supplies_freshness_to_read_only_stats_enrichment(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "default" / "players.db"
    player_registry.observe_player_session(
        db_path,
        reliable_id=PLAYER_ALPHA_ID,
        display_name="Alpha One",
        source=player_registry.PLAYER_SESSION_SOURCE_BACKEND_AUTH,
        confidence=player_registry.PLAYER_SESSION_CONFIDENCE_HIGH,
        observed_at="2026-07-10T12:00:00+00:00",
    )
    _write_console_log(tmp_path, [_kill_line()])

    run = player_log_ingest.run_player_log_ingest_once(data_root=tmp_path)
    before = db_path.read_bytes()
    enrichment = player_current_enrichment.load_current_player_enrichment(
        data_root=tmp_path,
        reliable_ids=[PLAYER_ALPHA_ID],
        now_at=run.freshness_at,
    )[PLAYER_ALPHA_ID]

    assert run.success is True
    assert run.stored_events == 1
    assert enrichment.stats_available is True
    assert enrichment.stats_freshness_status == "fresh"
    assert enrichment.kills == 1
    assert db_path.read_bytes() == before
