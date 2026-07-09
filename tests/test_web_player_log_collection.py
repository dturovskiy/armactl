"""Tests for web-triggered manual player log collection jobs."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from web_route_helpers import _client, _form_token, _login

from armactl.web.auth.permissions import PLAYERS_VIEW
from armactl.web.auth.setup import setup_owner_user

PLAYER_ALPHA_ID = "11111111-1111-4111-8111-111111111111"
PLAYER_CHARLIE_ID = "33333333-3333-4333-8333-333333333333"


def _setup_owner_client(tmp_path: Path):
    from armactl.web.app import create_app

    password = "owner player log collection password"
    setup_owner_user(tmp_path, "owner", password)
    client = _client(create_app(data_root=tmp_path))
    response = _login(client, "owner", password)
    assert response.status_code == 303
    return client


def _history_csrf_token(client) -> str:
    response = client.get("/players/history", follow_redirects=False)
    assert response.status_code == 200
    return _form_token(response.text)


def _audit_events(data_root: Path) -> list[dict]:
    audit_path = data_root / "logs" / "web" / "audit.log"
    if not audit_path.exists():
        return []
    return [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]


def _write_console_log(data_root: Path, lines: list[str], *, run: str = "run-1") -> Path:
    log_path = data_root / "default" / "config" / "logs" / run / "console.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return log_path


def _event_rows(db_path: Path) -> list[dict[str, object]]:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT *
            FROM player_log_events
            ORDER BY event_id ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _fixture_lines() -> list[str]:
    return [
        "BACKEND : Authenticated player: "
        f"rplIdentity=42 identityId={PLAYER_ALPHA_ID} name=Alpha One",
        "NETWORK : ### Updating player: PlayerId=7, Name=Alpha One, "
        f"rplIdentity=42, IdentityId={PLAYER_ALPHA_ID}",
        "DEFAULT : unrelated server message",
    ]


def test_collect_logs_route_requires_auth_players_permission_and_csrf(
    tmp_path: Path,
    monkeypatch,
    set_web_owner_permissions,
) -> None:
    from armactl.web.app import create_app
    from armactl.web.jobs import player_logs

    monkeypatch.setattr(
        player_logs,
        "start_player_log_collection_worker",
        lambda db_path, job_id: (_ for _ in ()).throw(AssertionError("worker started")),
    )

    anonymous = _client(create_app(data_root=tmp_path))
    response = anonymous.post(
        "/players/history/collect-logs",
        data={"csrf_token": "invalid"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"

    client = _setup_owner_client(tmp_path)
    csrf_token = _history_csrf_token(client)

    set_web_owner_permissions(set())
    response = client.post(
        "/players/history/collect-logs",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.text == "Permission denied."

    set_web_owner_permissions({PLAYERS_VIEW})
    response = client.post(
        "/players/history/collect-logs",
        data={"csrf_token": "invalid"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.text == "Invalid CSRF token."


def test_collect_logs_accepts_refreshed_form_csrf_token(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from armactl.web.jobs import player_logs

    started_jobs: list[int] = []
    monkeypatch.setattr(
        player_logs,
        "start_player_log_collection_worker",
        lambda db_path, job_id: started_jobs.append(job_id),
    )
    client = _setup_owner_client(tmp_path)
    page = client.get("/players/history", follow_redirects=False)
    token_response = client.get("/auth/csrf-token")

    assert page.status_code == 200
    assert token_response.status_code == 200
    csrf_token = token_response.json()["csrf_token"]
    response = client.post(
        "/players/history/collect-logs",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/players/history?log_collection=queued")
    assert started_jobs


def test_history_ui_renders_collect_button_notice_and_no_raw_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from armactl.web.jobs import list_recent_jobs, player_logs

    started_jobs: list[int] = []
    monkeypatch.setattr(
        player_logs,
        "start_player_log_collection_worker",
        lambda db_path, job_id: started_jobs.append(job_id),
    )
    client = _setup_owner_client(tmp_path)

    page = client.get("/players/history", follow_redirects=False)

    assert page.status_code == 200
    assert 'action="/players/history/collect-logs"' in page.text
    assert "Update events from logs" in page.text
    assert 'name="log_path"' not in page.text

    outside_path = tmp_path / "outside.log"
    csrf_token = _form_token(page.text)
    response = client.post(
        "/players/history/collect-logs",
        data={"csrf_token": csrf_token, "log_path": str(outside_path)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/players/history?log_collection=queued")
    assert len(started_jobs) == 1

    notice_page = client.get(response.headers["location"], follow_redirects=False)

    assert notice_page.status_code == 200
    assert "notice-success" in notice_page.text
    assert "Player log collection queued." in notice_page.text
    assert "Allowlisted server logs will be scanned in the background." in notice_page.text
    assert 'href="/jobs"' in notice_page.text

    active_response = client.post(
        "/players/history/collect-logs",
        data={"csrf_token": csrf_token, "source_path": str(outside_path)},
        follow_redirects=False,
    )
    assert active_response.status_code == 303
    assert active_response.headers["location"].startswith(
        "/players/history?log_collection=active"
    )
    assert len(started_jobs) == 1
    assert len(list_recent_jobs(tmp_path / "web" / "web.db")) == 1

    active_notice_page = client.get(
        active_response.headers["location"],
        follow_redirects=False,
    )
    audit_text = (tmp_path / "logs" / "web" / "audit.log").read_text(encoding="utf-8")

    assert active_notice_page.status_code == 200
    assert "notice-warning" in active_notice_page.text
    assert "Player log collection already running." in active_notice_page.text
    assert "No duplicate job was created" in active_notice_page.text
    for rendered in (notice_page.text, active_notice_page.text, audit_text):
        assert str(outside_path) not in rendered
        assert "outside.log" not in rendered


def test_duplicate_active_player_log_collection_job_is_deduped(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from armactl.web.jobs import list_active_jobs, player_logs
    from armactl.web.services import player_log_collection

    started_jobs: list[int] = []
    monkeypatch.setattr(
        player_logs,
        "start_player_log_collection_worker",
        lambda db_path, job_id: started_jobs.append(job_id),
    )
    db_path = tmp_path / "web" / "web.db"
    audit_log_path = tmp_path / "logs" / "web" / "audit.log"

    first = player_log_collection.request_player_log_collection_and_start(
        db_path,
        audit_log_path=audit_log_path,
        username="owner",
        user_id=None,
    )
    second = player_log_collection.request_player_log_collection_and_start(
        db_path,
        audit_log_path=audit_log_path,
        username="owner",
        user_id=None,
    )

    assert first.created is True
    assert second.created is False
    assert first.job.id == second.job.id
    assert started_jobs == [first.job.id]
    active_jobs = list_active_jobs(db_path)
    assert [job.id for job in active_jobs] == [first.job.id]
    assert [event["details"]["phase"] for event in _audit_events(tmp_path)] == [
        "intent",
        "intent",
    ]


def test_player_log_collection_job_reports_empty_and_no_matching_results(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from armactl.web.jobs import get_job, list_recent_jobs, player_logs

    monkeypatch.setattr(
        player_logs,
        "start_player_log_collection_worker",
        lambda db_path, job_id: None,
    )
    client = _setup_owner_client(tmp_path)
    csrf_token = _history_csrf_token(client)
    db_path = tmp_path / "web" / "web.db"

    empty_response = client.post(
        "/players/history/collect-logs",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    assert empty_response.status_code == 303
    empty_job_id = list_recent_jobs(db_path)[0].id
    player_logs.dispatch_player_log_collection_job(db_path, empty_job_id)
    empty_job = get_job(db_path, empty_job_id)

    assert empty_job is not None
    assert empty_job.status == "succeeded"
    assert empty_job.result_message == "No allowlisted player logs found."
    assert "files_requested=0" in empty_job.stdout_tail
    assert "parsed_events=0" in empty_job.stdout_tail

    raw_line = (
        "DEFAULT : unmatched connection from 198.51.100.77:2302 "
        "using /home/deus/projects/armactl/private.log"
    )
    log_path = _write_console_log(tmp_path, [raw_line], run="run-with-no-events")
    no_match_response = client.post(
        "/players/history/collect-logs",
        data={"csrf_token": csrf_token, "path": str(log_path)},
        follow_redirects=False,
    )
    assert no_match_response.status_code == 303
    no_match_job_id = list_recent_jobs(db_path)[0].id
    player_logs.dispatch_player_log_collection_job(db_path, no_match_job_id)
    no_match_job = get_job(db_path, no_match_job_id)
    jobs_page = client.get("/jobs", follow_redirects=False)
    audit_text = (tmp_path / "logs" / "web" / "audit.log").read_text(encoding="utf-8")

    assert no_match_job is not None
    assert no_match_job.status == "succeeded"
    assert no_match_job.result_message == "No matching player log events found."
    assert "files_requested=1" in no_match_job.stdout_tail
    assert "scanned_lines=1" in no_match_job.stdout_tail
    assert "parsed_events=0" in no_match_job.stdout_tail
    assert "stored_events=0" in no_match_job.stdout_tail
    assert "Collect player log events" in jobs_page.text
    assert "No matching player log events found." in jobs_page.text

    events = _audit_events(tmp_path)
    outcomes = [event for event in events if event["details"]["phase"] == "outcome"]
    assert [event["message"] for event in outcomes] == [
        "No allowlisted player logs found.",
        "No matching player log events found.",
    ]
    assert outcomes[1]["details"]["files_requested"] == "1"
    assert outcomes[1]["details"]["parsed_events"] == "0"
    for rendered in (no_match_job.stdout_tail, jobs_page.text, audit_text):
        assert raw_line not in rendered
        assert str(log_path) not in rendered
        assert "198.51.100.77" not in rendered
        assert "/home/deus/projects/armactl/private.log" not in rendered


def test_player_log_collection_job_failure_uses_safe_job_and_audit_messages(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from armactl.web.jobs import get_job, player_logs
    from armactl.web.services import player_log_collection

    allowed_log = _write_console_log(tmp_path, _fixture_lines())
    raw_failure = (
        f"failed reading {allowed_log} from 198.51.100.88:2302; "
        "BACKEND : Authenticated player: identityId=secret name=Raw Line"
    )

    def fail_collect(*args, **kwargs):
        raise RuntimeError(raw_failure)

    monkeypatch.setattr(
        player_logs.player_log_collector,
        "collect_player_log_events",
        fail_collect,
    )
    monkeypatch.setattr(
        player_logs,
        "start_player_log_collection_worker",
        lambda db_path, job_id: None,
    )
    db_path = tmp_path / "web" / "web.db"
    queued = player_log_collection.request_player_log_collection_and_start(
        db_path,
        audit_log_path=tmp_path / "logs" / "web" / "audit.log",
        username="owner",
        user_id=None,
    )

    dispatch = player_logs.dispatch_player_log_collection_job(db_path, queued.job.id)
    job = get_job(db_path, queued.job.id)
    audit_text = (tmp_path / "logs" / "web" / "audit.log").read_text(encoding="utf-8")

    assert dispatch.ran is True
    assert job is not None
    assert job.status == "failed"
    assert job.error_class == "RuntimeError"
    assert job.error_message == "Player log collection failed."
    assert "files=1" in job.stdout_tail
    assert raw_failure not in job.error_message
    assert raw_failure not in job.stdout_tail
    assert raw_failure not in audit_text
    assert str(allowed_log) not in job.stdout_tail
    assert str(allowed_log) not in audit_text
    assert "198.51.100.88" not in job.error_message
    assert "198.51.100.88" not in audit_text
    events = _audit_events(tmp_path)
    assert events[-1]["success"] is False
    assert events[-1]["details"]["phase"] == "outcome"
    assert events[-1]["details"]["reason_message"] == "Player log collection failed."


def test_player_log_collection_job_collects_allowlisted_logs_and_audits_counts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from armactl.web.jobs import get_job, player_logs
    from armactl.web.services import player_log_collection

    allowed_log = _write_console_log(tmp_path, _fixture_lines())
    outside_log = tmp_path / "outside.log"
    outside_log.write_text(
        "BACKEND : Authenticated player: "
        f"rplIdentity=44 identityId={PLAYER_CHARLIE_ID} name=Charlie Three\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        player_logs,
        "start_player_log_collection_worker",
        lambda db_path, job_id: None,
    )
    db_path = tmp_path / "web" / "web.db"
    audit_log_path = tmp_path / "logs" / "web" / "audit.log"

    queued = player_log_collection.request_player_log_collection_and_start(
        db_path,
        audit_log_path=audit_log_path,
        username="owner",
        user_id=None,
    )
    dispatch = player_logs.dispatch_player_log_collection_job(db_path, queued.job.id)

    job = get_job(db_path, queued.job.id)
    assert dispatch.ran is True
    assert job is not None
    assert job.status == "succeeded"
    assert job.result_message == "Player log collection completed."
    assert "scanned_lines=3" in job.stdout_tail
    assert "parsed_events=2" in job.stdout_tail
    assert "stored_events=2" in job.stdout_tail
    assert str(allowed_log) not in job.stdout_tail
    assert str(outside_log) not in job.stdout_tail

    rows = _event_rows(tmp_path / "default" / "players.db")
    encoded_rows = json.dumps(rows, sort_keys=True)
    assert len(rows) == 2
    assert PLAYER_ALPHA_ID in encoded_rows
    assert PLAYER_CHARLIE_ID not in encoded_rows
    assert str(allowed_log) not in encoded_rows
    assert str(outside_log) not in encoded_rows

    second = player_log_collection.request_player_log_collection_and_start(
        db_path,
        audit_log_path=audit_log_path,
        username="owner",
        user_id=None,
    )
    second_dispatch = player_logs.dispatch_player_log_collection_job(
        db_path,
        second.job.id,
    )
    second_job = get_job(db_path, second.job.id)

    assert second.created is True
    assert second_dispatch.ran is True
    assert second_job is not None
    assert second_job.status == "succeeded"
    assert second_job.result_message == "No new player log events found."
    assert "parsed_events=2" in second_job.stdout_tail
    assert "stored_events=0" in second_job.stdout_tail
    assert "duplicates=2" in second_job.stdout_tail
    assert str(allowed_log) not in second_job.stdout_tail
    assert str(outside_log) not in second_job.stdout_tail
    assert len(_event_rows(tmp_path / "default" / "players.db")) == 2

    events = _audit_events(tmp_path)
    assert [event["details"]["phase"] for event in events] == [
        "intent",
        "outcome",
        "intent",
        "outcome",
    ]
    outcome = events[1]
    duplicate_outcome = events[3]
    assert outcome["action"] == player_logs.PLAYER_LOG_COLLECTION_ACTION
    assert outcome["target"] == player_logs.PLAYER_LOG_COLLECTION_JOB_KIND
    assert outcome["success"] is True
    assert outcome["details"]["scanned_lines"] == "3"
    assert outcome["details"]["parsed_events"] == "2"
    assert outcome["details"]["stored_events"] == "2"
    assert outcome["details"]["duplicate_events"] == "0"
    assert outcome["details"]["skipped_lines"] == "0"
    assert outcome["details"]["error_count"] == "0"
    assert duplicate_outcome["message"] == "No new player log events found."
    assert duplicate_outcome["details"]["parsed_events"] == "2"
    assert duplicate_outcome["details"]["stored_events"] == "0"
    assert duplicate_outcome["details"]["duplicate_events"] == "2"

    audit_text = (tmp_path / "logs" / "web" / "audit.log").read_text(encoding="utf-8")
    assert str(allowed_log) not in audit_text
    assert str(outside_log) not in audit_text
    assert "outside.log" not in audit_text
    assert "Charlie Three" not in audit_text


def test_player_log_collection_job_classifies_controlled_skipped_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from armactl.web.jobs import get_job, player_logs
    from armactl.web.services import player_log_collection

    allowed_log = _write_console_log(tmp_path, _fixture_lines(), run="allowed")
    oversized_log = _write_console_log(tmp_path, ["oversized"], run="oversized")
    oversized_log.write_bytes(b"x" * (player_logs.DEFAULT_MAX_FILE_BYTES + 1))
    monkeypatch.setattr(
        player_logs,
        "start_player_log_collection_worker",
        lambda db_path, job_id: None,
    )
    db_path = tmp_path / "web" / "web.db"
    audit_log_path = tmp_path / "logs" / "web" / "audit.log"

    queued = player_log_collection.request_player_log_collection_and_start(
        db_path,
        audit_log_path=audit_log_path,
        username="owner",
        user_id=None,
    )
    dispatch = player_logs.dispatch_player_log_collection_job(db_path, queued.job.id)

    job = get_job(db_path, queued.job.id)
    assert dispatch.ran is True
    assert job is not None
    assert job.status == "succeeded"
    assert job.result_message == "Player log collection completed with skipped files."
    assert "files_requested=2" in job.stdout_tail
    assert "files_scanned=1" in job.stdout_tail
    assert "files_skipped=1" in job.stdout_tail
    assert "errors=1" in job.stdout_tail
    assert "skipped_file_reasons=file_too_large=1" in job.stdout_tail

    events = _audit_events(tmp_path)
    outcome = events[-1]

    assert outcome["action"] == player_logs.PLAYER_LOG_COLLECTION_ACTION
    assert outcome["target"] == player_logs.PLAYER_LOG_COLLECTION_JOB_KIND
    assert outcome["success"] is True
    assert outcome["message"] == "Player log collection completed with skipped files."
    assert outcome["details"]["files_requested"] == "2"
    assert outcome["details"]["files_scanned"] == "1"
    assert outcome["details"]["files_skipped"] == "1"
    assert outcome["details"]["error_count"] == "1"
    assert outcome["details"]["skipped_file_reasons"] == "file_too_large=1"

    audit_text = audit_log_path.read_text(encoding="utf-8")
    for rendered in (job.stdout_tail, audit_text):
        assert str(allowed_log) not in rendered
        assert str(oversized_log) not in rendered


def test_player_log_collection_job_audits_uncontrolled_skip_as_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from armactl.web.jobs import get_job, player_logs
    from armactl.web.services import player_log_collection

    scanned_log = _write_console_log(tmp_path, [""], run="scanned")
    skipped_log = _write_console_log(tmp_path, [""], run="skipped")
    stat_failed_error = player_logs.player_log_collector.PlayerLogCollectionError(
        source="console.log",
        code="stat_failed",
        message="file metadata could not be read",
    )
    summary = player_logs.player_log_collector.PlayerLogCollectionSummary(
        dry_run=False,
        files_requested=2,
        files_scanned=1,
        files_skipped=1,
        lines_scanned=3,
        matched_events=0,
        stored_events=0,
        duplicate_events=0,
        unmatched_lines=3,
        skipped_lines=0,
        errors=(stat_failed_error,),
        files=(
            player_logs.player_log_collector.PlayerLogCollectionFileSummary(
                source="console.log",
                status="scanned",
                bytes_scanned=128,
                lines_scanned=3,
                unmatched_lines=3,
            ),
            player_logs.player_log_collector.PlayerLogCollectionFileSummary(
                source="console.log",
                status="error",
                errors=(stat_failed_error,),
            ),
        ),
    )

    monkeypatch.setattr(
        player_logs.player_log_collector,
        "collect_player_log_events",
        lambda *args, **kwargs: summary,
    )
    monkeypatch.setattr(
        player_logs,
        "start_player_log_collection_worker",
        lambda db_path, job_id: None,
    )
    db_path = tmp_path / "web" / "web.db"
    audit_log_path = tmp_path / "logs" / "web" / "audit.log"

    queued = player_log_collection.request_player_log_collection_and_start(
        db_path,
        audit_log_path=audit_log_path,
        username="owner",
        user_id=None,
    )
    dispatch = player_logs.dispatch_player_log_collection_job(db_path, queued.job.id)

    job = get_job(db_path, queued.job.id)
    assert dispatch.ran is True
    assert job is not None
    assert job.status == "succeeded"
    assert job.result_message == "Player log collection completed with skipped files."
    assert "skipped_file_reasons=stat_failed=1" in job.stdout_tail

    events = _audit_events(tmp_path)
    outcome = events[-1]

    assert outcome["action"] == player_logs.PLAYER_LOG_COLLECTION_ACTION
    assert outcome["target"] == player_logs.PLAYER_LOG_COLLECTION_JOB_KIND
    assert outcome["success"] is False
    assert outcome["message"] == "Player log collection completed with skipped files."
    assert outcome["details"]["skipped_file_reasons"] == "stat_failed=1"

    audit_text = audit_log_path.read_text(encoding="utf-8")
    for rendered in (job.stdout_tail, audit_text):
        assert str(tmp_path) not in rendered
        assert str(scanned_log) not in rendered
        assert str(skipped_log) not in rendered


def test_allowlist_resolver_uses_instance_config_console_logs_only(tmp_path: Path) -> None:
    from armactl.web.jobs import player_logs

    allowed = _write_console_log(tmp_path, _fixture_lines(), run="allowed")
    ignored_name = tmp_path / "default" / "config" / "logs" / "allowed" / "server.log"
    ignored_name.write_text("\n", encoding="utf-8")
    nested = tmp_path / "default" / "config" / "logs" / "allowed" / "nested" / "console.log"
    nested.parent.mkdir(parents=True)
    nested.write_text("\n", encoding="utf-8")
    outside = tmp_path / "elsewhere" / "console.log"
    outside.parent.mkdir()
    outside.write_text("\n", encoding="utf-8")

    resolved = player_logs.resolve_allowlisted_player_log_paths(data_root=tmp_path)

    assert resolved == (allowed,)
