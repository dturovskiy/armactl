"""Tests for manual player-session operator job controls."""

from __future__ import annotations

from pathlib import Path

import pytest
from web_route_helpers import _client, _form_token, _login

from armactl.web.auth.permissions import PLAYERS_VIEW
from armactl.web.auth.setup import setup_owner_user

SESSION_JOB_ROUTES = (
    (
        "/players/sessions/scan-live",
        "live-scan",
        "players:scan-live-sessions",
        "start_player_live_session_scan_worker",
        "Live player session scan queued.",
        "Live player session scan already running.",
    ),
    (
        "/players/sessions/sessionize-log-events",
        "log-sessionization",
        "players:sessionize-log-events",
        "start_player_log_sessionization_worker",
        "Player log sessionization queued.",
        "Player log sessionization already running.",
    ),
    (
        "/players/sessions/maintenance",
        "maintenance",
        "players:session-maintenance",
        "start_player_session_maintenance_worker",
        "Player session maintenance queued.",
        "Player session maintenance already running.",
    ),
)

SESSION_WORKER_ATTRS = tuple(route[3] for route in SESSION_JOB_ROUTES)


def _setup_owner_client(tmp_path: Path):
    from armactl.web.app import create_app

    password = "owner player session controls password"
    setup_owner_user(tmp_path, "owner", password)
    client = _client(create_app(data_root=tmp_path))
    response = _login(client, "owner", password)
    assert response.status_code == 303
    return client


def _sessions_csrf_token(client) -> str:
    response = client.get("/players/sessions", follow_redirects=False)
    assert response.status_code == 200
    return _form_token(response.text)


def _patch_session_workers(monkeypatch, target_attr: str | None = None) -> list[int]:
    from armactl.web.jobs import player_sessions

    started_jobs: list[int] = []

    for worker_attr in SESSION_WORKER_ATTRS:
        if worker_attr == target_attr:
            monkeypatch.setattr(
                player_sessions,
                worker_attr,
                lambda db_path, job_id: started_jobs.append(job_id),
            )
        else:
            monkeypatch.setattr(
                player_sessions,
                worker_attr,
                lambda db_path, job_id: (_ for _ in ()).throw(
                    AssertionError("unexpected session worker started")
                ),
            )
    return started_jobs


