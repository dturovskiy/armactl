from __future__ import annotations

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
    assert "Check state" in response.text
    assert message in response.text
    assert state_label in response.text
    assert "action=\"/updates/check\"" in response.text
    if check_disabled:
        assert "disabled aria-disabled=\"true\"" in response.text
    else:
        assert "disabled aria-disabled=\"true\"" not in response.text
    if shows_update:
        assert "action=\"/updates/update\"" in response.text
        assert "Update server" in response.text
    else:
        assert "action=\"/updates/update\"" not in response.text
    assert "game version" not in response.text.lower()


def test_updates_page_blocks_update_action_when_server_running(
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
    assert "Stop the game server before updating." in response.text
    assert "action=\"/updates/check\"" in response.text
    assert "action=\"/updates/update\"" not in response.text
    assert "name=\"confirm\" value=\"running-update\"" not in response.text


def test_updates_get_does_not_run_discovery_or_steamcmd(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app

    def fail_backend(*args, **kwargs):
        raise AssertionError("updates GET must not run SteamCMD or discovery")

    password = "owner updates password"
    setup_owner_user(tmp_path, "owner", password)
    monkeypatch.setattr(server_versions.installer, "fetch_steam_app_info", fail_backend)
    monkeypatch.setattr(server_versions.discovery, "discover", fail_backend)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/updates", follow_redirects=False)

    assert response.status_code == 200
    assert "Latest build unknown" in response.text


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
    assert response.headers["location"] == "/updates"
    assert len(jobs) == 1
    assert jobs[0].status == "queued"
    assert scheduled == [jobs[0].id]


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
    assert response.headers["location"] == "/updates"
    assert len(jobs) == 1
    assert jobs[0].status == "queued"
    assert scheduled == [jobs[0].id]


def test_updates_update_blocks_running_server_without_job(
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
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("running update must not start worker")
        ),
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

    assert response.status_code == 400
    assert response.text == "Stop the game server before updating."
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
