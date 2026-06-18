"""Route and template tests for the web jobs pages."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from web_route_helpers import _client, _form_token, _login, _set_cookie

from armactl.web.auth.cookies import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME
from armactl.web.auth.setup import setup_owner_user
from armactl.web.i18n import LANGUAGE_COOKIE_NAME


def _jobs_csrf_token(client) -> str:
    jobs_response = client.get("/jobs")
    assert jobs_response.status_code == 200
    return _form_token(jobs_response.text)


def _audit_log_text(data_root: Path) -> str:
    audit_path = data_root / "logs" / "web" / "audit.log"
    return audit_path.read_text(encoding="utf-8")


def _audit_events(data_root: Path) -> list[dict]:
    events = [json.loads(line) for line in _audit_log_text(data_root).splitlines()]
    return [event for event in events if (event.get('details') or {}).get('phase') != 'intent']


def _insert_raw_web_job(
    db_path: Path,
    *,
    kind: str,
    status: str,
    created_at: str,
) -> int:
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO web_jobs (
                kind,
                status,
                requested_by_username,
                instance,
                current_step,
                created_at,
                updated_at
            )
            VALUES (?, ?, 'owner', 'default', 'legacy active row', ?, ?)
            """,
            (kind, status, created_at, created_at),
        )
        job_id = cursor.lastrowid
    assert job_id is not None
    return int(job_id)


