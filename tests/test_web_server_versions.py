"""Tests for web server version/update state."""

from __future__ import annotations

from pathlib import Path

from armactl.state import ServerState
from armactl.web.jobs.server import SERVER_UPDATE_CHECK_JOB_KIND, SERVER_UPDATE_JOB_KIND
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


def _write_appmanifest(install_dir: Path, build_id: str) -> None:
    manifest_dir = install_dir / "steamapps"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "appmanifest_1874900.acf").write_text(
        '"AppState"\n{\n    "appid" "1874900"\n'
        f'    "buildid" "{build_id}"\n}}\n',
        encoding="utf-8",
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


def test_read_latest_build_from_steam_app_info_public_branch():
    output = '''"1874900"
{
    "depots"
    {
        "branches"
        {
            "public" { "buildid" "999999" }
        }
    }
}
'''

    assert server_versions.read_latest_build_from_steam_app_info(output) == "999999"


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


def test_server_version_state_does_not_offer_downgrade_when_installed_is_newer(
    tmp_path: Path,
):
    state = _state(tmp_path, running=True)

    version_state = server_versions.load_server_version_state(
        state=state,
        adapter=FakeVersionAdapter(installed="24501482", latest="23728491"),
    )

    assert version_state.check_state == server_versions.SERVER_VERSION_CHECK_UPTODATE
    assert version_state.message == "Installed build is newer than reported latest"
    assert version_state.up_to_date is True
    assert version_state.can_update is False


def test_server_version_state_installed_only_without_cache_is_unknown(tmp_path: Path):
    state = _state(tmp_path)
    install_dir = Path(state.install_dir or "")
    _write_appmanifest(install_dir, "100")
    db_path = tmp_path / "web" / "web.db"
    ensure_web_db(db_path)

    version_state = server_versions.load_server_version_state(
        state=state,
        db_path=db_path,
    )

    assert version_state.check_state == server_versions.SERVER_VERSION_CHECK_UNKNOWN
    assert version_state.installed == "100"
    assert version_state.latest == ""
    assert version_state.last_checked == ""
    assert version_state.can_update is False


def test_server_version_state_unknown_when_latest_missing(tmp_path: Path):
    state = _state(tmp_path)

    version_state = server_versions.load_server_version_state(
        state=state,
        adapter=FakeVersionAdapter(installed="100", latest=None),
    )

    assert version_state.check_state == server_versions.SERVER_VERSION_CHECK_UNKNOWN
    assert version_state.status == "unknown"
    assert version_state.message == "Latest build unknown"
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
    assert version_state.message == "Build check failed"
    assert "raw-secret" not in version_state.failure_reason
    assert "token=***" in version_state.failure_reason
    assert version_state.can_update is False


def test_latest_check_success_caches_up_to_date_state(tmp_path: Path):
    state = _state(tmp_path)
    install_dir = Path(state.install_dir or "")
    _write_appmanifest(install_dir, "100")
    db_path = tmp_path / "web" / "web.db"
    ensure_web_db(db_path)

    checked = server_versions.refresh_latest_server_version_state(
        db_path,
        state=state,
        adapter=FakeVersionAdapter(installed="100", latest="100", branch="public"),
        job_id=42,
    )
    loaded = server_versions.load_server_version_state(state=state, db_path=db_path)
    cached = server_versions.load_cached_server_version_check(db_path)

    assert checked.check_state == server_versions.SERVER_VERSION_CHECK_UPTODATE
    assert loaded.check_state == server_versions.SERVER_VERSION_CHECK_UPTODATE
    assert loaded.message == "Server is already up to date"
    assert loaded.installed == "100"
    assert loaded.latest == "100"
    assert loaded.last_checked == checked.last_checked
    assert cached is not None
    assert cached.job_id == 42


def test_latest_check_success_caches_update_available_state(tmp_path: Path):
    state = _state(tmp_path)
    install_dir = Path(state.install_dir or "")
    _write_appmanifest(install_dir, "100")
    db_path = tmp_path / "web" / "web.db"
    ensure_web_db(db_path)

    checked = server_versions.refresh_latest_server_version_state(
        db_path,
        state=state,
        adapter=FakeVersionAdapter(installed="100", latest="101", branch="public"),
    )
    loaded = server_versions.load_server_version_state(state=state, db_path=db_path)

    assert checked.check_state == server_versions.SERVER_VERSION_CHECK_AVAILABLE
    assert loaded.check_state == server_versions.SERVER_VERSION_CHECK_AVAILABLE
    assert loaded.installed == "100"
    assert loaded.latest == "101"
    assert loaded.can_update is True


def test_latest_check_failure_caches_controlled_failed_state(tmp_path: Path):
    state = _state(tmp_path)
    install_dir = Path(state.install_dir or "")
    _write_appmanifest(install_dir, "100")
    db_path = tmp_path / "web" / "web.db"
    ensure_web_db(db_path)

    checked = server_versions.refresh_latest_server_version_state(
        db_path,
        state=state,
        adapter=FakeVersionAdapter(error=RuntimeError("token=raw-secret failed")),
    )
    loaded = server_versions.load_server_version_state(state=state, db_path=db_path)

    assert checked.check_state == server_versions.SERVER_VERSION_CHECK_FAILED
    assert loaded.check_state == server_versions.SERVER_VERSION_CHECK_FAILED
    assert loaded.installed == "100"
    assert loaded.latest == ""
    assert "raw-secret" not in loaded.failure_reason
    assert "token=***" in loaded.failure_reason
    assert loaded.can_update is False


def test_latest_check_cache_overwrites_idempotently(tmp_path: Path):
    state = _state(tmp_path)
    install_dir = Path(state.install_dir or "")
    _write_appmanifest(install_dir, "100")
    db_path = tmp_path / "web" / "web.db"
    ensure_web_db(db_path)

    server_versions.refresh_latest_server_version_state(
        db_path,
        state=state,
        adapter=FakeVersionAdapter(installed="100", latest="101"),
    )
    server_versions.refresh_latest_server_version_state(
        db_path,
        state=state,
        adapter=FakeVersionAdapter(installed="100", latest="100"),
    )

    loaded = server_versions.load_server_version_state(state=state, db_path=db_path)

    assert loaded.check_state == server_versions.SERVER_VERSION_CHECK_UPTODATE
    assert loaded.latest == "100"


def test_cached_check_freshness(tmp_path: Path):
    from datetime import datetime, timedelta, timezone

    db_path = tmp_path / "web" / "web.db"
    fresh = server_versions.save_server_version_check(
        db_path,
        installed="100",
        latest="101",
        check_state=server_versions.SERVER_VERSION_CHECK_AVAILABLE,
        checked_at=datetime.now(timezone.utc).isoformat(),
    )
    recent = server_versions.save_server_version_check(
        db_path,
        installed="100",
        latest="101",
        check_state=server_versions.SERVER_VERSION_CHECK_AVAILABLE,
        checked_at=(datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(),
    )
    stale = server_versions.save_server_version_check(
        db_path,
        installed="100",
        latest="101",
        check_state=server_versions.SERVER_VERSION_CHECK_AVAILABLE,
        checked_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
    )
    failed = server_versions.save_server_version_check(
        db_path,
        installed="100",
        latest="",
        check_state=server_versions.SERVER_VERSION_CHECK_FAILED,
        checked_at=datetime.now(timezone.utc).isoformat(),
    )

    assert server_versions.cached_check_has_fresh_result(fresh)
    assert server_versions.cached_check_has_fresh_result(recent)
    assert not server_versions.cached_check_has_fresh_result(
        recent,
        max_age_seconds=server_versions.SERVER_VERSION_UPDATE_SAFETY_TTL_SECONDS,
    )
    assert not server_versions.cached_check_has_fresh_result(stale)
    assert not server_versions.cached_check_has_fresh_result(failed)


def test_stale_cached_check_is_explicitly_expired_and_cannot_update(tmp_path: Path):
    from datetime import datetime, timedelta, timezone

    state = _state(tmp_path)
    install_dir = Path(state.install_dir or "")
    _write_appmanifest(install_dir, "100")
    db_path = tmp_path / "web" / "web.db"
    server_versions.save_server_version_check(
        db_path,
        installed="100",
        latest="101",
        check_state=server_versions.SERVER_VERSION_CHECK_AVAILABLE,
        checked_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
    )

    version_state = server_versions.load_server_version_state(
        state=state,
        db_path=db_path,
    )

    assert version_state.check_state == server_versions.SERVER_VERSION_CHECK_STALE
    assert version_state.message == "Build check expired"
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


def test_server_version_state_reports_active_update_check_job(tmp_path: Path):
    db_path = tmp_path / "web" / "web.db"
    ensure_web_db(db_path)
    job = create_job(db_path, kind=SERVER_UPDATE_CHECK_JOB_KIND, requested_by_username="owner")

    version_state = server_versions.load_server_version_state(
        state=_state(tmp_path),
        db_path=db_path,
        adapter=FakeVersionAdapter(installed="100", latest="101"),
    )

    assert version_state.check_state == server_versions.SERVER_VERSION_CHECK_CHECKING
    assert version_state.message == "Checking for updates"
    assert version_state.check_job_id == job.id
    assert version_state.update_job_id is None
    assert version_state.can_update is False
