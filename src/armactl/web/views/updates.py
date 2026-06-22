# Server-update page view helpers.

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from armactl.web.services import server_job_actions, server_versions


def _text(value: Any, default: str = "unknown") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _bool(value: Any) -> bool:
    return bool(value)


def _version(page: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = page.get("version")
    return raw if isinstance(raw, Mapping) else {}


def _item(label: str, value: Any, *, translate_value: bool = False) -> dict[str, Any]:
    return dict(
        label=label,
        value=_text(value),
        translate_value=translate_value,
    )


def _check_state_label(check_state: str) -> str:
    if check_state == server_versions.SERVER_VERSION_CHECK_UPTODATE:
        return "up to date"
    if check_state == server_versions.SERVER_VERSION_CHECK_AVAILABLE:
        return "update available"
    if check_state == server_versions.SERVER_VERSION_CHECK_UNKNOWN:
        return "latest build unknown"
    if check_state == server_versions.SERVER_VERSION_CHECK_FAILED:
        return "build check failed"
    if check_state == server_versions.SERVER_VERSION_CHECK_CHECKING:
        return "checking"
    if check_state == server_versions.SERVER_VERSION_CHECK_UPDATING:
        return "updating"
    return check_state or "latest build unknown"


def _status_class(check_state: str) -> str:
    if check_state == server_versions.SERVER_VERSION_CHECK_UPTODATE:
        return "ok"
    if check_state == server_versions.SERVER_VERSION_CHECK_AVAILABLE:
        return "warning"
    if check_state == server_versions.SERVER_VERSION_CHECK_FAILED:
        return "failed"
    if check_state in {
        server_versions.SERVER_VERSION_CHECK_CHECKING,
        server_versions.SERVER_VERSION_CHECK_UPDATING,
    }:
        return "warning"
    return "unavailable"


def _update_note(
    *,
    check_state: str,
    can_update_server: bool,
    update_available: bool,
    server_running: bool,
) -> str:
    if not can_update_server:
        return "Server update permission is required."
    if check_state == server_versions.SERVER_VERSION_CHECK_CHECKING:
        return "A build check is already running."
    if check_state == server_versions.SERVER_VERSION_CHECK_UPDATING:
        return "A server update is already running."
    if update_available and server_running:
        return server_job_actions.STOP_RUNNING_SERVER_UPDATE_MESSAGE
    if check_state == server_versions.SERVER_VERSION_CHECK_UPTODATE:
        return server_versions.SERVER_VERSION_MESSAGE_UP_TO_DATE
    if check_state in {
        server_versions.SERVER_VERSION_CHECK_UNKNOWN,
        server_versions.SERVER_VERSION_CHECK_FAILED,
    }:
        return "Run a build check before updating."
    return ""


def build_updates_view(
    page: Mapping[str, Any],
    *,
    can_update_server: bool,
) -> dict[str, Any]:
    version = _version(page)
    installed = _text(version.get("installed"), "unknown")
    latest = _text(version.get("latest"), "unknown")
    branch = _text(version.get("branch"), server_versions.DEFAULT_SERVER_BRANCH)
    last_checked = _text(
        version.get("last_checked") or version.get("lastChecked"),
        "never",
    )
    check_state = _text(
        version.get("check_state") or version.get("checkState"),
        server_versions.SERVER_VERSION_CHECK_UNKNOWN,
    )
    server_running = _bool(
        page.get("server_running")
        or version.get("server_running")
        or version.get("serverRunning")
    )
    update_available = (
        check_state == server_versions.SERVER_VERSION_CHECK_AVAILABLE
        and _bool(version.get("can_update") or version.get("canUpdate"))
    )
    backend_allows_update = can_update_server and update_available and not server_running
    checking_or_updating = check_state in {
        server_versions.SERVER_VERSION_CHECK_CHECKING,
        server_versions.SERVER_VERSION_CHECK_UPDATING,
    }
    can_request_check = can_update_server and not checking_or_updating
    note = _update_note(
        check_state=check_state,
        can_update_server=can_update_server,
        update_available=update_available,
        server_running=server_running,
    )
    failure_reason = _text(
        version.get("failure_reason") or version.get("failureReason"),
        "",
    )

    return dict(
        instance=_text(page.get("instance"), "default"),
        status=dict(
            message=_text(
                version.get("message"),
                server_versions.SERVER_VERSION_MESSAGE_UNKNOWN,
            ),
            check_state=check_state,
            check_state_label=_check_state_label(check_state),
            css_class=_status_class(check_state),
        ),
        items=[
            _item(
                "Installed build",
                installed,
                translate_value=installed == "unknown",
            ),
            _item(
                "Latest build",
                latest,
                translate_value=latest == "unknown",
            ),
            _item("Branch", branch, translate_value=branch == "unknown"),
            _item(
                "Last checked",
                last_checked,
                translate_value=last_checked == "never",
            ),
            _item("Check state", _check_state_label(check_state), translate_value=True),
        ],
        server_running=server_running,
        can_request_check=can_request_check,
        check_disabled=not can_request_check,
        check_disabled_reason="" if can_request_check else note,
        can_update_server=can_update_server,
        backend_allows_update=backend_allows_update,
        update_note=note,
        failure_reason=failure_reason,
    )