@pytest.mark.parametrize(
    "route, _query_key, _kind, _worker_attr, _queued, _active",
    SESSION_JOB_ROUTES,
)
def test_session_job_routes_require_auth_permission_and_csrf(
    tmp_path: Path,
    monkeypatch,
    set_web_owner_permissions,
    route: str,
    _query_key: str,
    _kind: str,
    _worker_attr: str,
    _queued: str,
    _active: str,
) -> None:
    from armactl.web.app import create_app

    _patch_session_workers(monkeypatch)
    anonymous = _client(create_app(data_root=tmp_path))
    response = anonymous.post(
        route,
        data={"csrf_token": "invalid"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"

    client = _setup_owner_client(tmp_path)
    csrf_token = _sessions_csrf_token(client)

    set_web_owner_permissions(set())
    response = client.post(
        route,
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.text == "Permission denied."

    set_web_owner_permissions({PLAYERS_VIEW})
    response = client.post(
        route,
        data={"csrf_token": "invalid"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.text == "Invalid CSRF token."


@pytest.mark.parametrize(
    "route, query_key, kind, worker_attr, queued_title, active_title",
    SESSION_JOB_ROUTES,
)
def test_session_job_post_queues_redirect_notice_and_dedupes(
    tmp_path: Path,
    monkeypatch,
    route: str,
    query_key: str,
    kind: str,
    worker_attr: str,
    queued_title: str,
    active_title: str,
) -> None:
    from armactl.web.jobs import list_recent_jobs

    started_jobs = _patch_session_workers(monkeypatch, worker_attr)
    client = _setup_owner_client(tmp_path)
    page = client.get("/players/sessions", follow_redirects=False)
    csrf_token = _form_token(page.text)
    raw_path = tmp_path / "private-console.log"

    response = client.post(
        route,
        data={
            "csrf_token": csrf_token,
            "raw_path": str(raw_path),
            "raw_line": "198.51.100.44 token=raw-secret",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith(
        f"/players/sessions?session_job={query_key}&job_status=queued"
    )
    jobs = [
        job
        for job in list_recent_jobs(tmp_path / "web" / "web.db")
        if job.kind == kind
    ]
    assert len(jobs) == 1
    assert started_jobs == [jobs[0].id]

    notice_page = client.get(response.headers["location"], follow_redirects=False)

    assert notice_page.status_code == 200
    assert "notice-success" in notice_page.text
    assert queued_title in notice_page.text
    assert 'href="/jobs"' in notice_page.text
    assert str(raw_path) not in notice_page.text
    assert "raw-secret" not in notice_page.text
    assert "198.51.100.44" not in notice_page.text

    active_response = client.post(
        route,
        data={"csrf_token": csrf_token, "source_ref": str(raw_path)},
        follow_redirects=False,
    )

    assert active_response.status_code == 303
    assert active_response.headers["location"].startswith(
        f"/players/sessions?session_job={query_key}&job_status=active"
    )
    jobs_after_duplicate = [
        job
        for job in list_recent_jobs(tmp_path / "web" / "web.db")
        if job.kind == kind
    ]
    assert len(jobs_after_duplicate) == 1
    assert started_jobs == [jobs[0].id]

    active_notice_page = client.get(
        active_response.headers["location"],
        follow_redirects=False,
    )

    assert active_notice_page.status_code == 200
    assert "notice-warning" in active_notice_page.text
    assert active_title in active_notice_page.text
    assert "No duplicate job was created" in active_notice_page.text
    assert 'href="/jobs"' in active_notice_page.text
    assert str(raw_path) not in active_notice_page.text


@pytest.mark.parametrize(
    "route, query_key, _kind, _worker_attr, _queued_title, failure_title",
    (
        (
            "/players/sessions/scan-live",
            "live-scan",
            "players:scan-live-sessions",
            "start_player_live_session_scan_worker",
            "Live player session scan queued.",
            "Live player session scan was not queued.",
        ),
        (
            "/players/sessions/sessionize-log-events",
            "log-sessionization",
            "players:sessionize-log-events",
            "start_player_log_sessionization_worker",
            "Player log sessionization queued.",
            "Player log sessionization was not queued.",
        ),
        (
            "/players/sessions/maintenance",
            "maintenance",
            "players:session-maintenance",
            "start_player_session_maintenance_worker",
            "Player session maintenance queued.",
            "Player session maintenance was not queued.",
        ),
    ),
)
def test_session_job_post_audit_failure_redirects_with_notice(
    tmp_path: Path,
    monkeypatch,
    route: str,
    query_key: str,
    _kind: str,
    _worker_attr: str,
    _queued_title: str,
    failure_title: str,
) -> None:
    from armactl.web.jobs import list_recent_jobs
    from armactl.web.services import (
        player_live_session_scan,
        player_log_sessionization,
        player_session_maintenance,
    )

    failure_targets = {
        "/players/sessions/scan-live": (
            player_live_session_scan,
            "request_player_live_session_scan_and_start",
            player_live_session_scan.PlayerLiveSessionScanActionAuditError,
        ),
        "/players/sessions/sessionize-log-events": (
            player_log_sessionization,
            "request_player_log_sessionization_and_start",
            player_log_sessionization.PlayerLogSessionizationActionAuditError,
        ),
        "/players/sessions/maintenance": (
            player_session_maintenance,
            "request_player_session_maintenance_and_start",
            player_session_maintenance.PlayerSessionMaintenanceActionAuditError,
        ),
    }
    module, attr, error_type = failure_targets[route]

    def fail_enqueue(*args, **kwargs):
        raise error_type("audit failed")

    _patch_session_workers(monkeypatch)
    monkeypatch.setattr(module, attr, fail_enqueue)
    client = _setup_owner_client(tmp_path)
    csrf_token = _sessions_csrf_token(client)

    response = client.post(
        route,
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        f"/players/sessions?session_job={query_key}&job_status=failed"
    )
    assert list_recent_jobs(tmp_path / "web" / "web.db") == []

    notice_page = client.get(response.headers["location"], follow_redirects=False)

    assert notice_page.status_code == 200
    assert "notice-error" in notice_page.text
    assert failure_title in notice_page.text
    assert "Audit logging failed before the job could be queued." in notice_page.text
    assert 'href="/jobs"' in notice_page.text


def test_session_get_pages_render_controls_without_enqueueing_jobs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from armactl.web.jobs import list_recent_jobs

    _patch_session_workers(monkeypatch)
    client = _setup_owner_client(tmp_path)

    sessions = client.get("/players/sessions", follow_redirects=False)
    history = client.get("/players/history", follow_redirects=False)
    current = client.get("/players", follow_redirects=False)

    assert sessions.status_code == 200
    assert history.status_code == 200
    assert current.status_code == 200
    assert 'action="/players/sessions/scan-live"' in sessions.text
    assert 'action="/players/sessions/sessionize-log-events"' in sessions.text
    assert 'action="/players/sessions/maintenance"' in sessions.text
    assert 'name="log_path"' not in sessions.text
    assert 'name="source_ref"' not in sessions.text
    assert list_recent_jobs(tmp_path / "web" / "web.db") == []


def test_session_page_renders_active_job_indicators_with_safe_fields(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from armactl.web.jobs import (
        append_job_output,
        create_job,
        mark_job_running,
        mark_job_succeeded,
    )

    _patch_session_workers(monkeypatch)
    client = _setup_owner_client(tmp_path)
    db_path = tmp_path / "web" / "web.db"
    running_job = create_job(
        db_path,
        kind="players:scan-live-sessions",
        requested_by_username="owner",
        current_step="scan raw-secret /home/deus/private.log",
    )
    mark_job_running(
        db_path,
        running_job.id,
        current_step="running raw-secret 198.51.100.44",
    )
    append_job_output(
        db_path,
        running_job.id,
        stdout="stdout raw-secret /home/deus/private.log",
        stderr="stderr token=raw-token",
    )
    queued_job = create_job(
        db_path,
        kind="players:sessionize-log-events",
        requested_by_username="owner",
    )
    finished_job = create_job(
        db_path,
        kind="players:session-maintenance",
        requested_by_username="owner",
    )
    mark_job_running(db_path, finished_job.id)
    mark_job_succeeded(db_path, finished_job.id, result_message="done raw-secret")
    create_job(
        db_path,
        kind="players:refresh-current",
        requested_by_username="owner",
    )

    response = client.get("/players/sessions", follow_redirects=False)

    assert response.status_code == 200
    html = response.text
    assert "Active session jobs" in html
    assert 'href="/jobs#background-jobs"' in html
    assert f'data-active-session-job-id="{running_job.id}"' in html
    assert f'data-active-session-job-id="{queued_job.id}"' in html
    assert f">#{running_job.id}</a>" in html
    assert f">#{queued_job.id}</a>" in html
    assert "players:scan-live-sessions" in html
    assert "players:sessionize-log-events" in html
    assert "players:session-maintenance" not in html
    assert "players:refresh-current" not in html
    assert "running" in html
    assert "queued" in html
    assert "raw-secret" not in html
    assert "raw-token" not in html
    assert "198.51.100.44" not in html
    assert "/home/deus" not in html
    assert "Last stdout lines" not in html
    assert "Current step" not in html


def test_jobs_page_labels_player_session_jobs_human_readable(tmp_path: Path) -> None:
    from armactl.web.app import create_app
    from armactl.web.jobs import create_job

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    db_path = tmp_path / "web" / "web.db"
    create_job(
        db_path,
        kind="players:scan-live-sessions",
        requested_by_username="owner",
    )
    create_job(
        db_path,
        kind="players:sessionize-log-events",
        requested_by_username="owner",
    )
    create_job(
        db_path,
        kind="players:session-maintenance",
        requested_by_username="owner",
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/jobs", follow_redirects=False)

    assert response.status_code == 200
    assert "Scan live player sessions" in response.text
    assert "Sessionize player log events" in response.text
    assert "Player session maintenance" in response.text
