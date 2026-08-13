# Server-update page view helpers.

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from armactl.web.services import server_job_actions, server_versions

STALE_ACTIVE_JOB_SECONDS = 6 * 60 * 60


def _text(value: Any, default: str = "unknown") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _bool(value: Any) -> bool:
    return bool(value)


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _timestamp(value: Any) -> datetime | None:
    text = _text(value, "")
    if not text or text == "never":
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _timestamp_is_older_than(value: Any, seconds: int) -> bool:
    parsed = _timestamp(value)
    if parsed is None:
        return False
    age = datetime.now(timezone.utc) - parsed
    return timedelta(0) <= age and age >= timedelta(seconds=seconds)


def _job_timestamp(job: Mapping[str, Any]) -> Any:
    return (
        job.get("updated_at")
        or job.get("updatedAt")
        or job.get("started_at")
        or job.get("startedAt")
        or job.get("created_at")
        or job.get("createdAt")
    )


def _job_status(job: Mapping[str, Any]) -> str:
    status = _text(job.get("status"), "")
    return status if status in {"queued", "running"} else ""


def _job_status_label(status: str) -> str:
    if status == "queued":
        return "already queued"
    if status == "running":
        return "already running"
    return "active job"


def _job_lease_expires_at(job: Mapping[str, Any]) -> Any:
    return job.get("worker_lease_expires_at") or job.get("workerLeaseExpiresAt")


def _job_has_expired_lease(job: Mapping[str, Any]) -> bool:
    if _job_status(job) != "running":
        return False
    lease_state = _text(
        job.get("worker_lease_state") or job.get("workerLeaseState"),
        "",
    )
    if lease_state == "expired":
        return True
    return _timestamp_is_older_than(_job_lease_expires_at(job), 0)


def _job_may_be_stale(job: Mapping[str, Any]) -> bool:
    if not _job_status(job):
        return False
    if _job_has_expired_lease(job):
        return True
    return _timestamp_is_older_than(_job_timestamp(job), STALE_ACTIVE_JOB_SECONDS)


def _job_link(job_id: int, label: str) -> dict[str, Any]:
    return {"id": job_id, "label": label, "jobs_url": "/jobs#background-jobs"}


def _active_job_notice(*, check_state: str, status: str) -> str:
    if check_state == server_versions.SERVER_VERSION_CHECK_CHECKING:
        if status == "queued":
            return "Update check job already queued."
        if status == "running":
            return "Update check job already running."
        return "An update check job is already active."
    if check_state == server_versions.SERVER_VERSION_CHECK_UPDATING:
        if status == "queued":
            return "Server update job already queued."
        if status == "running":
            return "Server update job already running."
        return "A server update job is already active."
    return ""


def _active_job_view(
    *,
    check_state: str,
    check_job_id: int | None,
    update_job_id: int | None,
    job: Mapping[str, Any],
) -> dict[str, Any] | None:
    job_id = _positive_int(job.get("id"))
    if check_state == server_versions.SERVER_VERSION_CHECK_UPDATING:
        active_id = update_job_id or job_id
        label = "Active update job"
    elif check_state == server_versions.SERVER_VERSION_CHECK_CHECKING:
        active_id = check_job_id or job_id
        label = "Active update check job"
    else:
        return None
    if active_id is None:
        return None

    status = _job_status(job)
    lease_expired = _job_has_expired_lease(job)
    active_job = _job_link(active_id, label)
    active_job["status"] = status
    active_job["status_label"] = _job_status_label(status) if status else ""
    active_job["notice"] = _active_job_notice(
        check_state=check_state,
        status=status,
    )
    active_job["lease_expired"] = lease_expired
    active_job["may_be_stale"] = _job_may_be_stale(job)
    active_job["stale_guidance"] = (
        "Worker lease expired for this running job. This is diagnostics only; "
        "the web UI did not cancel, repair, or stop processes."
        if lease_expired
        else (
            "This job may be stale. This is diagnostics only; open Jobs to "
            "review the active row; use the CLI fallback if the web worker "
            "is no longer running."
        )
    )
    return active_job


def _failed_update_job_view(job: Mapping[str, Any]) -> dict[str, Any] | None:
    job_id = _positive_int(job.get("id"))
    if job_id is None:
        return None
    return _job_link(job_id, "Last failed update job")


def _cache_result_is_stale(*, check_state: str, last_checked: str) -> bool:
    if check_state not in {
        server_versions.SERVER_VERSION_CHECK_UPTODATE,
        server_versions.SERVER_VERSION_CHECK_AVAILABLE,
        server_versions.SERVER_VERSION_CHECK_FAILED,
    }:
        return False
    return _timestamp_is_older_than(
        last_checked,
        server_versions.SERVER_VERSION_CHECK_CACHE_TTL_SECONDS,
    )


def _check_action_label(
    *,
    check_state: str,
    can_request_check: bool,
    last_checked: str,
) -> str:
    if not can_request_check:
        return "Check for updates"
    if check_state == server_versions.SERVER_VERSION_CHECK_FAILED:
        return "Check again"
    if _cache_result_is_stale(
        check_state=check_state,
        last_checked=last_checked,
    ):
        return "Check again"
    return "Check for updates"


def _cache_notice(*, check_state: str, last_checked: str) -> str:
    if not _cache_result_is_stale(
        check_state=check_state,
        last_checked=last_checked,
    ):
        return ""
    return (
        "Cached check result is stale. Check again to refresh latest build "
        "metadata before updating."
    )


