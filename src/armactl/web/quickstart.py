"""One-command setup workflow for the armactl web panel."""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from armactl.service_manager import ServiceResult
from armactl.web import service as web_service
from armactl.web.auth import WebAuthError
from armactl.web.auth.models import UserRecord
from armactl.web.auth.setup import owner_user_exists, setup_owner_user
from armactl.web.runtime import (
    WebRuntimeConfig,
    WebRuntimeConfigError,
    ensure_web_runtime,
    load_web_runtime_config,
    save_web_runtime_config,
)

LOCAL_ACCESS = "local"
LAN_ACCESS = "lan"
ACCESS_BIND_HOSTS = {
    LOCAL_ACCESS: "127.0.0.1",
    LAN_ACCESS: "0.0.0.0",
}


class WebQuickstartError(RuntimeError):
    """Raised when the web quickstart workflow cannot continue safely."""


@dataclass(frozen=True)
class WebQuickstartRequest:
    """Operator choices for the first-run web setup workflow."""

    data_root: Path | None
    bind_host: str
    bind_port: int
    https_required: bool | None
    owner_username: str | None
    owner_password: str | None


@dataclass(frozen=True)
class WebQuickstartResult:
    """Result of preparing and starting the web service."""

    config: WebRuntimeConfig
    owner_created: bool
    owner_existing: bool
    owner_user: UserRecord | None
    install_result: web_service.WebServiceInstallResult
    start_result: ServiceResult | None


def bind_host_for_access_mode(access_mode: str) -> str:
    """Map friendly access modes to concrete bind hosts."""
    try:
        return ACCESS_BIND_HOSTS[access_mode]
    except KeyError as error:
        raise WebQuickstartError(f"Unknown web access mode: {access_mode}") from error


def web_owner_exists(data_root: Path | None = None) -> bool:
    """Return whether the web runtime already has an owner user."""
    config = ensure_web_runtime(data_root)
    return owner_user_exists(config.db_path)


def _apply_runtime_settings(request: WebQuickstartRequest) -> WebRuntimeConfig:
    config = ensure_web_runtime(request.data_root)
    target_https_required = (
        config.https_required if request.https_required is None else request.https_required
    )
    updated = replace(
        config,
        bind_host=request.bind_host,
        bind_port=request.bind_port,
        https_required=target_https_required,
    )
    if updated != config:
        save_web_runtime_config(updated)
        return load_web_runtime_config(request.data_root)
    return config


def _ensure_owner(
    request: WebQuickstartRequest,
    config: WebRuntimeConfig,
) -> tuple[bool, bool, UserRecord | None]:
    try:
        if owner_user_exists(config.db_path):
            return False, True, None
    except WebAuthError as error:
        raise WebQuickstartError(str(error)) from error

    if not request.owner_username:
        raise WebQuickstartError("Owner username is required for first web setup.")
    if not request.owner_password:
        raise WebQuickstartError("Owner password is required for first web setup.")

    try:
        setup_result = setup_owner_user(
            request.data_root,
            request.owner_username,
            request.owner_password,
        )
    except (WebAuthError, WebRuntimeConfigError) as error:
        raise WebQuickstartError(str(error)) from error

    return True, False, setup_result.user


def _install_succeeded(result: web_service.WebServiceInstallResult) -> bool:
    return all(service_result.success for service_result in result.results)


def _health_host(bind_host: str) -> str:
    if bind_host in {"0.0.0.0", "::"}:
        return "127.0.0.1"
    return bind_host


def wait_for_web_health(config: WebRuntimeConfig, timeout_seconds: float = 10.0) -> ServiceResult:
    deadline = time.monotonic() + timeout_seconds
    url = f"http://{_health_host(config.bind_host)}:{config.bind_port}/healthz"
    last_error = "not ready"

    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=1.0) as response:
                if 200 <= response.status < 300:
                    return ServiceResult(True, f"Web service is ready: {url}", 0)
                last_error = f"HTTP {response.status}"
        except (HTTPError, URLError, OSError, TimeoutError) as error:
            last_error = str(error)
        time.sleep(0.25)

    return ServiceResult(
        False,
        f"Web service started but did not become ready at {url}: {last_error}",
        1,
    )


def run_web_quickstart(request: WebQuickstartRequest) -> WebQuickstartResult:
    """Prepare runtime, owner, service unit, and start the web panel."""
    try:
        config = _apply_runtime_settings(request)
        owner_created, owner_existing, owner_user = _ensure_owner(request, config)
        install_result = web_service.install_web_service(request.data_root)
    except WebRuntimeConfigError as error:
        raise WebQuickstartError(str(error)) from error

    start_result = None
    if _install_succeeded(install_result):
        start_result = web_service.start_web_service()
        if start_result.success:
            start_result = wait_for_web_health(install_result.config)

    return WebQuickstartResult(
        config=install_result.config,
        owner_created=owner_created,
        owner_existing=owner_existing,
        owner_user=owner_user,
        install_result=install_result,
        start_result=start_result,
    )
