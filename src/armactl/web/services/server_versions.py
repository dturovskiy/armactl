"""Read-only server version/update state for the web panel."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from armactl import discovery, installer, integrity, paths
from armactl.redaction import redact_sensitive_text
from armactl.state import ServerState
from armactl.web.jobs import store as job_store
from armactl.web.jobs.server import SERVER_UPDATE_CHECK_JOB_KIND, SERVER_UPDATE_JOB_KIND
from armactl.web.runtime.db import ensure_web_db

SERVER_VERSION_CHECK_UPTODATE = "uptodate"
SERVER_VERSION_CHECK_AVAILABLE = "available"
SERVER_VERSION_CHECK_UNKNOWN = "unknown"
SERVER_VERSION_CHECK_CHECKING = "checking"
SERVER_VERSION_CHECK_FAILED = "failed"
SERVER_VERSION_CHECK_UPDATING = "updating"

SERVER_VERSION_STATUS_UP_TO_DATE = "up to date"
SERVER_VERSION_STATUS_AVAILABLE = "update available"
SERVER_VERSION_STATUS_UNKNOWN = "unknown"
SERVER_VERSION_STATUS_CHECKING = "checking"
SERVER_VERSION_STATUS_FAILED = "check failed"
SERVER_VERSION_STATUS_UPDATING = "updating"

SERVER_VERSION_MESSAGE_UP_TO_DATE = "Server is already up to date"
SERVER_VERSION_MESSAGE_AVAILABLE = "Update available"
SERVER_VERSION_MESSAGE_UNKNOWN = "Latest build unknown"
SERVER_VERSION_MESSAGE_CHECKING = "Checking for updates"
SERVER_VERSION_MESSAGE_FAILED = "Build check failed"
SERVER_VERSION_MESSAGE_CHECK_QUEUED = "Update check queued."
SERVER_VERSION_MESSAGE_CHECK_COMPLETED = "Update check completed."
SERVER_VERSION_MESSAGE_UPDATING = "Update job running"

STEAMCMD_APP_INFO_SOURCE = "steamcmd-app-info"
DEFAULT_SERVER_BRANCH = "public"
MAX_FAILURE_REASON_LENGTH = 240
MAX_SOURCE_TEXT_LENGTH = 80
MAX_STEAM_APP_INFO_PARSE_CHARS = 2_000_000
MAX_STEAM_APP_INFO_TOKENS = 80_000

MAX_VERSION_TEXT_LENGTH = 80
SERVER_VERSION_CHECK_CACHE_TTL_SECONDS = 600


def service_status_blocks_update(status: Mapping[str, object] | None) -> bool:
    """Return whether live service status should block server update."""
    if not status:
        return False
    if bool(status.get("active")):
        return True
    active_state = str(
        status.get("active_state") or status.get("ActiveState") or ""
    ).strip().lower()
    sub_state = str(
        status.get("sub_state") or status.get("SubState") or ""
    ).strip().lower()
    return active_state in {"active", "activating"} or sub_state in {
        "running",
        "start",
        "auto-restart",
    }

_STEAM_KV_TOKEN_RE = re.compile('"((?:\\\\.|[^"\\\\])*)"|([{}])')
_CACHE_CHECK_STATES = frozenset(
    {
        SERVER_VERSION_CHECK_UPTODATE,
        SERVER_VERSION_CHECK_AVAILABLE,
        SERVER_VERSION_CHECK_FAILED,
    }
)
_BUILD_ID_RE = re.compile(r'"buildid"\s+"(?P<value>[^"]+)"', re.IGNORECASE)


@dataclass(frozen=True)
class ServerVersionProbe:
    """Raw adapter result before it is shaped for web/dashboard use."""

    installed: str | None = None
    latest: str | None = None
    branch: str = DEFAULT_SERVER_BRANCH
    source: str = ""



@dataclass(frozen=True)
class CachedServerVersionCheck:
    """Persisted latest-build check metadata safe for dashboard reads."""

    instance: str
    installed: str
    latest: str
    branch: str
    check_state: str
    failure_reason: str
    checked_at: str
    source: str
    job_id: int | None = None

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
    check_job_id: int | None = None
    source: str = ""
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
            "check_job_id": self.check_job_id,
            "checkJobId": self.check_job_id,
            "source": self.source,
            "latest_source": self.source,
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


class ServerVersionCheckError(RuntimeError):
    """Raised when a latest-build check cannot produce safe metadata."""


def _steam_kv_unescape(value: str) -> str:
    return value.replace(r'\"', '"').replace(r"\\", "\\")


def _steam_kv_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for match in _STEAM_KV_TOKEN_RE.finditer(text[:MAX_STEAM_APP_INFO_PARSE_CHARS]):
        quoted, brace = match.groups()
        tokens.append(_steam_kv_unescape(quoted) if quoted is not None else str(brace))
        if len(tokens) > MAX_STEAM_APP_INFO_TOKENS:
            raise ServerVersionCheckError("Steam app info output is too large to parse safely.")
    return tokens


def _parse_steam_kv_object(tokens: list[str], index: int = 0) -> tuple[dict[str, object], int]:
    parsed: dict[str, object] = {}
    while index < len(tokens):
        key = tokens[index]
        index += 1
        if key == "}":
            return parsed, index
        if key == "{":
            continue
        if index >= len(tokens):
            break

        value = tokens[index]
        index += 1
        if value == "{":
            child, index = _parse_steam_kv_object(tokens, index)
            parsed[key] = child
        elif value == "}":
            parsed[key] = ""
            return parsed, index
        else:
            parsed[key] = value
    return parsed, index


def _child_mapping(value: object, key: str) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    child = value.get(key)
    return child if isinstance(child, dict) else None


def _case_child_mapping(value: object, key: str) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    child = value.get(key)
    if isinstance(child, dict):
        return child
    normalized = key.casefold()
    for candidate_key, candidate_value in value.items():
        if str(candidate_key).casefold() == normalized and isinstance(candidate_value, dict):
            return candidate_value
    return None


def read_latest_build_from_steam_app_info(
    app_info_output: str,
    *,
    branch: str = DEFAULT_SERVER_BRANCH,
) -> str | None:
    """Return a branch build ID from SteamCMD app_info_print output."""
    tokens = _steam_kv_tokens(app_info_output)
    parsed, _index = _parse_steam_kv_object(tokens)
    app_data = _child_mapping(parsed, integrity.APP_ID) or parsed
    depots = _child_mapping(app_data, "depots")
    branches = _child_mapping(depots, "branches")
    branch_data = _case_child_mapping(branches, branch)
    if branch_data is None:
        return None
    return _safe_version_text(branch_data.get("buildid")) or None


class SteamCmdAppInfoVersionAdapter:
    """Fetch latest build metadata through SteamCMD app_info_print."""

    source = STEAMCMD_APP_INFO_SOURCE

    def __init__(self, *, branch: str = DEFAULT_SERVER_BRANCH) -> None:
        self.branch = _safe_version_text(branch) or DEFAULT_SERVER_BRANCH

    def probe(self, install_dir: Path, *, instance: str) -> ServerVersionProbe:
        del instance
        output = installer.fetch_steam_app_info(integrity.APP_ID)
        latest = read_latest_build_from_steam_app_info(output, branch=self.branch)
        if not latest:
            raise ServerVersionCheckError("Latest build was not present in Steam app info.")
        return ServerVersionProbe(
            installed=read_installed_build_from_appmanifest(install_dir),
            latest=latest,
            branch=self.branch,
            source=self.source,
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


def _safe_failure_text(value: object | None) -> str:
    text = redact_sensitive_text(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = " ".join(part.strip() for part in text.splitlines() if part.strip())
    text = "".join(char for char in text if ord(char) >= 32)
    return text[:MAX_FAILURE_REASON_LENGTH].strip()


def _safe_source_text(value: object | None) -> str:
    text = _safe_version_text(value)
    return text[:MAX_SOURCE_TEXT_LENGTH].strip()


def _safe_check_state(value: object | None) -> str:
    text = _safe_version_text(value).lower()
    if text in _CACHE_CHECK_STATES:
        return text
    return SERVER_VERSION_CHECK_UNKNOWN


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
    check_job_id: int | None = None,
    update_job_id: int | None = None,
    server_running: bool = False,
    source: object | None = "",
) -> ServerVersionState:
    return ServerVersionState(
        installed=_safe_version_text(installed),
        latest=_safe_version_text(latest),
        branch=_safe_version_text(branch) or DEFAULT_SERVER_BRANCH,
        last_checked=last_checked or "",
        check_state=check_state,
        status=status,
        message=message,
        up_to_date=up_to_date,
        can_update=can_update,
        failure_reason=_safe_failure_text(failure_reason),
        check_job_id=check_job_id,
        update_job_id=update_job_id,
        server_running=server_running,
        source=_safe_source_text(source),
    )


def unknown_server_version_state(
    *,
    installed: object | None = "",
    latest: object | None = "",
    branch: object | None = DEFAULT_SERVER_BRANCH,
    last_checked: str | None = None,
    server_running: bool = False,
    check_job_id: int | None = None,
    source: object | None = "",
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
        check_job_id=check_job_id,
        source=source,
    )


def failed_server_version_state(
    *,
    installed: object | None = "",
    latest: object | None = "",
    branch: object | None = DEFAULT_SERVER_BRANCH,
    failure_reason: object | None = "",
    last_checked: str | None = None,
    server_running: bool = False,
    check_job_id: int | None = None,
    source: object | None = "",
) -> ServerVersionState:
    """Return a controlled check-failed version state."""
    return _state(
        installed=installed,
        latest=latest,
        branch=branch,
        last_checked=last_checked,
        check_state=SERVER_VERSION_CHECK_FAILED,
        status=SERVER_VERSION_STATUS_FAILED,
        message=SERVER_VERSION_MESSAGE_FAILED,
        failure_reason=failure_reason,
        server_running=server_running,
        check_job_id=check_job_id,
        source=source,
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


def _connect_cache(db_path: Path) -> sqlite3.Connection:
    ensure_web_db(db_path)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def _cached_check_from_row(row: sqlite3.Row) -> CachedServerVersionCheck:
    job_id = row["job_id"]
    return CachedServerVersionCheck(
        instance=str(row["instance"]),
        installed=_safe_version_text(row["installed"]),
        latest=_safe_version_text(row["latest"]),
        branch=_safe_version_text(row["branch"]) or DEFAULT_SERVER_BRANCH,
        check_state=_safe_check_state(row["check_state"]),
        failure_reason=_safe_failure_text(row["failure_reason"]),
        checked_at=str(row["checked_at"]),
        source=_safe_source_text(row["source"]),
        job_id=int(job_id) if isinstance(job_id, int) else None,
    )


def load_cached_server_version_check(
    db_path: Path,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> CachedServerVersionCheck | None:
    """Return the last safe latest-build check cached in web.db."""
    normalized_instance = paths.validate_instance_name(instance)
    with _connect_cache(db_path) as connection:
        row = connection.execute(
            """
            SELECT instance,
                   installed,
                   latest,
                   branch,
                   check_state,
                   failure_reason,
                   checked_at,
                   source,
                   job_id
            FROM web_server_version_checks
            WHERE instance = ?
            """,
            (normalized_instance,),
        ).fetchone()
    if row is None:
        return None
    return _cached_check_from_row(row)


def cached_check_has_fresh_result(
    cached: CachedServerVersionCheck | None,
    *,
    max_age_seconds: int = SERVER_VERSION_CHECK_CACHE_TTL_SECONDS,
    now: datetime | None = None,
) -> bool:
    """Return whether cached latest-build data is fresh enough to avoid a new check."""
    if cached is None or cached.check_state not in {
        SERVER_VERSION_CHECK_UPTODATE,
        SERVER_VERSION_CHECK_AVAILABLE,
    }:
        return False
    try:
        checked_at = datetime.fromisoformat(cached.checked_at)
    except ValueError:
        return False
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    age = current.astimezone(timezone.utc) - checked_at.astimezone(timezone.utc)
    return timedelta(0) <= age <= timedelta(seconds=max_age_seconds)


def save_server_version_check(
    db_path: Path,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    installed: object | None = "",
    latest: object | None = "",
    branch: object | None = DEFAULT_SERVER_BRANCH,
    check_state: object | None = SERVER_VERSION_CHECK_UNKNOWN,
    failure_reason: object | None = "",
    checked_at: str | None = None,
    source: object | None = "",
    job_id: int | None = None,
) -> CachedServerVersionCheck:
    """Persist one bounded latest-build check result for read-only dashboard use."""
    normalized_instance = paths.validate_instance_name(instance)
    checked = checked_at or _utc_now()
    safe_values = {
        "installed": _safe_version_text(installed),
        "latest": _safe_version_text(latest),
        "branch": _safe_version_text(branch) or DEFAULT_SERVER_BRANCH,
        "check_state": _safe_check_state(check_state),
        "failure_reason": _safe_failure_text(failure_reason),
        "source": _safe_source_text(source),
    }
    with _connect_cache(db_path) as connection:
        connection.execute(
            """
            INSERT INTO web_server_version_checks(
                instance, installed, latest, branch, check_state, failure_reason,
                checked_at, updated_at, source, job_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(instance) DO UPDATE SET
                installed = excluded.installed,
                latest = excluded.latest,
                branch = excluded.branch,
                check_state = excluded.check_state,
                failure_reason = excluded.failure_reason,
                checked_at = excluded.checked_at,
                updated_at = excluded.updated_at,
                source = excluded.source,
                job_id = excluded.job_id
            """,
            (
                normalized_instance,
                safe_values["installed"],
                safe_values["latest"],
                safe_values["branch"],
                safe_values["check_state"],
                safe_values["failure_reason"],
                checked,
                checked,
                safe_values["source"],
                job_id,
            ),
        )
    cached = load_cached_server_version_check(db_path, instance=normalized_instance)
    if cached is None:
        raise ServerVersionCheckError("Latest-build check cache write failed.")
    return cached


def _active_job_id(db_path: Path | None, *, kind: str, instance: str) -> int | None:
    if db_path is None:
        return None
    try:
        active = job_store.get_active_job(
            db_path,
            kind=kind,
            instance=instance,
        )
    except Exception:
        return None
    return active.id if active is not None else None


def _version_state_from_values(
    *,
    installed: object | None,
    latest: object | None,
    branch: object | None = DEFAULT_SERVER_BRANCH,
    last_checked: str | None = None,
    server_running: bool = False,
    source: object | None = "",
) -> ServerVersionState:
    installed_text = _safe_version_text(installed)
    latest_text = _safe_version_text(latest)
    branch_text = _safe_version_text(branch) or DEFAULT_SERVER_BRANCH

    if not installed_text or not latest_text:
        return unknown_server_version_state(
            installed=installed_text,
            latest=latest_text,
            branch=branch_text,
            last_checked=last_checked,
            server_running=server_running,
            source=source,
        )
    if installed_text == latest_text:
        return _state(
            installed=installed_text,
            latest=latest_text,
            branch=branch_text,
            last_checked=last_checked,
            check_state=SERVER_VERSION_CHECK_UPTODATE,
            status=SERVER_VERSION_STATUS_UP_TO_DATE,
            message=SERVER_VERSION_MESSAGE_UP_TO_DATE,
            up_to_date=True,
            server_running=server_running,
            source=source,
        )
    return _state(
        installed=installed_text,
        latest=latest_text,
        branch=branch_text,
        last_checked=last_checked,
        check_state=SERVER_VERSION_CHECK_AVAILABLE,
        status=SERVER_VERSION_STATUS_AVAILABLE,
        message=SERVER_VERSION_MESSAGE_AVAILABLE,
        can_update=True,
        server_running=server_running,
        source=source,
    )


def _read_installed_for_state(install_dir: Path) -> str:
    try:
        return read_installed_build_from_appmanifest(install_dir) or ""
    except OSError:
        return ""


def _cached_or_none(
    db_path: Path | None,
    *,
    instance: str,
) -> CachedServerVersionCheck | None:
    if db_path is None:
        return None
    try:
        return load_cached_server_version_check(db_path, instance=instance)
    except Exception:
        return None


def load_server_version_state(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    state: ServerState | None = None,
    adapter: ServerVersionAdapter | None = None,
    db_path: Path | None = None,
    last_checked: str | None = None,
    server_running: bool | None = None,
) -> ServerVersionState:
    """Load a safe read-only server update state for web rendering/workflows."""
    try:
        server_state = state or discovery.discover(instance=instance, save=False)
    except Exception as exc:  # noqa: BLE001 - version checks must fail closed.
        return failed_server_version_state(
            failure_reason=exc,
            last_checked=last_checked,
        )

    normalized_instance = paths.validate_instance_name(instance)
    if server_running is None:
        server_running = bool(server_state.server_running)
    else:
        server_running = bool(server_running)
    install_dir = Path(server_state.install_dir or paths.server_dir(normalized_instance))
    cached = _cached_or_none(db_path, instance=normalized_instance)
    cached_installed = cached.installed if cached is not None else ""
    cached_latest = cached.latest if cached is not None else ""
    cached_branch = cached.branch if cached is not None else DEFAULT_SERVER_BRANCH
    cached_checked_at = cached.checked_at if cached is not None else ""
    cached_source = cached.source if cached is not None else ""
    installed = _read_installed_for_state(install_dir) or cached_installed

    active_update_job_id = _active_job_id(
        db_path,
        kind=SERVER_UPDATE_JOB_KIND,
        instance=normalized_instance,
    )
    if active_update_job_id is not None:
        return _state(
            installed=installed,
            latest=cached_latest,
            branch=cached_branch,
            last_checked=last_checked or cached_checked_at,
            check_state=SERVER_VERSION_CHECK_UPDATING,
            status=SERVER_VERSION_STATUS_UPDATING,
            message=SERVER_VERSION_MESSAGE_UPDATING,
            update_job_id=active_update_job_id,
            server_running=server_running,
            source=cached_source,
        )

    active_check_job_id = _active_job_id(
        db_path,
        kind=SERVER_UPDATE_CHECK_JOB_KIND,
        instance=normalized_instance,
    )
    if active_check_job_id is not None:
        return _state(
            installed=installed,
            latest=cached_latest,
            branch=cached_branch,
            last_checked=last_checked or cached_checked_at,
            check_state=SERVER_VERSION_CHECK_CHECKING,
            status=SERVER_VERSION_STATUS_CHECKING,
            message=SERVER_VERSION_MESSAGE_CHECKING,
            check_job_id=active_check_job_id,
            server_running=server_running,
            source=cached_source,
        )

    if not server_state.server_installed and not install_dir.exists():
        return unknown_server_version_state(
            installed=installed,
            last_checked=last_checked,
            server_running=server_running,
        )

    if adapter is not None:
        try:
            probe = adapter.probe(install_dir, instance=normalized_instance)
        except Exception as exc:  # noqa: BLE001 - dashboard must fail closed.
            return failed_server_version_state(
                installed=installed,
                failure_reason=exc,
                last_checked=last_checked,
                server_running=server_running,
            )
        return _version_state_from_values(
            installed=probe.installed,
            latest=probe.latest,
            branch=probe.branch,
            last_checked=last_checked,
            server_running=server_running,
            source=probe.source,
        )

    if cached is None:
        return unknown_server_version_state(
            installed=installed,
            branch=DEFAULT_SERVER_BRANCH,
            last_checked=last_checked,
            server_running=server_running,
        )
    if cached.check_state == SERVER_VERSION_CHECK_FAILED:
        return failed_server_version_state(
            installed=installed,
            latest=cached.latest,
            branch=cached.branch,
            failure_reason=cached.failure_reason,
            last_checked=last_checked or cached.checked_at,
            server_running=server_running,
            source=cached.source,
        )
    return _version_state_from_values(
        installed=installed,
        latest=cached.latest,
        branch=cached.branch,
        last_checked=last_checked or cached.checked_at,
        server_running=server_running,
        source=cached.source,
    )


def refresh_latest_server_version_state(
    db_path: Path,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    state: ServerState | None = None,
    adapter: ServerVersionAdapter | None = None,
    job_id: int | None = None,
) -> ServerVersionState:
    """Run an explicit latest-build check and cache only safe metadata."""
    checked_at = _utc_now()
    normalized_instance = paths.validate_instance_name(instance)
    try:
        server_state = state or discovery.discover(instance=normalized_instance, save=False)
    except Exception as exc:  # noqa: BLE001 - latest checks must fail closed.
        save_server_version_check(
            db_path,
            instance=normalized_instance,
            check_state=SERVER_VERSION_CHECK_FAILED,
            failure_reason=exc,
            checked_at=checked_at,
            job_id=job_id,
        )
        return failed_server_version_state(failure_reason=exc, last_checked=checked_at)

    install_dir = Path(server_state.install_dir or paths.server_dir(normalized_instance))
    installed = _read_installed_for_state(install_dir)
    probe_adapter = adapter or SteamCmdAppInfoVersionAdapter()
    source = _safe_source_text(getattr(probe_adapter, "source", STEAMCMD_APP_INFO_SOURCE))
    try:
        probe = probe_adapter.probe(install_dir, instance=normalized_instance)
    except Exception as exc:  # noqa: BLE001 - cache controlled check failure.
        cached = save_server_version_check(
            db_path,
            instance=normalized_instance,
            installed=installed,
            latest="",
            branch=DEFAULT_SERVER_BRANCH,
            check_state=SERVER_VERSION_CHECK_FAILED,
            failure_reason=exc,
            checked_at=checked_at,
            source=source,
            job_id=job_id,
        )
        return failed_server_version_state(
            installed=cached.installed,
            latest=cached.latest,
            branch=cached.branch,
            failure_reason=cached.failure_reason,
            last_checked=cached.checked_at,
            server_running=bool(server_state.server_running),
            source=cached.source,
        )

    version_state = _version_state_from_values(
        installed=probe.installed,
        latest=probe.latest,
        branch=probe.branch,
        last_checked=checked_at,
        server_running=bool(server_state.server_running),
        source=probe.source or source,
    )
    save_server_version_check(
        db_path,
        instance=normalized_instance,
        installed=version_state.installed,
        latest=version_state.latest,
        branch=version_state.branch,
        check_state=version_state.check_state,
        failure_reason=version_state.failure_reason,
        checked_at=checked_at,
        source=version_state.source,
        job_id=job_id,
    )
    return version_state