def _failure_guidance(check_state: str, check_job_id: int | None) -> str:
    if check_state != server_versions.SERVER_VERSION_CHECK_FAILED:
        return ""
    if check_job_id:
        return (
            "Check again to refresh latest build metadata. Open Jobs for the failed "
            "check; use the CLI fallback if SteamCMD keeps failing."
        )
    return (
        "Check again to refresh latest build metadata. Use the CLI fallback if "
        "SteamCMD keeps failing."
    )


def _failed_update_guidance(
    *,
    failed_update_job: dict[str, Any] | None,
    backend_allows_update: bool,
    server_running: bool,
    checking_or_updating: bool,
) -> str:
    if failed_update_job is None:
        return ""
    if backend_allows_update:
        return (
            "The last update job failed. Retry update is available because the game "
            "server appears stopped and no update job is active. Open Jobs for details; "
            "use the CLI fallback if the web retry fails."
        )
    if server_running:
        return (
            "The last update job failed. Stop the game server before retrying. Open "
            "Jobs for details; use the CLI fallback if needed."
        )
    if checking_or_updating:
        return (
            "The last update job failed. Wait for the active job to finish, then open "
            "Jobs for details or use the CLI fallback if needed."
        )
    return (
        "The last update job failed. Run a build check before retrying. Open Jobs for "
        "details; use the CLI fallback if needed."
    )


def _version(page: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = page.get("version")
    return raw if isinstance(raw, Mapping) else {}


def _item(
    label: str,
    value: Any,
    *,
    translate_value: bool = False,
    timestamp: bool = False,
) -> dict[str, Any]:
    return dict(
        label=label,
        value=_text(value),
        translate_value=translate_value,
        timestamp=timestamp,
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
        return "An update check job is already active."
    if check_state == server_versions.SERVER_VERSION_CHECK_UPDATING:
        return "A server update job is already active."
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
    action_notice: str = "",
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
    check_job_id = _positive_int(
        version.get("check_job_id") or version.get("checkJobId")
    )
    update_job_id = _positive_int(
        version.get("update_job_id") or version.get("updateJobId")
    )
    active_job = _active_job_view(
        check_state=check_state,
        check_job_id=check_job_id,
        update_job_id=update_job_id,
        job=_mapping(version.get("active_job") or version.get("activeJob")),
    )
    failed_update_job = _failed_update_job_view(
        _mapping(version.get("failed_update_job") or version.get("failedUpdateJob"))
    )
    failed_check_job = None
    if check_state == server_versions.SERVER_VERSION_CHECK_FAILED and check_job_id:
        failed_check_job = _job_link(check_job_id, "Failed update check job")
    compatibility = dict(_mapping(page.get("compatibility")))
    compatibility.setdefault("parked_profile_compatibility", {})
    raw_profiles = page.get("profiles")
    profiles = []
    if isinstance(raw_profiles, list):
        for item in raw_profiles:
            if not isinstance(item, Mapping):
                continue
            profile = dict(item)
            profile.setdefault(
                "compatibility",
                {
                    "status": "not_tested",
                    "label": "Not tested for current build",
                    "css_class": "unavailable",
                    "tested_at": "",
                    "tested_build_id": "",
                    "reason": "",
                },
            )
            profiles.append(profile)
    policy = dict(_mapping(page.get("policy")))
    profile_job = dict(_mapping(page.get("profile_job")))
    profile_operation_active = bool(profile_job)
    profile_actions_enabled = (
        can_update_server
        and not server_running
        and not checking_or_updating
        and not profile_operation_active
    )
    if not can_update_server:
        profile_actions_disabled_reason = "Server update permission is required."
    elif server_running:
        profile_actions_disabled_reason = (
            "Stop the game server before switching or testing profiles."
        )
    elif checking_or_updating:
        profile_actions_disabled_reason = (
            "Wait for the active update operation before changing profiles."
        )
    elif profile_operation_active:
        profile_actions_disabled_reason = (
            "Wait for the active profile operation to finish."
        )
    else:
        profile_actions_disabled_reason = ""

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
                timestamp=last_checked != "never",
            ),
            _item("Check state", _check_state_label(check_state), translate_value=True),
        ],
        server_running=server_running,
        can_request_check=can_request_check,
        check_action_label=_check_action_label(
            check_state=check_state,
            can_request_check=can_request_check,
            last_checked=last_checked,
        ),
        check_disabled=not can_request_check,
        check_disabled_reason="" if can_request_check else note,
        can_update_server=can_update_server,
        backend_allows_update=backend_allows_update,
        update_action_label=(
            "Retry update" if failed_update_job and backend_allows_update else "Update server"
        ),
        update_note=note,
        cache_notice=_cache_notice(
            check_state=check_state,
            last_checked=last_checked,
        ),
        failure_reason=failure_reason,
        failure_guidance=_failure_guidance(check_state, check_job_id),
        failed_check_job=failed_check_job,
        failed_update_job=failed_update_job,
        failed_update_guidance=_failed_update_guidance(
            failed_update_job=failed_update_job,
            backend_allows_update=backend_allows_update,
            server_running=server_running,
            checking_or_updating=checking_or_updating,
        ),
        action_notice=_text(action_notice, ""),
        active_job=active_job,
        compatibility=compatibility,
        profiles=profiles,
        policy=policy,
        profile_job=profile_job,
        profile_actions_enabled=profile_actions_enabled,
        profile_actions_disabled_reason=profile_actions_disabled_reason,
    )
