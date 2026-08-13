"""Route and template tests for the web jobs pages."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
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


def _server_version_state(check_state: str, *, running: bool = False):
    from armactl.web.services import server_versions

    if check_state == server_versions.SERVER_VERSION_CHECK_UPTODATE:
        return server_versions.ServerVersionState(
            installed="100",
            latest="100",
            branch="public",
            check_state=check_state,
            status="up to date",
            message="Server is already up to date",
            up_to_date=True,
            server_running=running,
        )
    if check_state == server_versions.SERVER_VERSION_CHECK_AVAILABLE:
        return server_versions.ServerVersionState(
            installed="100",
            latest="101",
            branch="public",
            check_state=check_state,
            status="update available",
            message="Update available",
            can_update=True,
            server_running=running,
        )
    if check_state == server_versions.SERVER_VERSION_CHECK_FAILED:
        return server_versions.ServerVersionState(
            check_state=check_state,
            status="check failed",
            message="Build check failed",
            failure_reason="probe failed",
            server_running=running,
        )
    return server_versions.ServerVersionState(
        installed="100",
        branch="public",
        check_state=server_versions.SERVER_VERSION_CHECK_UNKNOWN,
        status="unknown",
        message="Latest build unknown",
        server_running=running,
    )


def test_unauthenticated_jobs_redirects_to_login(tmp_path: Path):
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    response = client.get("/jobs", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_authenticated_owner_sees_jobs_page(tmp_path: Path):
    from armactl.web.app import create_app
    from armactl.web.jobs import append_job_output, create_job, mark_job_running, mark_job_succeeded

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    db_path = tmp_path / "web" / "web.db"
    job = create_job(db_path, kind="safe:test", requested_by_username="owner")
    create_job(
        db_path,
        kind="players:scan-live-sessions",
        requested_by_username="owner",
    )
    mark_job_running(db_path, job.id, current_step="Working", progress_current=1, progress_total=2)
    append_job_output(db_path, job.id, stdout="step output")
    mark_job_succeeded(db_path, job.id, result_message="Job completed.", current_step="Done")
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/jobs", follow_redirects=False)

    assert response.status_code == 200
    assert "Operations" in response.text
    assert "Background jobs" in response.text
    assert "/static/js/jobs.js" in response.text
    assert "/static/js/time.js" in response.text
    assert "data-jobs-refresh-root" in response.text
    assert "No pending operator work." in response.text
    assert "safe:test" in response.text
    assert "Other jobs" in response.text
    assert "Player data" in response.text
    assert "Scan live player sessions" in response.text
    assert "job-row-summary" in response.text
    assert "Job details" in response.text
    assert f'data-job-details-id="{job.id}"' in response.text
    assert "succeeded" in response.text
    assert "Job completed." in response.text
    assert "Last stdout lines" in response.text or "Останні рядки stdout" in response.text
    assert "owner" in response.text
    assert 'action="/logout"' in response.text


def test_jobs_page_formats_utc_timestamps(tmp_path: Path):
    from armactl.web.app import create_app

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    db_path = tmp_path / "web" / "web.db"
    job_id = _insert_raw_web_job(
        db_path,
        kind="server:update-check",
        status="failed",
        created_at="2026-06-27T18:10:58.356827+00:00",
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE web_jobs
               SET started_at = ?, finished_at = ?, updated_at = ?
             WHERE id = ?
            """,
            (
                "2026-06-27T18:10:58.381890+00:00",
                "2026-06-27T18:12:28.483776+00:00",
                "2026-06-27T18:12:28.483776+00:00",
                job_id,
            ),
        )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/jobs", follow_redirects=False)

    assert response.status_code == 200
    assert 'data-local-time datetime="2026-06-27T18:10:58.356827Z"' in response.text
    assert 'data-local-time datetime="2026-06-27T18:12:28.483776Z"' in response.text
    assert "2026-06-27 18:10 UTC" in response.text
    assert "2026-06-27 18:12 UTC" in response.text
    assert "2026-06-27T18:10:58.356827+00:00" not in response.text
    assert "2026-06-27T18:12:28.483776+00:00" not in response.text


def test_jobs_js_static_asset_is_served(tmp_path: Path):
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    response = client.get("/static/js/jobs.js", follow_redirects=False)

    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert "data-jobs-refresh-root" in response.text
    assert "job-card-running" in response.text
    assert "background-jobs" in response.text
    assert "collectOpenJobDetails" in response.text
    assert "restoreOpenJobDetails" in response.text
    assert "data-job-details-id" in response.text

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
    assert "Інші завдання" in response.text


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
    assert "/static/js/jobs.js" in response.text
    assert "data-jobs-refresh-root" in response.text
    assert "No pending operator work." in response.text
    assert "No background jobs." in response.text
    assert "No jobs yet." not in response.text


