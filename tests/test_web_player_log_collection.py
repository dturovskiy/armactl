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


def test_history_ui_renders_collect_button_notice_and_no_raw_paths(
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
    audit_text = (tmp_path / "logs" / "web" / "audit.log").read_text(encoding="utf-8")

    assert notice_page.status_code == 200
    assert "Player log collection queued." in notice_page.text
    assert "Allowlisted server logs will be scanned in the background." in notice_page.text
    assert 'href="/jobs"' in notice_page.text
    assert str(outside_path) not in notice_page.text
    assert str(outside_path) not in audit_text
    assert "outside.log" not in audit_text


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

    events = _audit_events(tmp_path)
    assert [event["details"]["phase"] for event in events] == ["intent", "outcome"]
    outcome = events[1]
    assert outcome["action"] == player_logs.PLAYER_LOG_COLLECTION_ACTION
    assert outcome["target"] == player_logs.PLAYER_LOG_COLLECTION_JOB_KIND
    assert outcome["success"] is True
    assert outcome["details"]["scanned_lines"] == "3"
    assert outcome["details"]["parsed_events"] == "2"
    assert outcome["details"]["stored_events"] == "2"
    assert outcome["details"]["duplicate_events"] == "0"
    assert outcome["details"]["skipped_lines"] == "0"
    assert outcome["details"]["error_count"] == "0"

    audit_text = (tmp_path / "logs" / "web" / "audit.log").read_text(encoding="utf-8")
    assert str(allowed_log) not in audit_text
    assert str(outside_log) not in audit_text
    assert "outside.log" not in audit_text
    assert "Charlie Three" not in audit_text


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
