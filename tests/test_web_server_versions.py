"""Tests for web server version/update state."""

from __future__ import annotations

from pathlib import Path

from armactl.state import ServerState
from armactl.web.jobs.server import SERVER_UPDATE_JOB_KIND
from armactl.web.jobs.store import create_job
from armactl.web.runtime import ensure_web_db
from armactl.web.services import server_versions


class FakeVersionAdapter:
    def __init__(self, *, installed=None, latest=None, branch="public", error=None):
        self.installed = installed
        self.latest = latest
        self.branch = branch
        self.error = error

    def probe(self, install_dir: Path, *, instance: str):
        if self.error is not None:
            raise self.error
        return server_versions.ServerVersionProbe(
            installed=self.installed,
            latest=self.latest,
            branch=self.branch,
        )


def _state(tmp_path: Path, *, running: bool = False) -> ServerState:
    install_dir = tmp_path / "server"
    install_dir.mkdir()
    return ServerState(
        server_installed=True,
        binary_exists=True,
        config_exists=True,
        server_running=running,
        install_dir=str(install_dir),
    )


def test_read_installed_build_from_steam_appmanifest(tmp_path: Path):
    install_dir = tmp_path / "server"
    manifest_dir = install_dir / "steamapps"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "appmanifest_1874900.acf").write_text(
        '"AppState"\n{\n    "appid" "1874900"\n    "buildid" "12345678"\n}\n',
        encoding="utf-8",
    )

    assert server_versions.read_installed_build_from_appmanifest(install_dir) == "12345678"


def test_server_version_state_up_to_date(tmp_path: Path):
    state = _state(tmp_path)

    version_state = server_versions.load_server_version_state(
        state=state,
        adapter=FakeVersionAdapter(installed="100", latest="100", branch="public"),
        last_checked="2026-06-21T00:00:00+00:00",
    )

    assert version_state.check_state == server_versions.SERVER_VERSION_CHECK_UPTODATE
    assert version_state.status == "up to date"
    assert version_state.message == "Server is already up to date"
    assert version_state.up_to_date is True
    assert version_state.can_update is False
    assert version_state.to_dict()["checkState"] == "uptodate"
    assert version_state.to_dict()["upToDate"] is True


def test_server_version_state_update_available(tmp_path: Path):
    state = _state(tmp_path, running=True)

    version_state = server_versions.load_server_version_state(
        state=state,
        adapter=FakeVersionAdapter(installed="100", latest="101", branch="experimental"),
    )

    assert version_state.check_state == server_versions.SERVER_VERSION_CHECK_AVAILABLE
    assert version_state.status == "update available"
    assert version_state.message == "Update available"
    assert version_state.installed == "100"
    assert version_state.latest == "101"
    assert version_state.branch == "experimental"
    assert version_state.can_update is True
    assert version_state.server_running is True


def test_server_version_state_unknown_when_latest_missing(tmp_path: Path):
    state = _state(tmp_path)

    version_state = server_versions.load_server_version_state(
        state=state,
        adapter=FakeVersionAdapter(installed="100", latest=None),
    )

    assert version_state.check_state == server_versions.SERVER_VERSION_CHECK_UNKNOWN
    assert version_state.status == "unknown"
    assert version_state.message == "Latest version unknown"
    assert version_state.installed == "100"
    assert version_state.latest == ""
    assert version_state.can_update is False


def test_server_version_state_failed_when_adapter_raises(tmp_path: Path):
    state = _state(tmp_path)

    version_state = server_versions.load_server_version_state(
        state=state,
        adapter=FakeVersionAdapter(error=RuntimeError("token=raw-secret failed")),
    )

    assert version_state.check_state == server_versions.SERVER_VERSION_CHECK_FAILED
    assert version_state.status == "check failed"
    assert version_state.message == "Version check failed"
    assert "raw-secret" not in version_state.failure_reason
    assert "token=***" in version_state.failure_reason
    assert version_state.can_update is False


def test_server_version_state_reports_active_update_job(tmp_path: Path):
    db_path = tmp_path / "web" / "web.db"
    ensure_web_db(db_path)
    job = create_job(db_path, kind=SERVER_UPDATE_JOB_KIND, requested_by_username="owner")

    version_state = server_versions.load_server_version_state(
        state=_state(tmp_path),
        db_path=db_path,
        adapter=FakeVersionAdapter(installed="100", latest="101"),
    )

    assert version_state.check_state == server_versions.SERVER_VERSION_CHECK_UPDATING
    assert version_state.message == "Update job running"
    assert version_state.update_job_id == job.id
    assert version_state.can_update is False