def test_jobs_page_shows_fresh_worker_lease_for_running_job(tmp_path: Path):
    from armactl.web.app import create_app
    from armactl.web.jobs import create_job, mark_job_running

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    db_path = tmp_path / "web" / "web.db"
    job = create_job(db_path, kind="safe:lease", requested_by_username="owner")
    mark_job_running(db_path, job.id, worker_id="worker1234")
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/jobs", follow_redirects=False)

    assert response.status_code == 200
    assert "Worker lease" in response.text
    assert "fresh lease" in response.text
    assert "Last heartbeat" in response.text
    assert "Lease expires" in response.text
    assert "worker1234" not in response.text
    assert "Worker lease expired." not in response.text


def test_jobs_page_shows_expired_worker_lease_diagnostic_safely(tmp_path: Path):
    from armactl.web.app import create_app
    from armactl.web.jobs import create_job, mark_job_running

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    db_path = tmp_path / "web" / "web.db"
    job = create_job(db_path, kind="safe:expired", requested_by_username="owner")
    running = mark_job_running(db_path, job.id, worker_id="worker1234")
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE web_jobs
            SET worker_heartbeat_at = ?, worker_lease_expires_at = ?
            WHERE id = ?
            """,
            (
                "2000-01-01T00:00:00+00:00",
                "2000-01-01T00:00:00+00:00",
                running.id,
            ),
        )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/jobs", follow_redirects=False)

    assert response.status_code == 200
    assert "Worker lease expired." in response.text
    assert "expired lease" in response.text
    assert "Mark abandoned" in response.text
    assert "Does not kill a worker; only marks expired metadata" in response.text
    assert "Review job details to mark abandoned." in response.text
    assert "The running job remains visible" in response.text
    assert "worker1234" not in response.text
    assert "Traceback" not in response.text
    assert "ARMACTL_WEB_SESSION_SECRET" not in response.text


def test_post_mark_stale_running_job_abandoned_updates_expired_running_metadata(
    tmp_path: Path,
):
    from armactl.web.app import create_app
    from armactl.web.jobs import create_job, get_job, mark_job_running

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    db_path = tmp_path / "web" / "web.db"
    job = create_job(db_path, kind="safe:expired", requested_by_username="owner")
    running = mark_job_running(db_path, job.id, worker_id="worker1234")
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE web_jobs
            SET worker_heartbeat_at = ?, worker_lease_expires_at = ?
            WHERE id = ?
            """,
            (
                "2000-01-01T00:00:00+00:00",
                "2000-01-01T00:00:00+00:00",
                running.id,
            ),
        )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    csrf_token = _jobs_csrf_token(client)

    response = client.post(
        f"/jobs/{running.id}/mark-abandoned",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    refreshed = get_job(db_path, running.id)
    audit_events = _audit_events(tmp_path)

    assert response.status_code == 303
    assert response.headers["location"] == "/jobs"
    assert refreshed.status == "abandoned"
    assert refreshed.finished_at is not None
    assert "no worker process was killed" in refreshed.result_message
    assert audit_events[-1]["action"] == "job.stale-running.mark-abandoned"
    assert audit_events[-1]["success"] is True
    assert audit_events[-1]["details"]["marked"] == "true"
    assert audit_events[-1]["details"]["job_status"] == "abandoned"
    assert "worker1234" not in _audit_log_text(tmp_path)


def test_post_mark_stale_running_job_abandoned_rejects_fresh_running_lease(
    tmp_path: Path,
):
    from armactl.web.app import create_app
    from armactl.web.jobs import create_job, get_job, mark_job_running

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    db_path = tmp_path / "web" / "web.db"
    job = create_job(db_path, kind="safe:fresh", requested_by_username="owner")
    running = mark_job_running(db_path, job.id, worker_id="worker1234")
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    csrf_token = _jobs_csrf_token(client)

    response = client.post(
        f"/jobs/{running.id}/mark-abandoned",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert response.text == "Job was not eligible for stale metadata recovery."
    assert get_job(db_path, running.id).status == "running"
    assert "worker1234" not in response.text
    assert "Traceback" not in response.text


def test_mark_stale_running_job_abandoned_route_keeps_auth_permission_csrf_guards(
    tmp_path: Path,
    set_web_owner_permissions,
):
    from armactl.web.app import create_app
    from armactl.web.auth import permissions

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)

    unauthenticated = _client(create_app(data_root=tmp_path))
    unauth_response = unauthenticated.post(
        "/jobs/1/mark-abandoned",
        data={},
        follow_redirects=False,
    )

    set_web_owner_permissions(set())
    denied = _client(create_app(data_root=tmp_path))
    _login(denied, "owner", password)
    denied_response = denied.post(
        "/jobs/1/mark-abandoned",
        data={},
        follow_redirects=False,
    )

    set_web_owner_permissions(permissions.ALL_PERMISSIONS)
    csrf_client = _client(create_app(data_root=tmp_path))
    _login(csrf_client, "owner", password)
    csrf_response = csrf_client.post(
        "/jobs/1/mark-abandoned",
        data={"csrf_token": "bad-token"},
        follow_redirects=False,
    )

    assert unauth_response.status_code == 303
    assert unauth_response.headers["location"] == "/login"
    assert denied_response.status_code == 403
    assert denied_response.text == "Permission denied."
    assert csrf_response.status_code == 403
    assert csrf_response.text == "Invalid CSRF token."


def test_jobs_page_mark_abandoned_button_only_for_eligible_stale_running_rows(
    tmp_path: Path,
):
    from armactl.web.app import create_app
    from armactl.web.jobs import create_job, mark_job_running

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    db_path = tmp_path / "web" / "web.db"
    expired_job = create_job(db_path, kind="safe:expired", requested_by_username="owner")
    expired = mark_job_running(db_path, expired_job.id, worker_id="worker1234")
    fresh_job = create_job(db_path, kind="safe:fresh", requested_by_username="owner")
    fresh = mark_job_running(db_path, fresh_job.id, worker_id="worker5678")
    unknown_job = create_job(db_path, kind="safe:unknown", requested_by_username="owner")
    unknown = mark_job_running(db_path, unknown_job.id)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE web_jobs
            SET worker_heartbeat_at = ?, worker_lease_expires_at = ?
            WHERE id = ?
            """,
            (
                "2000-01-01T00:00:00+00:00",
                "2000-01-01T00:00:00+00:00",
                expired.id,
            ),
        )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/jobs", follow_redirects=False)

    assert response.status_code == 200
    assert f"action=\"/jobs/{expired.id}/mark-abandoned\"" in response.text
    assert f"action=\"/jobs/{unknown.id}/mark-abandoned\"" in response.text
    assert f"action=\"/jobs/{fresh.id}/mark-abandoned\"" not in response.text
    assert response.text.count("Mark abandoned") == 2
    assert "Does not kill a worker; only marks expired metadata" in response.text
    assert "worker1234" not in response.text
    assert "worker5678" not in response.text


def test_mark_stale_running_job_abandoned_audit_failure_is_controlled(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.jobs import create_job, get_job, mark_job_running
    from armactl.web.services import job_recovery
    from armactl.web.services.audit import AuditLogError

    def fail_audit(*args, **kwargs):
        raise AuditLogError("disk full token=raw-job-secret")

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    db_path = tmp_path / "web" / "web.db"
    job = create_job(db_path, kind="safe:expired", requested_by_username="owner")
    running = mark_job_running(db_path, job.id, worker_id="worker1234")
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE web_jobs
            SET worker_heartbeat_at = ?, worker_lease_expires_at = ?
            WHERE id = ?
            """,
            (
                "2000-01-01T00:00:00+00:00",
                "2000-01-01T00:00:00+00:00",
                running.id,
            ),
        )
    monkeypatch.setattr(job_recovery, "append_audit_event", fail_audit)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    csrf_token = _jobs_csrf_token(client)

    response = client.post(
        f"/jobs/{running.id}/mark-abandoned",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 500
    assert response.text == (
        "Job recovery action could not be completed because audit logging failed."
    )
    assert get_job(db_path, running.id).status == "running"
    assert "raw-job-secret" not in response.text
    assert "Traceback" not in response.text


def test_mark_stale_running_job_abandoned_response_does_not_render_raw_output(
    tmp_path: Path,
):
    from armactl.web.app import create_app
    from armactl.web.jobs import create_job, mark_job_running

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    db_path = tmp_path / "web" / "web.db"
    job = create_job(db_path, kind="safe:expired", requested_by_username="owner")
    running = mark_job_running(db_path, job.id, worker_id="worker1234")
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE web_jobs
            SET stdout_tail = ?, stderr_tail = ?, worker_heartbeat_at = ?,
                worker_lease_expires_at = ?
            WHERE id = ?
            """,
            (
                "raw command /srv/arma/password-file secret=hunter2",
                "ARMACTL_WEB_SESSION_SECRET=raw-secret",
                "2000-01-01T00:00:00+00:00",
                "2000-01-01T00:00:00+00:00",
                running.id,
            ),
        )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    csrf_token = _jobs_csrf_token(client)

    response = client.post(
        f"/jobs/{running.id}/mark-abandoned",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "/srv/arma/password-file" not in response.text
    assert "hunter2" not in response.text
    assert "raw-secret" not in response.text


def test_get_jobs_does_not_mutate_duplicate_or_expired_running_jobs(tmp_path: Path):
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
    running_id = _insert_raw_web_job(
        db_path,
        kind=SERVER_INSTALL_JOB_KIND,
        status="running",
        created_at="2026-01-01T00:00:01+00:00",
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE web_jobs
            SET worker_id = ?, worker_heartbeat_at = ?, worker_lease_expires_at = ?
            WHERE id = ?
            """,
            (
                "worker1234",
                "2000-01-01T00:00:00+00:00",
                "2000-01-01T00:00:00+00:00",
                running_id,
            ),
        )
        before = connection.execute(
            "SELECT id, status, finished_at, worker_lease_expires_at FROM web_jobs ORDER BY id"
        ).fetchall()
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/jobs", follow_redirects=False)

    with sqlite3.connect(db_path) as connection:
        after = connection.execute(
            "SELECT id, status, finished_at, worker_lease_expires_at FROM web_jobs ORDER BY id"
        ).fetchall()
    assert response.status_code == 200
    assert after == before
    assert "Duplicate active jobs detected." in response.text
    assert "Worker lease expired." in response.text
    assert "Job-store maintenance completed." not in response.text


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
    assert (
        "Queued duplicates can be repaired by job-store maintenance; running rows "
        "remain visible and are not cancelled by the web UI."
        in response.text
    )
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
    from armactl.web.jobs import SERVER_INSTALL_JOB_KIND, enqueue_server_install

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
        status="queued",
        created_at="2026-01-01T00:00:01+00:00",
    )
    assert enqueue_server_install(db_path, requested_by_username="owner").id == kept
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/jobs", follow_redirects=False)

    assert response.status_code == 200
    assert "Job-store integrity" in response.text
    assert "Job-store maintenance completed." in response.text
    assert "Duplicate queued jobs were repaired. Oldest active jobs were kept." in response.text
    assert (
        "Cancelled duplicate queued jobs remain visible below for audit context."
        in response.text
    )
    assert "Repaired duplicate queued jobs" in response.text
    assert "Last repair" in response.text
    assert "notice-panel diagnostic-notice" in response.text
    assert "notice-panel notice-warning" not in response.text
    assert "Duplicate active jobs detected." not in response.text
    assert "Web job-store maintenance cancelled duplicate active jobs." not in response.text
    assert "Review cancelled jobs below." not in response.text
    assert "Cancelled by web job-store maintenance; older active job kept." in response.text
    assert "Duplicate queued job cancelled" in response.text
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
    from armactl.web.jobs import SERVER_INSTALL_JOB_KIND, enqueue_server_install

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
        status="queued",
        created_at="2026-01-01T00:00:01+00:00",
    )
    enqueue_server_install(db_path, requested_by_username="owner")
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    _set_cookie(client, LANGUAGE_COOKIE_NAME, "uk")

    response = client.get("/jobs", follow_redirects=False)

    assert response.status_code == 200
    assert "Цілісність сховища завдань" in response.text
    assert "Обслуговування сховища завдань завершено." in response.text
    assert "Дублікати завдань у черзі виправлено." in response.text
    assert "Виправлені дублікати завдань у черзі" in response.text
    assert "Job-store maintenance completed." not in response.text
    assert "Duplicate queued jobs were repaired." not in response.text
    assert "Repaired duplicate queued jobs" not in response.text
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
    assert "Start or restart game server" in response.text
    assert "Background jobs" in response.text
    assert "Config changes" in response.text
    assert 'href="/config"' in response.text
    assert "Save config" in response.text
    assert "config.save" not in response.text
    assert "max_players" in response.text
    assert "No background jobs." in response.text
    assert "No jobs yet." not in response.text
    assert "pending-work-list" in response.text
    assert "pending-work-item" in response.text
    assert "pending-work-main-row" in response.text
    assert "pending-work-meta-row" in response.text
    assert "pending-work-dates-row" in response.text
    assert "pending-work-table" not in response.text
    assert "hunter2" not in response.text
    assert "raw-token" not in response.text
    assert "session-secret" not in response.text
    assert "password=***" in response.text
    assert "token=***" in response.text
    assert "ARMACTL_WEB_SESSION_SECRET=***" in response.text
    assert SESSION_COOKIE_NAME not in response.text
    assert CSRF_COOKIE_NAME not in response.text



