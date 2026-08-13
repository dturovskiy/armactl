from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest
from web_route_helpers import _client, _form_token, _login

from armactl.state import ServerState, save_state
from armactl.web.auth.permissions import DASHBOARD_VIEW
from armactl.web.auth.setup import setup_owner_user
from armactl.web.services import server_versions


def _version_state(check_state: str, *, running: bool = False):
    if check_state == server_versions.SERVER_VERSION_CHECK_UPTODATE:
        return server_versions.ServerVersionState(
            installed="100",
            latest="100",
            branch="public",
            last_checked="2026-06-21T00:00:00+00:00",
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
            last_checked="2026-06-21T00:00:00+00:00",
            check_state=check_state,
            status="update available",
            message="Update available",
            can_update=True,
            server_running=running,
        )
    if check_state == server_versions.SERVER_VERSION_CHECK_FAILED:
        return server_versions.ServerVersionState(
            installed="100",
            latest="",
            branch="public",
            last_checked="2026-06-21T00:00:00+00:00",
            check_state=check_state,
            status="check failed",
            message="Build check failed",
            failure_reason="probe failed",
            server_running=running,
        )
    if check_state == server_versions.SERVER_VERSION_CHECK_CHECKING:
        return server_versions.ServerVersionState(
            installed="100",
            latest="101",
            branch="public",
            last_checked="2026-06-21T00:00:00+00:00",
            check_state=check_state,
            status="checking",
            message="Checking for updates",
            check_job_id=7,
            server_running=running,
        )
    if check_state == server_versions.SERVER_VERSION_CHECK_UPDATING:
        return server_versions.ServerVersionState(
            installed="100",
            latest="101",
            branch="public",
            last_checked="2026-06-21T00:00:00+00:00",
            check_state=check_state,
            status="updating",
            message="Update job running",
            update_job_id=9,
            server_running=running,
        )
    return server_versions.ServerVersionState(
        installed="100",
        latest="",
        branch="public",
        check_state=server_versions.SERVER_VERSION_CHECK_UNKNOWN,
        status="unknown",
        message="Latest build unknown",
        server_running=running,
    )


def _updates_page(check_state: str, *, running: bool = False) -> dict:
    return dict(
        instance="default",
        server_installed=True,
        server_running=running,
        version=_version_state(check_state, running=running).to_dict(),
    )


def _install_updates_page(monkeypatch, page: dict) -> None:
    from armactl.web.page_models import updates as updates_page_model

    monkeypatch.setattr(
        updates_page_model,
        "load_updates_page",
        lambda *args, **kwargs: page,
    )


def _updates_csrf_token(client) -> str:
    response = client.get("/updates")
    assert response.status_code == 200
    return _form_token(response.text)


