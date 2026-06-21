"""Read-only server version/update state for the web panel."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from armactl import discovery, integrity, paths
from armactl.redaction import redact_sensitive_text
from armactl.state import ServerState
from armactl.web.jobs import store as job_store
from armactl.web.jobs.server import SERVER_UPDATE_JOB_KIND

SERVER_VERSION_CHECK_UPTODATE = "uptodate"
SERVER_VERSION_CHECK_AVAILABLE = "available"
SERVER_VERSION_CHECK_UNKNOWN = "unknown"
SERVER_VERSION_CHECK_FAILED = "failed"
SERVER_VERSION_CHECK_UPDATING = "updating"

SERVER_VERSION_STATUS_UP_TO_DATE = "up to date"
SERVER_VERSION_STATUS_AVAILABLE = "update available"
SERVER_VERSION_STATUS_UNKNOWN = "unknown"
SERVER_VERSION_STATUS_FAILED = "check failed"
SERVER_VERSION_STATUS_UPDATING = "updating"

SERVER_VERSION_MESSAGE_UP_TO_DATE = "Server is already up to date"
SERVER_VERSION_MESSAGE_AVAILABLE = "Update available"
SERVER_VERSION_MESSAGE_UNKNOWN = "Latest version unknown"
SERVER_VERSION_MESSAGE_FAILED = "Version check failed"
SERVER_VERSION_MESSAGE_UPDATING = "Update job running"

DEFAULT_SERVER_BRANCH = "public"
MAX_VERSION_TEXT_LENGTH = 80

_BUILD_ID_RE = re.compile(r'"buildid"\s+"(?P<value>[^"]+)"', re.IGNORECASE)


@dataclass(frozen=True)
class ServerVersionProbe:
    """Raw adapter result before it is shaped for web/dashboard use."""

    installed: str | None = None
    latest: str | None = None
    branch: str = DEFAULT_SERVER_BRANCH


class ServerVersionAdapter(Protocol):
    """Adapter contract for safe installed/latest build detection."""

    def probe(self, install_dir: Path, *, instance: str) -> ServerVersionProbe:
        """Return safe local/remote version metadata, if available."""


@dataclass(frozen=True)
class ServerVersionState:
    """Dashboard-safe server version/update state."""

    installed: str = ""
    latest: str = ""
    branch: str = DEFAULT_SERVER_BRANCH
    last_checked: str = ""
    check_state: str = SERVER_VERSION_CHECK_UNKNOWN
    status: str = SERVER_VERSION_STATUS_UNKNOWN
    message: str = SERVER_VERSION_MESSAGE_UNKNOWN
    up_to_date: bool = False
    can_update: bool = False
    failure_reason: str = ""
    update_job_id: int | None = None
    server_running: bool = False

    def to_dict(self) -> dict[str, object]:
        """Return both Pythonic and handoff-compatible field names."""
        return {
            "installed": self.installed,
            "latest": self.latest,
            "branch": self.branch,
            "last_checked": self.last_checked,
            "lastChecked": self.last_checked,
            "check_state": self.check_state,
            "checkState": self.check_state,
            "status": self.status,
            "message": self.message,
            "up_to_date": self.up_to_date,
            "upToDate": self.up_to_date,
            "can_update": self.can_update,
            "canUpdate": self.can_update,
            "failure_reason": self.failure_reason,
            "failureReason": self.failure_reason,
            "update_job_id": self.update_job_id,
            "updateJobId": self.update_job_id,
            "server_running": self.server_running,
            "serverRunning": self.server_running,
        }


class LocalSteamAppManifestVersionAdapter:
    """Read installed build metadata from Steam's local appmanifest.

    Steam's local appmanifest can identify the installed build. It does not, by
    itself, prove the latest available build, so the default adapter leaves
    latest unknown instead of shelling out or doing network work from dashboard
    rendering.
    """

    def probe(self, install_dir: Path, *, instance: str) -> ServerVersionProbe:
        del instance
        return ServerVersionProbe(
            installed=read_installed_build_from_appmanifest(install_dir),
            latest=None,
            branch=DEFAULT_SERVER_BRANCH,
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_version_text(value: object | None) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    text = "".join(char for char in text if ord(char) >= 32 and char not in {'"', "'"})
    return text[:MAX_VERSION_TEXT_LENGTH].strip()


def _state(
    *,
    installed: object | None,
    latest: object | None,
    branch: object | None = DEFAULT_SERVER_BRANCH,
    last_checked: str | None = None,
    check_state: str,
    status: str,
    message: str,
    up_to_date: bool = False,
    can_update: bool = False,
    failure_reason: object | None = "",
    update_job_id: int | None = None,
    server_running: bool = False,
) -> ServerVersionState:
    return ServerVersionState(
        installed=_safe_version_text(installed),
        latest=_safe_version_text(latest),
        branch=_safe_version_text(branch) or DEFAULT_SERVER_BRANCH,
        last_checked=last_checked or _utc_now(),
        check_state=check_state,
        status=status,
        message=message,
        up_to_date=up_to_date,
        can_update=can_update,
        failure_reason=_safe_version_text(redact_sensitive_text(failure_reason)),
        update_job_id=update_job_id,
        server_running=server_running,
    )


def unknown_server_version_state(
    *,
    installed: object | None = "",
    latest: object | None = "",
    branch: object | None = DEFAULT_SERVER_BRANCH,
    last_checked: str | None = None,
    server_running: bool = False,
) -> ServerVersionState:
    """Return a controlled fail-closed unknown version state."""
    return _state(
        installed=installed,
        latest=latest,
        branch=branch,
        last_checked=last_checked,
        check_state=SERVER_VERSION_CHECK_UNKNOWN,
        status=SERVER_VERSION_STATUS_UNKNOWN,
        message=SERVER_VERSION_MESSAGE_UNKNOWN,
        server_running=server_running,
    )


def failed_server_version_state(
    *,
    failure_reason: object | None = "",
    last_checked: str | None = None,
    server_running: bool = False,
) -> ServerVersionState:
    """Return a controlled check-failed version state."""
    return _state(
        installed="",
        latest="",
        last_checked=last_checked,
        check_state=SERVER_VERSION_CHECK_FAILED,
        status=SERVER_VERSION_STATUS_FAILED,
        message=SERVER_VERSION_MESSAGE_FAILED,
        failure_reason=failure_reason,
        server_running=server_running,
    )


def read_installed_build_from_appmanifest(install_dir: Path) -> str | None:
    """Return the installed Steam build ID from the local appmanifest, if present."""
    manifest_path = integrity.steam_appmanifest_path(Path(install_dir))
    if not manifest_path.is_file():
        return None

    content = manifest_path.read_text(encoding="utf-8", errors="ignore")
    match = _BUILD_ID_RE.search(content)
    if match is None:
        return None
    return _safe_version_text(match.group("value")) or None


def _active_update_job_id(db_path: Path | None, *, instance: str) -> int | None:
    if db_path is None:
        return None
    try:
        active = job_store.get_active_job(
            db_path,
            kind=SERVER_UPDATE_JOB_KIND,
            instance=instance,
        )
    except Exception:
        return None
    return active.id if active is not None else None


def load_server_version_state(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    state: ServerState | None = None,
    adapter: ServerVersionAdapter | None = None,
    db_path: Path | None = None,
    last_checked: str | None = None,
) -> ServerVersionState:
    """Load a safe read-only server update state for web rendering/workflows."""
    try:
        server_state = state or discovery.discover(instance=instance, save=False)
    except Exception as exc:  # noqa: BLE001 - version checks must fail closed.
        return failed_server_version_state(
            failure_reason=exc,
            last_checked=last_checked,
        )
    server_running = bool(server_state.server_running)
    active_update_job_id = _active_update_job_id(db_path, instance=instance)
    if active_update_job_id is not None:
        return _state(
            installed="",
            latest="",
            last_checked=last_checked,
            check_state=SERVER_VERSION_CHECK_UPDATING,
            status=SERVER_VERSION_STATUS_UPDATING,
            message=SERVER_VERSION_MESSAGE_UPDATING,
            update_job_id=active_update_job_id,
            server_running=server_running,
        )

    install_dir = Path(server_state.install_dir or paths.server_dir(instance))
    if not server_state.server_installed and not install_dir.exists():
        return unknown_server_version_state(
            last_checked=last_checked,
            server_running=server_running,
        )

    probe_adapter = adapter or LocalSteamAppManifestVersionAdapter()
    try:
        probe = probe_adapter.probe(install_dir, instance=instance)
    except Exception as exc:  # noqa: BLE001 - dashboard must fail closed.
        return failed_server_version_state(
            failure_reason=exc,
            last_checked=last_checked,
            server_running=server_running,
        )

    installed = _safe_version_text(probe.installed)
    latest = _safe_version_text(probe.latest)
    branch = _safe_version_text(probe.branch) or DEFAULT_SERVER_BRANCH

    if not installed or not latest:
        return unknown_server_version_state(
            installed=installed,
            latest=latest,
            branch=branch,
            last_checked=last_checked,
            server_running=server_running,
        )
    if installed == latest:
        return _state(
            installed=installed,
            latest=latest,
            branch=branch,
            last_checked=last_checked,
            check_state=SERVER_VERSION_CHECK_UPTODATE,
            status=SERVER_VERSION_STATUS_UP_TO_DATE,
            message=SERVER_VERSION_MESSAGE_UP_TO_DATE,
            up_to_date=True,
            server_running=server_running,
        )
    return _state(
        installed=installed,
        latest=latest,
        branch=branch,
        last_checked=last_checked,
        check_state=SERVER_VERSION_CHECK_AVAILABLE,
        status=SERVER_VERSION_STATUS_AVAILABLE,
        message=SERVER_VERSION_MESSAGE_AVAILABLE,
        can_update=True,
        server_running=server_running,
    )