def test_jobs_page_shows_human_pending_admin_action(tmp_path: Path):
    from armactl.web.app import create_app
    from armactl.web.services.pending_work import KIND_ADMINS, mark_restart_pending

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    mark_restart_pending(
        tmp_path / "web" / "web.db",
        kind=KIND_ADMINS,
        source_action="admin.remove",
        username="owner",
        details="76561198000000001",
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    _set_cookie(client, LANGUAGE_COOKIE_NAME, "uk")

    response = client.get("/jobs", follow_redirects=False)

    assert response.status_code == 200
    assert "Зміни адмінів" in response.text
    assert "Видалення адміна" in response.text
    assert "76561198000000001" in response.text
    assert "Запустити або перезапустити ігровий сервер" in response.text
    assert "admin.remove" not in response.text
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


@pytest.mark.parametrize(
    ("action", "path", "job_kind"),
    [
        ("install", "/jobs/server/install", "server:install"),
        ("repair", "/jobs/server/repair", "server:repair"),
    ],
)
def test_post_server_job_reuses_existing_active_job_without_duplicate_worker(
    tmp_path: Path,
    monkeypatch,
    action: str,
    path: str,
    job_kind: str,
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
        path,
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    second_response = client.post(
        path,
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    jobs = [
        job
        for job in list_recent_jobs(tmp_path / "web" / "web.db")
        if job.kind == job_kind
    ]
    audit_events = _audit_events(tmp_path)

    assert first_response.status_code == 303
    assert first_response.headers["location"] == "/jobs"
    assert second_response.status_code == 303
    assert second_response.headers["location"] == "/jobs"
    assert len(jobs) == 1
    assert jobs[0].status == "queued"
    assert scheduled == [jobs[0].id]
    assert [event["action"] for event in audit_events[-2:]] == [
        f"job.server-{action}.enqueue",
        f"job.server-{action}.enqueue",
    ]
    assert [event["details"]["created"] for event in audit_events[-2:]] == ["true", "false"]


@pytest.mark.parametrize(
    ("path", "job_kind"),
    [
        ("/jobs/server/install", "server:install"),
        ("/jobs/server/repair", "server:repair"),
    ],
)
def test_server_job_intent_audit_failure_does_not_create_job(
    tmp_path: Path,
    monkeypatch,
    path: str,
    job_kind: str,
):
    from armactl.web.app import create_app
    from armactl.web.jobs.store import list_recent_jobs
    from armactl.web.services import server_job_actions
    from armactl.web.services.audit import AuditLogError

    def fail_audit(*args, **kwargs):
        raise AuditLogError("disk full token=raw-job-secret")

    def fail_worker(*args, **kwargs):
        raise AssertionError("worker should not start when intent audit fails")

    monkeypatch.setattr(server_job_actions, "append_audit_event", fail_audit)
    monkeypatch.setattr(server_job_actions.server_jobs, "start_server_job_worker", fail_worker)

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    csrf_token = _jobs_csrf_token(client)

    response = client.post(
        path,
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    jobs = [job for job in list_recent_jobs(tmp_path / "web" / "web.db") if job.kind == job_kind]

    assert response.status_code == 500
    assert response.text == "Job was not queued because audit logging failed."
    assert jobs == []
    assert "raw-job-secret" not in response.text
    assert "Traceback" not in response.text


@pytest.mark.parametrize(
    ("path", "job_kind"),
    [
        ("/jobs/server/install", "server:install"),
        ("/jobs/server/repair", "server:repair"),
    ],
)
def test_server_job_outcome_audit_failure_cancels_created_job(
    tmp_path: Path,
    monkeypatch,
    path: str,
    job_kind: str,
):
    from armactl.web.app import create_app
    from armactl.web.jobs.store import list_recent_jobs
    from armactl.web.services import server_job_actions
    from armactl.web.services.audit import AuditLogError

    def fail_outcome_audit(*args, **kwargs):
        details = kwargs.get("details") or {}
        if details.get("phase") == "outcome":
            raise AuditLogError("disk full token=raw-job-secret")

    def fail_worker(*args, **kwargs):
        raise AssertionError("worker should not start when outcome audit fails")

    monkeypatch.setattr(server_job_actions, "append_audit_event", fail_outcome_audit)
    monkeypatch.setattr(server_job_actions.server_jobs, "start_server_job_worker", fail_worker)

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    csrf_token = _jobs_csrf_token(client)

    response = client.post(
        path,
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    jobs = [job for job in list_recent_jobs(tmp_path / "web" / "web.db") if job.kind == job_kind]

    assert response.status_code == 500
    assert response.text == "Job action could not be completed because audit logging failed."
    assert len(jobs) == 1
    assert jobs[0].status == "cancelled"
    assert jobs[0].result_message == "Cancelled because audit logging failed."
    assert "raw-job-secret" not in response.text
    assert "Traceback" not in response.text


def test_existing_active_server_job_outcome_audit_failure_is_controlled(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.jobs import server as server_jobs
    from armactl.web.jobs.store import list_recent_jobs
    from armactl.web.services import server_job_actions
    from armactl.web.services.audit import AuditLogError

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

    def fail_outcome_audit(*args, **kwargs):
        details = kwargs.get("details") or {}
        if details.get("phase") == "outcome":
            raise AuditLogError("disk full token=raw-job-secret")

    monkeypatch.setattr(server_job_actions, "append_audit_event", fail_outcome_audit)
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

    assert first_response.status_code == 303
    assert second_response.status_code == 500
    assert second_response.text == "Job action could not be completed because audit logging failed."
    assert len(jobs) == 1
    assert jobs[0].status == "queued"
    assert scheduled == [jobs[0].id]
    assert "raw-job-secret" not in second_response.text
    assert "Traceback" not in second_response.text


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


def test_unauthenticated_update_job_redirects_to_login(tmp_path: Path):
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    response = client.post("/jobs/server/update", data={}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_update_job_permission_denied_returns_403(
    tmp_path: Path,
    monkeypatch,
    set_web_owner_permissions,
):
    from armactl.web.app import create_app
    from armactl.web.auth import permissions
    from armactl.web.services import server_job_actions

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    set_web_owner_permissions(permissions.ALL_PERMISSIONS - {permissions.SERVER_UPDATE})
    monkeypatch.setattr(
        server_job_actions,
        "request_server_update_and_start",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("update backend should not be called")
        ),
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.post("/jobs/server/update", data={}, follow_redirects=False)

    assert response.status_code == 403
    assert response.text == "Permission denied."


def test_update_job_requires_csrf(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app
    from armactl.web.services import server_job_actions

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    monkeypatch.setattr(
        server_job_actions,
        "request_server_update_and_start",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("update backend should not be called before csrf")
        ),
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.post(
        "/jobs/server/update",
        data={"csrf_token": "bad-token"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.text == "Invalid CSRF token."


def test_unauthenticated_update_check_job_redirects_to_login(tmp_path: Path):
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    response = client.post("/jobs/server/update-check", data={}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_update_check_job_permission_denied_returns_403(
    tmp_path: Path,
    monkeypatch,
    set_web_owner_permissions,
):
    from armactl.web.app import create_app
    from armactl.web.auth import permissions
    from armactl.web.services import server_job_actions

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    set_web_owner_permissions(permissions.ALL_PERMISSIONS - {permissions.SERVER_UPDATE})
    monkeypatch.setattr(
        server_job_actions,
        "request_server_update_check_and_start",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("update-check backend should not be called")
        ),
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.post("/jobs/server/update-check", data={}, follow_redirects=False)

    assert response.status_code == 403
    assert response.text == "Permission denied."


def test_update_check_job_requires_csrf(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app
    from armactl.web.services import server_job_actions

    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    monkeypatch.setattr(
        server_job_actions,
        "request_server_update_check_and_start",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("update-check backend should not be called before csrf")
        ),
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.post(
        "/jobs/server/update-check",
        data={"csrf_token": "bad-token"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.text == "Invalid CSRF token."


def test_post_update_check_creates_queued_job_without_running_backend(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.jobs import server as server_jobs
    from armactl.web.jobs.store import list_recent_jobs
    from armactl.web.services import server_versions

    monkeypatch.setattr(
        server_versions.installer,
        "fetch_steam_app_info",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("HTTP request must not run SteamCMD latest check")
        ),
    )
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

    response = client.post(
        "/jobs/server/update-check",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    jobs = [
        job
        for job in list_recent_jobs(tmp_path / "web" / "web.db")
        if job.kind == "server:update-check"
    ]
    audit_events = _audit_events(tmp_path)

    assert response.status_code == 303
    assert response.headers["location"] == "/jobs"
    assert len(jobs) == 1
    assert jobs[0].status == "queued"
    assert jobs[0].current_step == "Queued update check"
    assert scheduled == [jobs[0].id]
    assert audit_events[-1]["action"] == "job.server-update-check.enqueue"
    assert audit_events[-1]["details"]["job_kind"] == "server:update-check"
    assert audit_events[-1]["details"]["created"] == "true"


def test_post_update_check_uses_fresh_cache_without_creating_job(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.jobs import server as server_jobs
    from armactl.web.jobs.store import list_recent_jobs
    from armactl.web.services import server_versions

    scheduled: list[int] = []
    monkeypatch.setattr(
        server_jobs,
        "start_server_job_worker",
        lambda db_path, job_id: scheduled.append(job_id),
    )
    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    db_path = tmp_path / "web" / "web.db"
    server_versions.save_server_version_check(
        db_path,
        installed="100",
        latest="101",
        check_state=server_versions.SERVER_VERSION_CHECK_AVAILABLE,
        source=server_versions.STEAMCMD_APP_INFO_SOURCE,
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    csrf_token = _jobs_csrf_token(client)

    response = client.post(
        "/jobs/server/update-check",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    jobs = [
        job
        for job in list_recent_jobs(db_path)
        if job.kind == "server:update-check"
    ]

    assert response.status_code == 303
    assert response.headers["location"] == "/jobs"
    assert jobs == []
    assert scheduled == []

def test_post_update_noops_when_server_is_up_to_date(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app
    from armactl.web.jobs import server as server_jobs
    from armactl.web.jobs.store import list_recent_jobs
    from armactl.web.services import server_job_actions, server_versions

    monkeypatch.setattr(
        server_job_actions.server_versions,
        "load_server_version_state",
        lambda **kwargs: _server_version_state(
            server_versions.SERVER_VERSION_CHECK_UPTODATE
        ),
    )
    monkeypatch.setattr(
        server_jobs,
        "start_server_job_worker",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("up-to-date update must not start a worker")
        ),
    )
    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    csrf_token = _jobs_csrf_token(client)

    response = client.post(
        "/jobs/server/update",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    jobs = list_recent_jobs(tmp_path / "web" / "web.db")
    audit_events = _audit_events(tmp_path)

    assert response.status_code == 200
    assert response.text == "Server is already up to date"
    assert [job for job in jobs if job.kind == "server:update"] == []
    assert audit_events[-1]["action"] == "job.server-update.check"
    assert audit_events[-1]["success"] is True
    assert audit_events[-1]["details"]["check_state"] == "uptodate"
    assert audit_events[-1]["details"]["installed"] == "100"
    assert audit_events[-1]["details"]["latest"] == "100"


@pytest.mark.parametrize(
    ("check_state", "message"),
    [
        ("unknown", "Latest build unknown"),
        ("failed", "Build check failed"),
    ],
)
def test_post_update_fails_closed_when_latest_unknown_or_check_failed(
    tmp_path: Path,
    monkeypatch,
    check_state: str,
    message: str,
):
    from armactl.web.app import create_app
    from armactl.web.jobs import server as server_jobs
    from armactl.web.jobs.store import list_recent_jobs
    from armactl.web.services import server_job_actions

    monkeypatch.setattr(
        server_job_actions.server_versions,
        "load_server_version_state",
        lambda **kwargs: _server_version_state(check_state),
    )
    monkeypatch.setattr(
        server_jobs,
        "start_server_job_worker",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("fail-closed update must not start a worker")
        ),
    )
    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    csrf_token = _jobs_csrf_token(client)

    response = client.post(
        "/jobs/server/update",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    jobs = list_recent_jobs(tmp_path / "web" / "web.db")

    assert response.status_code == 409
    assert response.text == message
    assert [job for job in jobs if job.kind == "server:update"] == []


def test_post_update_available_creates_queued_job_without_running_backend(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.jobs import server as server_jobs
    from armactl.web.jobs.store import list_recent_jobs
    from armactl.web.services import server_job_actions, server_versions

    load_calls: list[dict] = []

    def fake_load_server_version_state(**kwargs):
        load_calls.append(kwargs)
        return _server_version_state(
            server_versions.SERVER_VERSION_CHECK_AVAILABLE
        )

    monkeypatch.setattr(
        server_job_actions.server_versions,
        "load_server_version_state",
        fake_load_server_version_state,
    )
    monkeypatch.setattr(
        server_jobs.installer,
        "stream_server_update",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("HTTP request must not run update backend")
        ),
    )
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

    response = client.post(
        "/jobs/server/update",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    jobs = [
        job
        for job in list_recent_jobs(tmp_path / "web" / "web.db")
        if job.kind == "server:update"
    ]
    audit_events = _audit_events(tmp_path)

    assert response.status_code == 303
    assert response.headers["location"] == "/jobs"
    assert len(jobs) == 1
    assert jobs[0].status == "queued"
    assert jobs[0].current_step == "Queued update"
    assert scheduled == [jobs[0].id]
    assert load_calls == [
        {
            "instance": "default",
            "db_path": tmp_path / "web" / "web.db",
            "server_running": False,
        }
    ]
    assert audit_events[-1]["action"] == "job.server-update.enqueue"
    assert audit_events[-1]["details"]["job_kind"] == "server:update"
    assert audit_events[-1]["details"]["created"] == "true"


def test_post_update_running_server_queues_safe_update_job(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.jobs import server as server_jobs
    from armactl.web.jobs.store import list_recent_jobs
    from armactl.web.services import server_job_actions, server_versions

    class FakeServiceAdapter:
        def get_service_status(self):
            return {"active_state": "active", "sub_state": "running"}

    monkeypatch.setattr(
        server_job_actions,
        "get_service_adapter",
        lambda: FakeServiceAdapter(),
    )
    monkeypatch.setattr(
        server_job_actions.server_versions,
        "load_server_version_state",
        lambda **kwargs: _server_version_state(
            server_versions.SERVER_VERSION_CHECK_AVAILABLE,
            running=kwargs["server_running"],
        ),
    )
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

    response = client.post(
        "/jobs/server/update",
        data={"csrf_token": csrf_token, "confirm": "running-update"},
        follow_redirects=False,
    )
    jobs = [
        job
        for job in list_recent_jobs(tmp_path / "web" / "web.db")
        if job.kind == "server:update"
    ]
    audit_events = _audit_events(tmp_path)

    assert response.status_code == 303
    assert len(jobs) == 1
    assert scheduled == [jobs[0].id]
    assert audit_events[-1]["action"] == "job.server-update.enqueue"
    assert audit_events[-1]["success"] is True


def test_update_job_intent_audit_failure_does_not_create_job(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.jobs.store import list_recent_jobs
    from armactl.web.services import server_job_actions, server_versions
    from armactl.web.services.audit import AuditLogError

    monkeypatch.setattr(
        server_job_actions.server_versions,
        "load_server_version_state",
        lambda **kwargs: _server_version_state(
            server_versions.SERVER_VERSION_CHECK_AVAILABLE
        ),
    )
    monkeypatch.setattr(
        server_job_actions,
        "append_audit_event",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AuditLogError("disk full token=raw-job-secret")
        ),
    )
    monkeypatch.setattr(
        server_job_actions.server_jobs,
        "start_server_job_worker",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("worker should not start when intent audit fails")
        ),
    )
    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    csrf_token = _jobs_csrf_token(client)

    response = client.post(
        "/jobs/server/update",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    jobs = [
        job
        for job in list_recent_jobs(tmp_path / "web" / "web.db")
        if job.kind == "server:update"
    ]

    assert response.status_code == 500
    assert response.text == "Job was not queued because audit logging failed."
    assert jobs == []
    assert "raw-job-secret" not in response.text


def test_update_job_outcome_audit_failure_cancels_created_job(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.jobs.store import list_recent_jobs
    from armactl.web.services import server_job_actions, server_versions
    from armactl.web.services.audit import AuditLogError

    monkeypatch.setattr(
        server_job_actions.server_versions,
        "load_server_version_state",
        lambda **kwargs: _server_version_state(
            server_versions.SERVER_VERSION_CHECK_AVAILABLE
        ),
    )

    def fail_outcome_audit(*args, **kwargs):
        details = kwargs.get("details") or {}
        if details.get("phase") == "outcome":
            raise AuditLogError("disk full token=raw-job-secret")

    monkeypatch.setattr(server_job_actions, "append_audit_event", fail_outcome_audit)
    monkeypatch.setattr(
        server_job_actions.server_jobs,
        "start_server_job_worker",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("worker should not start when outcome audit fails")
        ),
    )
    password = "owner jobs password"
    setup_owner_user(tmp_path, "owner", password)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    csrf_token = _jobs_csrf_token(client)

    response = client.post(
        "/jobs/server/update",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    jobs = [
        job
        for job in list_recent_jobs(tmp_path / "web" / "web.db")
        if job.kind == "server:update"
    ]

    assert response.status_code == 500
    assert response.text == "Job action could not be completed because audit logging failed."
    assert len(jobs) == 1
    assert jobs[0].status == "cancelled"
    assert jobs[0].result_message == "Cancelled because audit logging failed."
    assert "raw-job-secret" not in response.text