def test_unauthenticated_jobs_redirects_to_login(tmp_path: Path):
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    response = client.get("/jobs", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_authenticated_owner_sees_jobs_page(tmp_path: Path):
    from armactl.web.app import create_app
    from armactl.web.jobs import create_job, mark_job_running, mark_job_succeeded

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    db_path = tmp_path / "web" / "web.db"
    job = create_job(db_path, kind="safe:test", requested_by_username="owner")
    mark_job_running(db_path, job.id, current_step="Working", progress_current=1, progress_total=2)
    mark_job_succeeded(db_path, job.id, result_message="Job completed.", current_step="Done")
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/jobs", follow_redirects=False)

    assert response.status_code == 200
    assert "Operations" in response.text
    assert "Background jobs" in response.text
    assert "No pending operator work." in response.text
    assert "safe:test" in response.text
    assert "succeeded" in response.text
    assert "Job completed." in response.text
    assert "owner" in response.text
    assert 'action="/logout"' in response.text


def test_jobs_permission_denied_returns_controlled_403(
    tmp_path: Path,
    monkeypatch,
    set_web_owner_permissions,
):
    from armactl.web.app import create_app
    from armactl.web.services import job_integrity

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    set_web_owner_permissions(set())
    monkeypatch.setattr(
        job_integrity,
        "job_store_integrity_diagnostics",
        lambda db_path: (_ for _ in ()).throw(
            AssertionError("jobs backend should not be called")
        ),
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/jobs", follow_redirects=False)

    assert response.status_code == 403
    assert response.text == "Permission denied."
    assert "Traceback" not in response.text


def test_jobs_page_renders_ukrainian_labels(tmp_path: Path):
    from armactl.web.app import create_app
    from armactl.web.jobs import create_job

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    create_job(tmp_path / "web" / "web.db", kind="safe:test", requested_by_username="owner")
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    _set_cookie(client, LANGUAGE_COOKIE_NAME, "uk")

    response = client.get("/jobs", follow_redirects=False)

    assert response.status_code == 200
    assert '<html lang="uk"' in response.text
    assert "Операції" in response.text
    assert "Фонові завдання" in response.text
    assert "Запитав" in response.text
    assert "у черзі" in response.text


def test_jobs_output_is_escaped_and_secrets_are_not_rendered(tmp_path: Path):
    from armactl.web.app import create_app
    from armactl.web.jobs import append_job_output, create_job, mark_job_running

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    db_path = tmp_path / "web" / "web.db"
    job = create_job(db_path, kind="safe:test", requested_by_username="owner")
    mark_job_running(db_path, job.id)
    append_job_output(
        db_path,
        job.id,
        stdout="<script>alert(1)</script> password=hunter2",
        stderr="ARMACTL_WEB_SESSION_SECRET=secret-value",
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/jobs", follow_redirects=False)

    assert response.status_code == 200
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    assert "<script>alert(1)</script>" not in response.text
    assert "hunter2" not in response.text
    assert "secret-value" not in response.text
    assert "password=***" in response.text
    assert "ARMACTL_WEB_SESSION_SECRET=***" in response.text
    assert SESSION_COOKIE_NAME not in response.text
    assert CSRF_COOKIE_NAME not in response.text


def test_jobs_page_distinguishes_empty_pending_work_and_background_jobs(tmp_path: Path):
    from armactl.web.app import create_app

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/jobs", follow_redirects=False)

    assert response.status_code == 200
    assert "Pending operator work" in response.text
    assert "Background jobs" in response.text
    assert "No pending operator work." in response.text
    assert "No background jobs." in response.text
    assert "No jobs yet." not in response.text


def test_jobs_page_shows_active_duplicate_job_store_integrity_warning(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.services import job_integrity

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    monkeypatch.setattr(
        job_integrity,
        "job_store_integrity_diagnostics",
        lambda db_path: (
            job_integrity.JobStoreIntegrityDiagnostic(
                report_type="active_duplicate",
                severity="warning",
                job_kind="server:install",
                instance="default",
                active_job_ids=(11, 12),
                kept_job_id=11,
                duplicate_job_ids=(12,),
            ),
        ),
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/jobs", follow_redirects=False)

    assert response.status_code == 200
    assert "Job-store integrity" in response.text
    assert "Duplicate active jobs detected." in response.text
    assert "Current duplicate active jobs are still present." in response.text
    assert "Maintenance is required to cancel duplicate active rows." in response.text
    assert "notice-panel notice-warning" in response.text
    assert "Job kind" in response.text
    assert "Active job IDs" in response.text
    assert "#11" in response.text
    assert "#12" in response.text
    assert "Job-store maintenance completed." not in response.text
    assert "Active jobs #" not in response.text
    assert "Review cancelled jobs below." not in response.text
    assert "Web job-store maintenance cancelled duplicate active jobs." not in response.text
    assert "Pending operator work" in response.text
    assert "No pending operator work." in response.text


def test_jobs_page_shows_repaired_job_store_report_as_neutral_notice(tmp_path: Path):
    from armactl.web.app import create_app
    from armactl.web.jobs import SERVER_INSTALL_JOB_KIND

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    db_path = tmp_path / "web" / "web.db"
    kept = _insert_raw_web_job(
        db_path,
        kind=SERVER_INSTALL_JOB_KIND,
        status="queued",
        created_at="2026-01-01T00:00:00+00:00",
    )
    _insert_raw_web_job(
        db_path,
        kind=SERVER_INSTALL_JOB_KIND,
        status="running",
        created_at="2026-01-01T00:00:01+00:00",
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/jobs", follow_redirects=False)

    assert response.status_code == 200
    assert "Job-store integrity" in response.text
    assert "Job-store maintenance completed." in response.text
    assert "Duplicate active jobs were repaired. Oldest active jobs were kept." in response.text
    assert "Cancelled duplicate jobs remain visible below for audit context." in response.text
    assert "Repaired duplicate active jobs" in response.text
    assert "Last repair" in response.text
    assert "notice-panel diagnostic-notice" in response.text
    assert "notice-panel notice-warning" not in response.text
    assert "Duplicate active jobs detected." not in response.text
    assert "Web job-store maintenance cancelled duplicate active jobs." not in response.text
    assert "Review cancelled jobs below." not in response.text
    assert "Cancelled by web job-store maintenance; older active job kept." in response.text
    assert "Duplicate active job cancelled" in response.text
    with sqlite3.connect(db_path) as connection:
        active_rows = connection.execute(
            """
            SELECT id
            FROM web_jobs
            WHERE kind = ?
              AND status IN ('queued', 'running')
            ORDER BY id
            """,
            (SERVER_INSTALL_JOB_KIND,),
        ).fetchall()
    assert [row[0] for row in active_rows] == [kept]


def test_jobs_page_localizes_job_store_integrity_diagnostics_to_ukrainian(
    tmp_path: Path,
):
    from armactl.web.app import create_app
    from armactl.web.jobs import SERVER_INSTALL_JOB_KIND

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    db_path = tmp_path / "web" / "web.db"
    _insert_raw_web_job(
        db_path,
        kind=SERVER_INSTALL_JOB_KIND,
        status="queued",
        created_at="2026-01-01T00:00:00+00:00",
    )
    _insert_raw_web_job(
        db_path,
        kind=SERVER_INSTALL_JOB_KIND,
        status="running",
        created_at="2026-01-01T00:00:01+00:00",
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    _set_cookie(client, LANGUAGE_COOKIE_NAME, "uk")

    response = client.get("/jobs", follow_redirects=False)

    assert response.status_code == 200
    assert "Цілісність сховища завдань" in response.text
    assert "Обслуговування сховища завдань завершено." in response.text
    assert "Дублікати активних завдань виправлено." in response.text
    assert "Виправлені дублікати активних завдань" in response.text
    assert "Job-store maintenance completed." not in response.text
    assert "Duplicate active jobs were repaired." not in response.text
    assert "Repaired duplicate active jobs" not in response.text
    assert "Review cancelled jobs below." not in response.text


def test_jobs_page_shows_pending_work_when_background_jobs_empty_and_redacts_details(
    tmp_path: Path,
):
    from armactl.web.app import create_app
    from armactl.web.services.pending_work import KIND_CONFIG, mark_restart_pending

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    mark_restart_pending(
        tmp_path / "web" / "web.db",
        kind=KIND_CONFIG,
        source_action="config.save",
        username="owner",
        details=(
            "max_players password=hunter2 token=raw-token "
            "ARMACTL_WEB_SESSION_SECRET=session-secret"
        ),
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/jobs", follow_redirects=False)

    assert response.status_code == 200
    assert "Pending operator work" in response.text
    assert "These are not background jobs" in response.text
    assert "Background jobs" in response.text
    assert "Config changes" in response.text
    assert 'href="/config"' in response.text
    assert "config.save" in response.text
    assert "max_players" in response.text
    assert "No background jobs." in response.text
    assert "No jobs yet." not in response.text
    assert "pending-work-table" in response.text
    assert "pending-work-card" not in response.text
    assert "hunter2" not in response.text
    assert "raw-token" not in response.text
    assert "session-secret" not in response.text
    assert "password=***" in response.text
    assert "token=***" in response.text
    assert "ARMACTL_WEB_SESSION_SECRET=***" in response.text
    assert SESSION_COOKIE_NAME not in response.text
    assert CSRF_COOKIE_NAME not in response.text


def test_unauthenticated_install_and_repair_jobs_redirect_to_login(tmp_path: Path):
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    install_response = client.post("/jobs/server/install", data={}, follow_redirects=False)
    repair_response = client.post("/jobs/server/repair", data={}, follow_redirects=False)

    assert install_response.status_code == 303
    assert install_response.headers["location"] == "/login"
    assert repair_response.status_code == 303
    assert repair_response.headers["location"] == "/login"


def test_install_repair_job_permission_denied_returns_403(
    tmp_path: Path,
    monkeypatch,
    set_web_owner_permissions,
):
    from armactl.web.app import create_app
    from armactl.web.services import server_job_actions

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    set_web_owner_permissions(set())
    monkeypatch.setattr(
        server_job_actions,
        "enqueue_server_job_and_start",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("server job backend should not be called")
        ),
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    install_response = client.post("/jobs/server/install", data={}, follow_redirects=False)
    repair_response = client.post("/jobs/server/repair", data={}, follow_redirects=False)

    assert install_response.status_code == 403
    assert install_response.text == "Permission denied."
    assert repair_response.status_code == 403
    assert repair_response.text == "Permission denied."


def test_install_repair_jobs_require_csrf(tmp_path: Path):
    from armactl.web.app import create_app

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    install_response = client.post(
        "/jobs/server/install",
        data={"csrf_token": "bad-token"},
        follow_redirects=False,
    )
    repair_response = client.post(
        "/jobs/server/repair",
        data={"csrf_token": "bad-token"},
        follow_redirects=False,
    )

    assert install_response.status_code == 403
    assert install_response.text == "Invalid CSRF token."
    assert repair_response.status_code == 403
    assert repair_response.text == "Invalid CSRF token."


def test_post_install_and_repair_create_queued_jobs_without_running_backend(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.jobs import server as server_jobs
    from armactl.web.jobs.store import list_recent_jobs

    def fail_backend(*args, **kwargs):
        raise AssertionError("HTTP request must not run install/repair backend")

    monkeypatch.setattr(server_jobs.installer, "run_install", fail_backend)
    monkeypatch.setattr(server_jobs.repair, "run_repair", fail_backend)
    scheduled: list[int] = []
    monkeypatch.setattr(
        server_jobs,
        "start_server_job_worker",
        lambda db_path, job_id: scheduled.append(job_id),
    )

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    csrf_token = _jobs_csrf_token(client)

    install_response = client.post(
        "/jobs/server/install",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    repair_response = client.post(
        "/jobs/server/repair",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    jobs = list_recent_jobs(tmp_path / "web" / "web.db")
    audit_events = _audit_events(tmp_path)

    assert install_response.status_code == 303
    assert install_response.headers["location"] == "/jobs"
    assert repair_response.status_code == 303
    assert repair_response.headers["location"] == "/jobs"
    assert [job.kind for job in jobs[:2]] == ["server:repair", "server:install"]
    assert all(job.status == "queued" for job in jobs[:2])
    assert [event["action"] for event in audit_events[-2:]] == [
        "job.server-install.enqueue",
        "job.server-repair.enqueue",
    ]
    assert audit_events[-2]["details"]["job_kind"] == "server:install"
    assert audit_events[-2]["details"]["created"] == "true"
    assert audit_events[-1]["details"]["job_kind"] == "server:repair"
    assert audit_events[-1]["details"]["created"] == "true"
    assert scheduled == [jobs[1].id, jobs[0].id]


def test_post_install_reuses_existing_active_job_without_creating_duplicate(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.jobs import server as server_jobs
    from armactl.web.jobs.store import list_recent_jobs

    scheduled: list[int] = []
    monkeypatch.setattr(
        server_jobs,
        "start_server_job_worker",
        lambda db_path, job_id: scheduled.append(job_id),
    )

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    csrf_token = _jobs_csrf_token(client)

    first_response = client.post(
        "/jobs/server/install",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    second_response = client.post(
        "/jobs/server/install",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    jobs = [
        job
        for job in list_recent_jobs(tmp_path / "web" / "web.db")
        if job.kind == "server:install"
    ]
    audit_events = _audit_events(tmp_path)

    assert first_response.status_code == 303
    assert first_response.headers["location"] == "/jobs"
    assert second_response.status_code == 303
    assert second_response.headers["location"] == "/jobs"
    assert len(jobs) == 1
    assert jobs[0].status == "queued"
    assert scheduled == [jobs[0].id, jobs[0].id]
    assert [event["details"]["created"] for event in audit_events[-2:]] == ["true", "false"]


def test_install_job_audit_failure_cancels_created_job(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.jobs.store import list_recent_jobs
    from armactl.web.services import server_job_actions
    from armactl.web.services.audit import AuditLogError

    def fail_audit(*args, **kwargs):
        raise AuditLogError("disk full")

    def fail_worker(*args, **kwargs):
        raise AssertionError("worker should not start when audit fails")

    monkeypatch.setattr(server_job_actions, "append_audit_event", fail_audit)
    monkeypatch.setattr(server_job_actions.server_jobs, "start_server_job_worker", fail_worker)

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    csrf_token = _jobs_csrf_token(client)

    response = client.post(
        "/jobs/server/install",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    jobs = list_recent_jobs(tmp_path / "web" / "web.db")

    assert response.status_code == 500
    assert response.text == "Job queued but audit logging failed."
    assert len(jobs) == 1
    assert jobs[0].kind == "server:install"
    assert jobs[0].status == "cancelled"
    assert jobs[0].result_message == "Cancelled because audit logging failed."


def test_jobs_page_shows_queued_install_repair_jobs(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app
    from armactl.web.jobs import server as server_jobs

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    monkeypatch.setattr(server_jobs, "start_server_job_worker", lambda db_path, job_id: None)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    csrf_token = _jobs_csrf_token(client)
    client.post(
        "/jobs/server/install",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    response = client.get("/jobs", follow_redirects=False)

    assert response.status_code == 200
    assert "server:install" in response.text
    assert "queued" in response.text
    assert "Queued install" in response.text