def test_dashboard_profile_switch_redirects_back_to_dashboard(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.services import server_job_actions

    password = "owner updates password"
    setup_owner_user(tmp_path, "owner", password)
    _install_updates_page(
        monkeypatch,
        _updates_page(server_versions.SERVER_VERSION_CHECK_UPTODATE),
    )
    requested: dict[str, str] = {}

    def request_profile(*args, action: str, name: str, **kwargs):
        del args, kwargs
        requested.update(action=action, name=name)

    monkeypatch.setattr(
        server_job_actions,
        "request_server_profile_action_and_start",
        request_profile,
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    csrf_token = _updates_csrf_token(client)

    response = client.post(
        "/updates/profile/switch",
        data={
            "csrf_token": csrf_token,
            "profile_name": "serhiivka-modded",
            "return_to": "dashboard",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard?notice=profile-queued"
    assert requested == {"action": "switch", "name": "serhiivka-modded"}


@pytest.mark.parametrize(
    ("source", "name", "expected_action", "expected_name"),
    [
        ("named", "serhiivka-modded", "test", "serhiivka-modded"),
        ("parked", "", "test-parked", ""),
    ],
)
def test_profile_test_route_queues_test_only_job(
    tmp_path: Path,
    monkeypatch,
    source: str,
    name: str,
    expected_action: str,
    expected_name: str,
):
    from armactl.web.app import create_app
    from armactl.web.services import server_job_actions

    password = "owner profile test password"
    setup_owner_user(tmp_path, "owner", password)
    _install_updates_page(
        monkeypatch,
        _updates_page(server_versions.SERVER_VERSION_CHECK_UPTODATE),
    )
    requested: dict[str, str] = {}

    def request_profile(*args, action: str, name: str, **kwargs):
        del args, kwargs
        requested.update(action=action, name=name)

    monkeypatch.setattr(
        server_job_actions,
        "request_server_profile_action_and_start",
        request_profile,
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    csrf_token = _updates_csrf_token(client)

    response = client.post(
        "/updates/profile/test",
        data={
            "csrf_token": csrf_token,
            "profile_source": source,
            "profile_name": name,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/updates?notice=profile-test-queued"
    assert requested == {"action": expected_action, "name": expected_name}


@pytest.mark.parametrize(
    ("selection", "expected_action", "expected_name"),
    [
        ("vanilla", "vanilla", ""),
        ("retry-modded", "retry-modded", ""),
        ("profile:serhiivka-modded", "switch", "serhiivka-modded"),
    ],
)
def test_compact_profile_selector_dispatches_explicit_action(
    tmp_path: Path,
    monkeypatch,
    selection: str,
    expected_action: str,
    expected_name: str,
):
    from armactl.web.app import create_app
    from armactl.web.services import server_job_actions

    password = "owner compact profile password"
    setup_owner_user(tmp_path, "owner", password)
    _install_updates_page(
        monkeypatch,
        _updates_page(server_versions.SERVER_VERSION_CHECK_UPTODATE),
    )
    requested: dict[str, str] = {}

    def request_profile(*args, action: str, name: str, **kwargs):
        del args, kwargs
        requested.update(action=action, name=name)

    monkeypatch.setattr(
        server_job_actions,
        "request_server_profile_action_and_start",
        request_profile,
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    csrf_token = _updates_csrf_token(client)

    response = client.post(
        "/updates/profile/select",
        data={
            "csrf_token": csrf_token,
            "profile_selection": selection,
            "return_to": "dashboard",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard?notice=profile-queued"
    assert requested == {"action": expected_action, "name": expected_name}


def test_compact_profile_selector_rejects_invalid_selection(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.services import server_job_actions

    password = "owner invalid compact profile password"
    setup_owner_user(tmp_path, "owner", password)
    _install_updates_page(
        monkeypatch,
        _updates_page(server_versions.SERVER_VERSION_CHECK_UPTODATE),
    )

    def fail_request(*args, **kwargs):
        del args, kwargs
        raise AssertionError("invalid profile selection must not queue a job")

    monkeypatch.setattr(
        server_job_actions,
        "request_server_profile_action_and_start",
        fail_request,
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    csrf_token = _updates_csrf_token(client)

    response = client.post(
        "/updates/profile/select",
        data={
            "csrf_token": csrf_token,
            "profile_selection": "profile:../invalid",
            "return_to": "dashboard",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert response.text == "Profile selection is invalid."


@pytest.mark.parametrize(
    ("check_state", "message", "state_label", "check_disabled", "shows_update"),
    [
        (
            server_versions.SERVER_VERSION_CHECK_UNKNOWN,
            "Latest build unknown",
            "latest build unknown",
            False,
            False,
        ),
        (
            server_versions.SERVER_VERSION_CHECK_UPTODATE,
            "Server is already up to date",
            "up to date",
            False,
            False,
        ),
        (
            server_versions.SERVER_VERSION_CHECK_AVAILABLE,
            "Update available",
            "update available",
            False,
            True,
        ),
        (
            server_versions.SERVER_VERSION_CHECK_FAILED,
            "Build check failed",
            "build check failed",
            False,
            False,
        ),
        (
            server_versions.SERVER_VERSION_CHECK_CHECKING,
            "Checking for updates",
            "checking",
            True,
            False,
        ),
        (
            server_versions.SERVER_VERSION_CHECK_UPDATING,
            "Update job running",
            "updating",
            True,
            False,
        ),
    ],
)
def test_updates_page_renders_build_states(
    tmp_path: Path,
    monkeypatch,
    check_state: str,
    message: str,
    state_label: str,
    check_disabled: bool,
    shows_update: bool,
):
    from armactl.web.app import create_app

    password = "owner updates password"
    setup_owner_user(tmp_path, "owner", password)
    _install_updates_page(monkeypatch, _updates_page(check_state))
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/updates", follow_redirects=False)

    assert response.status_code == 200
    assert "Server updates" in response.text
    assert "Build status" in response.text
    assert "Installed build" in response.text
    assert "Latest build" in response.text
    assert "Branch" in response.text
    assert "Last checked" in response.text
    if check_state == server_versions.SERVER_VERSION_CHECK_UNKNOWN:
        assert "never" in response.text
    else:
        assert 'data-local-time datetime="2026-06-21T00:00:00Z"' in response.text
        assert "2026-06-21 00:00 UTC" in response.text
        assert "2026-06-21T00:00:00+00:00" not in response.text
    assert "Check state" in response.text
    assert message in response.text
    assert state_label in response.text
    assert "action=\"/updates/check\"" in response.text
    check_form_match = re.search(
        r'<form method="post" action="/updates/check".*?</form>',
        response.text,
        re.DOTALL,
    )
    assert check_form_match is not None
    check_form = check_form_match.group(0)
    if check_disabled:
        assert "disabled aria-disabled=\"true\"" in check_form
    else:
        assert "disabled aria-disabled=\"true\"" not in check_form
    if shows_update:
        assert "action=\"/updates/update\"" in response.text
        assert "Update server" in response.text
    else:
        assert "action=\"/updates/update\"" not in response.text
    assert "game version" not in response.text.lower()


def test_failed_update_check_renders_retry_and_jobs_guidance(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app

    password = "owner updates password"
    setup_owner_user(tmp_path, "owner", password)
    page = _updates_page(server_versions.SERVER_VERSION_CHECK_FAILED)
    page["version"]["check_job_id"] = 17
    page["version"]["checkJobId"] = 17
    _install_updates_page(monkeypatch, page)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/updates", follow_redirects=False)

    assert response.status_code == 200
    assert "Build check failed" in response.text
    assert "probe failed" in response.text
    assert "Check again" in response.text
    assert "Failed update check job" in response.text
    assert 'href="/jobs#background-jobs"' in response.text
    assert "#17" in response.text
    assert "use the CLI fallback" in response.text
    assert 'action="/updates/update"' not in response.text


def test_stale_cached_check_result_renders_check_again_notice(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app

    password = "owner updates password"
    setup_owner_user(tmp_path, "owner", password)
    page = _updates_page(server_versions.SERVER_VERSION_CHECK_AVAILABLE)
    page["version"]["last_checked"] = "2000-01-01T00:00:00+00:00"
    page["version"]["lastChecked"] = "2000-01-01T00:00:00+00:00"
    _install_updates_page(monkeypatch, page)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/updates", follow_redirects=False)

    assert response.status_code == 200
    assert "Cached check result is stale" in response.text
    assert "Check again" in response.text
    assert "action=\"/updates/update\"" not in response.text


def test_failed_update_renders_retry_only_when_stopped_without_active_job(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app

    password = "owner updates password"
    setup_owner_user(tmp_path, "owner", password)
    page = _updates_page(server_versions.SERVER_VERSION_CHECK_AVAILABLE)
    page["version"]["failed_update_job"] = {
        "id": 23,
        "kind": "server:update",
        "status": "failed",
        "updated_at": "2026-06-21T00:00:00+00:00",
    }
    _install_updates_page(monkeypatch, page)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/updates", follow_redirects=False)

    assert response.status_code == 200
    assert "Last failed update job" in response.text
    assert "#23" in response.text
    assert 'href="/jobs#background-jobs"' in response.text
    assert "Retry update" in response.text
    assert 'action="/updates/update"' in response.text
    assert "server appears stopped and no update job is active" in response.text
    assert "use the CLI fallback" in response.text


def test_failed_update_does_not_render_retry_when_server_running(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app

    password = "owner updates password"
    setup_owner_user(tmp_path, "owner", password)
    page = _updates_page(
        server_versions.SERVER_VERSION_CHECK_AVAILABLE,
        running=True,
    )
    page["version"]["failed_update_job"] = {
        "id": 24,
        "kind": "server:update",
        "status": "failed",
        "updated_at": "2026-06-21T00:00:00+00:00",
    }
    _install_updates_page(monkeypatch, page)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/updates", follow_redirects=False)

    assert response.status_code == 200
    assert "Last failed update job" in response.text
    assert "Stop the game server before retrying" in response.text
    assert "Retry update" not in response.text
    assert 'action="/updates/update"' not in response.text


def test_active_update_check_renders_running_status_without_duplicate_actions(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app

    password = "owner updates password"
    setup_owner_user(tmp_path, "owner", password)
    page = _updates_page(server_versions.SERVER_VERSION_CHECK_CHECKING)
    page["version"]["active_job"] = {
        "id": 7,
        "kind": "server:update-check",
        "status": "running",
        "updated_at": "2030-01-01T00:00:00+00:00",
    }
    _install_updates_page(monkeypatch, page)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/updates", follow_redirects=False)

    assert response.status_code == 200
    assert "Active update check job" in response.text
    assert "Update check job already running." in response.text
    assert "already running" in response.text
    assert "disabled aria-disabled=\"true\"" in response.text
    assert "action=\"/updates/update\"" not in response.text


def test_active_update_job_renders_background_jobs_link(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app

    password = "owner updates password"
    setup_owner_user(tmp_path, "owner", password)
    _install_updates_page(
        monkeypatch,
        _updates_page(server_versions.SERVER_VERSION_CHECK_UPDATING),
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/updates", follow_redirects=False)

    assert response.status_code == 200
    assert "Active update job" in response.text
    assert "#9" in response.text
    assert 'href="/jobs#background-jobs"' in response.text


def test_stale_active_job_metadata_renders_guidance_without_cancelling(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.jobs import (
        SERVER_UPDATE_JOB_KIND,
        create_job,
        get_job,
        list_recent_jobs,
        mark_job_running,
    )
    from armactl.web.page_models import updates as updates_page_model
    from armactl.web.runtime import ensure_web_db

    password = "owner updates password"
    setup_owner_user(tmp_path, "owner", password)
    state = ServerState(
        server_installed=True,
        binary_exists=True,
        config_exists=True,
        server_running=False,
        install_dir=str(tmp_path / "default" / "server"),
    )
    save_state(state, tmp_path / "default" / "state.json")
    db_path = tmp_path / "web" / "web.db"
    ensure_web_db(db_path)
    queued = create_job(
        db_path,
        kind=SERVER_UPDATE_JOB_KIND,
        requested_by_username="owner",
    )
    running = mark_job_running(db_path, queued.id, current_step="Updating")
    stale_at = "2026-06-20T00:00:00+00:00"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE web_jobs
            SET created_at = ?, started_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (stale_at, stale_at, stale_at, running.id),
        )

    class FakeServiceAdapter:
        def get_service_status(self, service_name):
            del service_name
            return {"active_state": "inactive", "sub_state": "dead"}

    monkeypatch.setattr(
        updates_page_model,
        "get_service_adapter",
        lambda: FakeServiceAdapter(),
    )
    before = get_job(db_path, running.id)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/updates", follow_redirects=False)

    after = get_job(db_path, running.id)
    assert response.status_code == 200
    assert "This job may be stale" in response.text
    assert "use the CLI fallback" in response.text
    assert 'href="/jobs#background-jobs"' in response.text
    assert before is not None
    assert after is not None
    assert before.status == "running"
    assert after.status == "running"
    assert [job.id for job in list_recent_jobs(db_path)] == [running.id]


def test_expired_active_update_worker_lease_renders_diagnostics_only(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.jobs import (
        SERVER_UPDATE_JOB_KIND,
        create_job,
        get_job,
        list_recent_jobs,
        mark_job_running,
    )
    from armactl.web.page_models import updates as updates_page_model
    from armactl.web.runtime import ensure_web_db

    password = "owner updates password"
    setup_owner_user(tmp_path, "owner", password)
    state = ServerState(
        server_installed=True,
        binary_exists=True,
        config_exists=True,
        server_running=False,
        install_dir=str(tmp_path / "default" / "server"),
    )
    save_state(state, tmp_path / "default" / "state.json")
    db_path = tmp_path / "web" / "web.db"
    ensure_web_db(db_path)
    queued = create_job(
        db_path,
        kind=SERVER_UPDATE_JOB_KIND,
        requested_by_username="owner",
    )
    running = mark_job_running(
        db_path,
        queued.id,
        current_step="Updating",
        worker_id="worker1234",
    )
    expired_at = "2000-01-01T00:00:00+00:00"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE web_jobs
            SET worker_heartbeat_at = ?, worker_lease_expires_at = ?
            WHERE id = ?
            """,
            (expired_at, expired_at, running.id),
        )

    class FakeServiceAdapter:
        def get_service_status(self, service_name):
            del service_name
            return {"active_state": "inactive", "sub_state": "dead"}

    monkeypatch.setattr(
        updates_page_model,
        "get_service_adapter",
        lambda: FakeServiceAdapter(),
    )
    before = get_job(db_path, running.id)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/updates", follow_redirects=False)

    after = get_job(db_path, running.id)
    assert response.status_code == 200
    assert "Worker lease expired for this running job" in response.text
    assert "diagnostics only" in response.text
    assert "did not cancel, repair, or stop processes" in response.text
    assert "worker1234" not in response.text
    assert before is not None
    assert after is not None
    assert before.status == "running"
    assert after.status == "running"
    assert [job.id for job in list_recent_jobs(db_path)] == [running.id]


def test_updates_page_offers_safe_update_action_when_server_running(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app

    password = "owner updates password"
    setup_owner_user(tmp_path, "owner", password)
    _install_updates_page(
        monkeypatch,
        _updates_page(server_versions.SERVER_VERSION_CHECK_AVAILABLE, running=True),
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/updates", follow_redirects=False)

    assert response.status_code == 200
    assert "Update available" in response.text
    assert "Safe update will stop the running game server" in response.text
    assert "action=\"/updates/check\"" in response.text
    assert "action=\"/updates/update\"" in response.text
    assert "name=\"confirm\" value=\"running-update\"" not in response.text


def test_updates_get_does_not_run_discovery_steamcmd_or_mutate_jobs(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.jobs import (
        SERVER_UPDATE_JOB_KIND,
        create_job,
        get_job,
        mark_job_failed,
        mark_job_running,
    )

    def fail_backend(*args, **kwargs):
        raise AssertionError("updates GET must not run SteamCMD or discovery")

    password = "owner updates password"
    setup_owner_user(tmp_path, "owner", password)
    db_path = tmp_path / "web" / "web.db"
    queued = create_job(
        db_path,
        kind=SERVER_UPDATE_JOB_KIND,
        requested_by_username="owner",
    )
    running = mark_job_running(db_path, queued.id, current_step="Updating")
    failed = mark_job_failed(
        db_path,
        running.id,
        error_message="update failed",
        result_message="Server update failed.",
    )
    monkeypatch.setattr(server_versions.installer, "fetch_steam_app_info", fail_backend)
    monkeypatch.setattr(server_versions.discovery, "discover", fail_backend)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/updates", follow_redirects=False)

    after = get_job(db_path, failed.id)
    assert response.status_code == 200
    assert "Latest build unknown" in response.text
    assert after == failed


def test_unauthenticated_updates_redirects_to_login(tmp_path: Path):
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    response = client.get("/updates", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_updates_permission_denied_skips_page_model(
    tmp_path: Path,
    monkeypatch,
    set_web_owner_permissions,
):
    from armactl.web.app import create_app
    from armactl.web.page_models import updates as updates_page_model

    password = "owner updates password"
    setup_owner_user(tmp_path, "owner", password)
    set_web_owner_permissions(set())
    monkeypatch.setattr(
        updates_page_model,
        "load_updates_page",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("updates page model should not load")
        ),
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/updates", follow_redirects=False)

    assert response.status_code == 403
    assert response.text == "Permission denied."


def test_updates_check_rejects_invalid_csrf(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app
    from armactl.web.services import server_job_actions

    password = "owner updates password"
    setup_owner_user(tmp_path, "owner", password)
    monkeypatch.setattr(
        server_job_actions,
        "request_server_update_check_and_start",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("backend should not run before csrf")
        ),
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.post(
        "/updates/check",
        data={"csrf_token": "bad-token"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.text == "Invalid CSRF token."


def test_updates_check_permission_denied_before_csrf(
    tmp_path: Path,
    monkeypatch,
    set_web_owner_permissions,
):
    from armactl.web.app import create_app
    from armactl.web.services import server_job_actions

    password = "owner updates password"
    setup_owner_user(tmp_path, "owner", password)
    set_web_owner_permissions({DASHBOARD_VIEW})
    monkeypatch.setattr(
        server_job_actions,
        "request_server_update_check_and_start",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("backend should not run without permission")
        ),
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.post(
        "/updates/check",
        data={"csrf_token": "bad-token"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.text == "Permission denied."


def test_updates_check_queues_existing_background_job(
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
    password = "owner updates password"
    setup_owner_user(tmp_path, "owner", password)
    _install_updates_page(
        monkeypatch,
        _updates_page(server_versions.SERVER_VERSION_CHECK_UNKNOWN),
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    csrf_token = _updates_csrf_token(client)

    response = client.post(
        "/updates/check",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    jobs = [
        job
        for job in list_recent_jobs(tmp_path / "web" / "web.db")
        if job.kind == "server:update-check"
    ]

    assert response.status_code == 303
    assert response.headers["location"] == "/updates?notice=check-queued"
    assert len(jobs) == 1
    assert jobs[0].status == "queued"
    assert scheduled == [jobs[0].id]


def test_updates_check_reuses_fresh_cached_result_without_job(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.jobs import server as server_jobs
    from armactl.web.jobs.store import list_recent_jobs

    monkeypatch.setattr(
        server_jobs,
        "start_server_job_worker",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("fresh cache must not start worker")
        ),
    )
    password = "owner updates password"
    setup_owner_user(tmp_path, "owner", password)
    server_versions.save_server_version_check(
        tmp_path / "web" / "web.db",
        installed="100",
        latest="101",
        check_state=server_versions.SERVER_VERSION_CHECK_AVAILABLE,
    )
    _install_updates_page(
        monkeypatch,
        _updates_page(server_versions.SERVER_VERSION_CHECK_AVAILABLE),
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    csrf_token = _updates_csrf_token(client)

    response = client.post(
        "/updates/check",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    jobs = [
        job
        for job in list_recent_jobs(tmp_path / "web" / "web.db")
        if job.kind == "server:update-check"
    ]

    assert response.status_code == 303
    assert response.headers["location"] == "/updates?notice=check-reused"
    assert jobs == []


def test_updates_update_queues_existing_background_job(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.jobs import server as server_jobs
    from armactl.web.jobs.store import list_recent_jobs
    from armactl.web.services import server_job_actions

    scheduled: list[int] = []
    monkeypatch.setattr(
        server_job_actions.server_versions,
        "load_server_version_state",
        lambda **kwargs: _version_state(server_versions.SERVER_VERSION_CHECK_AVAILABLE),
    )
    monkeypatch.setattr(
        server_jobs,
        "start_server_job_worker",
        lambda db_path, job_id: scheduled.append(job_id),
    )
    password = "owner updates password"
    setup_owner_user(tmp_path, "owner", password)
    _install_updates_page(
        monkeypatch,
        _updates_page(server_versions.SERVER_VERSION_CHECK_AVAILABLE),
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    csrf_token = _updates_csrf_token(client)

    response = client.post(
        "/updates/update",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    jobs = [
        job
        for job in list_recent_jobs(tmp_path / "web" / "web.db")
        if job.kind == "server:update"
    ]

    assert response.status_code == 303
    assert response.headers["location"] == "/updates?notice=update-queued"
    assert len(jobs) == 1
    assert jobs[0].status == "queued"
    assert scheduled == [jobs[0].id]


def test_updates_update_queues_safe_workflow_for_running_server(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.jobs import server as server_jobs
    from armactl.web.jobs.store import list_recent_jobs
    from armactl.web.services import server_job_actions

    monkeypatch.setattr(
        server_job_actions.server_versions,
        "load_server_version_state",
        lambda **kwargs: _version_state(
            server_versions.SERVER_VERSION_CHECK_AVAILABLE,
            running=True,
        ),
    )
    monkeypatch.setattr(
        server_jobs,
        "start_server_job_worker",
        lambda db_path, job_id: None,
    )
    password = "owner updates password"
    setup_owner_user(tmp_path, "owner", password)
    _install_updates_page(
        monkeypatch,
        _updates_page(server_versions.SERVER_VERSION_CHECK_AVAILABLE, running=True),
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    csrf_token = _updates_csrf_token(client)

    response = client.post(
        "/updates/update",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    jobs = [
        job
        for job in list_recent_jobs(tmp_path / "web" / "web.db")
        if job.kind == "server:update"
    ]

    assert response.status_code == 303
    assert response.headers["location"] == "/updates?notice=update-queued"
    assert len(jobs) == 1


def test_updates_update_redirects_up_to_date_without_job(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.app import create_app
    from armactl.web.jobs.store import list_recent_jobs
    from armactl.web.services import server_job_actions

    monkeypatch.setattr(
        server_job_actions.server_versions,
        "load_server_version_state",
        lambda **kwargs: _version_state(server_versions.SERVER_VERSION_CHECK_UPTODATE),
    )
    password = "owner updates password"
    setup_owner_user(tmp_path, "owner", password)
    _install_updates_page(
        monkeypatch,
        _updates_page(server_versions.SERVER_VERSION_CHECK_UPTODATE),
    )
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    csrf_token = _updates_csrf_token(client)

    response = client.post(
        "/updates/update",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    jobs = [
        job
        for job in list_recent_jobs(tmp_path / "web" / "web.db")
        if job.kind == "server:update"
    ]

    assert response.status_code == 303
    assert response.headers["location"] == "/updates?notice=up-to-date"
    assert jobs == []


def test_load_updates_page_uses_live_service_status_when_state_is_stale(
    tmp_path: Path,
    monkeypatch,
):
    from types import SimpleNamespace

    from armactl.web.page_models import updates as updates_page_model
    from armactl.web.runtime import ensure_web_db

    state = ServerState(
        server_installed=True,
        binary_exists=True,
        config_exists=True,
        server_running=False,
        install_dir=str(tmp_path / "default" / "server"),
    )
    save_state(state, tmp_path / "default" / "state.json")
    db_path = tmp_path / "web" / "web.db"
    ensure_web_db(db_path)

    class FakeServiceAdapter:
        def get_service_status(self, service_name):
            del service_name
            return {"active_state": "active", "sub_state": "running"}

    monkeypatch.setattr(
        updates_page_model,
        "get_service_adapter",
        lambda: FakeServiceAdapter(),
    )

    page = updates_page_model.load_updates_page(
        "default",
        web_config=SimpleNamespace(data_root=tmp_path, db_path=db_path),
    )

    assert page["server_running"] is True
    assert page["version"]["check_state"] == server_versions.SERVER_VERSION_CHECK_UNKNOWN


def test_updates_page_model_exposes_active_profile_test_job(
    tmp_path: Path,
    monkeypatch,
):
    from types import SimpleNamespace

    from armactl.web.jobs import server as server_jobs
    from armactl.web.page_models import updates as updates_page_model
    from armactl.web.runtime import ensure_web_db
    from armactl.web.views.updates import build_updates_view

    db_path = tmp_path / "web" / "web.db"
    ensure_web_db(db_path)
    job, created = server_jobs.ensure_server_profile_job(
        db_path,
        action="test",
        name="serhiivka-modded",
        requested_by_username="owner",
    )
    assert created is True

    class FakeServiceAdapter:
        def get_service_status(self, service_name):
            del service_name
            return {"active_state": "inactive", "sub_state": "dead"}

    monkeypatch.setattr(
        updates_page_model,
        "get_service_adapter",
        lambda: FakeServiceAdapter(),
    )

    page = updates_page_model.load_updates_page(
        "default",
        web_config=SimpleNamespace(data_root=tmp_path, db_path=db_path),
    )
    view = build_updates_view(page, can_update_server=True)

    assert page["profile_job"]["id"] == job.id
    assert page["profile_job"]["action"] == "test"
    assert page["profile_job"]["profile_name"] == "serhiivka-modded"
    assert view["profile_actions_enabled"] is False
    assert view["profile_actions_disabled_reason"] == (
        "Wait for the active profile operation to finish."
    )


def test_updates_template_renders_profile_compatibility_and_test_action(tmp_path: Path):
    from types import SimpleNamespace

    from starlette.requests import Request

    from armactl.web.app import create_app
    from armactl.web.i18n import web_template_context
    from armactl.web.views.updates import build_updates_view

    raw = _updates_page(server_versions.SERVER_VERSION_CHECK_UPTODATE)
    raw.update(
        compatibility={
            "available": True,
            "active_mode": "vanilla",
            "parked_modded_available": True,
            "parked_profile_name": "serhiivka-modded",
            "parked_profile_compatibility": {
                "status": "outdated",
                "label": "Retest required",
                "css_class": "warning",
                "tested_at": "2026-08-13T10:00:00+00:00",
                "tested_build_id": "99",
            },
        },
        profiles=[
            {
                "name": "vanilla",
                "active": True,
                "mode": "vanilla",
                "scenario_id": "Everon.conf",
                "mod_count": 0,
                "compatibility": {
                    "status": "compatible",
                    "label": "Ready for current build",
                    "css_class": "success",
                    "tested_at": "2026-08-13T11:00:00+00:00",
                    "tested_build_id": "100",
                    "reason": "",
                },
            }
        ],
        policy={"automatic_vanilla_fallback": True},
    )
    page = build_updates_view(raw, can_update_server=True)
    app = create_app(data_root=tmp_path)
    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/updates",
            "raw_path": b"/updates",
            "query_string": b"",
            "root_path": "",
            "headers": [],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
            "app": app,
            "router": getattr(app, "router"),
        }
    )
    context = {
        "request": request,
        "current_user": SimpleNamespace(username="owner"),
        "csrf_token": "token",
        "page": page,
        **web_template_context(request),
    }

    html = app.state.templates.get_template("updates.html").render(context)

    assert 'action="/updates/profile/test"' in html
    assert "serhiivka-modded" in html
    assert "Retest required" in html
    assert "Ready for current build" in html
    assert 'action="/updates/retry-modded"' in html
    assert "disabled aria-disabled=\"true\"" in html


def test_running_server_keeps_profile_tests_available_with_maintenance_notice():
    from armactl.web.views.updates import build_updates_view

    page = build_updates_view(
        _updates_page(server_versions.SERVER_VERSION_CHECK_UPTODATE, running=True),
        can_update_server=True,
    )

    assert page["server_running"] is True
    assert page["profile_actions_enabled"] is True
    assert page["profile_actions_disabled_reason"] == ""
    assert "briefly stop the running game server" in page["profile_actions_notice"]
