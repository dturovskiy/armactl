"""Systemd service helpers for the armactl web panel."""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from armactl import paths
from armactl.i18n import _, tr
from armactl.redaction import redact_sensitive_text, safe_subprocess_error
from armactl.service_manager import (
    ServiceResult,
    daemon_reload,
    disable_service,
    enable_service,
    get_service_status,
    install_privileged_systemctl_channel,
    install_systemd_unit_file,
    resolve_linux_user,
    restart_service,
    start_service,
    stop_service,
)
from armactl.web.launcher import validate_web_port
from armactl.web.runtime import (
    WebRuntimeConfig,
    WebRuntimeConfigError,
    ensure_web_runtime,
    load_web_runtime_config,
)
from armactl.web.security.exposure import get_exposure_warning

WEB_SERVICE_RESTART_SYSTEMCTL_TIMEOUT_SECONDS = 20
WEB_SERVICE_RESTART_HEALTH_TIMEOUT_SECONDS = 10.0
WEB_SERVICE_RESTART_READINESS_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class WebServiceInstallResult:
    config: WebRuntimeConfig
    service_name: str
    service_path: Path
    results: tuple[ServiceResult, ...]


@dataclass(frozen=True)
class WebServiceRestartResult:
    systemctl_result: ServiceResult
    http_result: ServiceResult | None
    success: bool
    message: str
    exit_code: int = 0
    readiness_result: ServiceResult | None = None


def web_service_name() -> str:
    return paths.WEB_SERVICE_NAME


def web_service_file() -> Path:
    return paths.web_service_file()


def web_python_path(project_root: Path | None = None) -> Path:
    root = project_root or paths.project_root()
    return root / ".venv" / "bin" / "python"


def _templates_dir(project_root: Path | None = None) -> Path:
    root = project_root or paths.project_root()
    return root / "templates"


def _template_environment(project_root: Path | None = None) -> Environment:
    return Environment(loader=FileSystemLoader(str(_templates_dir(project_root))))


def _normalize_generated_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized if normalized.endswith("\n") else f"{normalized}\n"


def _validate_web_service_config(config: WebRuntimeConfig) -> None:
    try:
        validate_web_port(config.bind_port)
    except ValueError as error:
        raise WebRuntimeConfigError(str(error)) from error


def check_web_service_runtime(project_root: Path | None = None) -> ServiceResult:
    python_bin = web_python_path(project_root)
    if not python_bin.exists():
        return ServiceResult(
            False,
            tr(
                "Web runtime Python not found at {path}. Re-run "
                "./scripts/bootstrap.sh --web or --dev.",
                path=python_bin,
            ),
            1,
        )

    try:
        result = subprocess.run(
            [str(python_bin), "-c", "import armactl, fastapi, uvicorn, argon2, multipart"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        return ServiceResult(False, _("Web runtime check timed out."), 1)
    except OSError as error:
        return ServiceResult(
            False,
            tr("Web runtime check failed: {error}", error=redact_sensitive_text(error)),
            1,
        )

    if result.returncode == 0:
        return ServiceResult(True, _("Web runtime is ready."))

    error_text = safe_subprocess_error(result.stderr, result.stdout)
    return ServiceResult(
        False,
        tr(
            "Web runtime dependency check failed: {error}",
            error=error_text or _("Unknown"),
        ),
        result.returncode or 1,
    )


def _health_check_host(bind_host: str) -> str:
    normalized = bind_host.strip()
    if normalized in {"", "0.0.0.0", "::", "[::]"}:
        return "127.0.0.1"
    if ":" in normalized and not normalized.startswith("["):
        return f"[{normalized}]"
    return normalized


def web_health_url(config: WebRuntimeConfig) -> str:
    host = _health_check_host(config.bind_host)
    return f"http://{host}:{config.bind_port}/healthz"


def web_readiness_url(config: WebRuntimeConfig) -> str:
    host = _health_check_host(config.bind_host)
    return f"http://{host}:{config.bind_port}/readyz"


def check_web_http_health(
    config: WebRuntimeConfig | None = None,
    data_root: Path | None = None,
    *,
    timeout_seconds: float = 0.0,
) -> ServiceResult:
    try:
        runtime_config = config or load_web_runtime_config(data_root)
    except WebRuntimeConfigError as error:
        return ServiceResult(
            False,
            tr(
                "Web HTTP health check unavailable: {error}",
                error=redact_sensitive_text(error),
            ),
            1,
        )

    url = web_health_url(runtime_config)
    deadline = time.monotonic() + max(timeout_seconds, 0.0)
    last_error = ""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while True:
        try:
            with opener.open(url, timeout=1.0) as response:
                if response.status == 200:
                    return ServiceResult(True, _("Web HTTP health check is ready."))
                last_error = f"HTTP {response.status}"
        except (OSError, TimeoutError, urllib.error.URLError) as error:
            last_error = redact_sensitive_text(error)

        if time.monotonic() >= deadline:
            break
        time.sleep(0.2)

    return ServiceResult(
        False,
        tr(
            "Web HTTP health check failed at {url}: {error}",
            url=url,
            error=last_error or _("Unknown"),
        ),
        1,
    )


def check_web_http_readiness(
    config: WebRuntimeConfig | None = None,
    data_root: Path | None = None,
    *,
    timeout_seconds: float = 0.0,
) -> ServiceResult:
    """Check schema readiness reported by the currently running web process."""
    try:
        runtime_config = config or load_web_runtime_config(data_root)
    except WebRuntimeConfigError as error:
        return ServiceResult(
            False,
            tr(
                "Web HTTP readiness check unavailable: {error}",
                error=redact_sensitive_text(error),
            ),
            1,
        )

    url = web_readiness_url(runtime_config)
    deadline = time.monotonic() + max(timeout_seconds, 0.0)
    last_error = ""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while True:
        try:
            with opener.open(url, timeout=1.0) as response:
                if response.status == 200:
                    try:
                        payload = json.loads(response.read(8192).decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        last_error = "invalid JSON response"
                    else:
                        if isinstance(payload, dict) and payload.get("ok") is True:
                            return ServiceResult(
                                True,
                                _("Web HTTP readiness check is ready."),
                            )
                        last_error = "readiness response is not ready"
                else:
                    last_error = f"HTTP {response.status}"
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return ServiceResult(
                    False,
                    _(
                        "Web readiness endpoint is unavailable; restart "
                        "armactl-web.service after updating armactl."
                    ),
                    1,
                )
            last_error = f"HTTP {error.code}"
        except (OSError, TimeoutError, urllib.error.URLError) as error:
            last_error = redact_sensitive_text(error)

        if time.monotonic() >= deadline:
            break
        time.sleep(0.2)

    return ServiceResult(
        False,
        tr(
            "Web HTTP readiness check failed: {error}",
            error=last_error or _("Unknown"),
        ),
        1,
    )


def render_web_service_unit(
    config: WebRuntimeConfig,
    *,
    project_root: Path | None = None,
    python_bin: Path | None = None,
    user: str | None = None,
    home_dir: Path | None = None,
) -> str:
    _validate_web_service_config(config)
    root = project_root or paths.project_root()
    service_template = _template_environment(root).get_template("armactl-web.service.j2")
    rendered = service_template.render(
        service_name=web_service_name(),
        user=user or resolve_linux_user(),
        home_dir=str(home_dir or Path.home()),
        project_root=str(root),
        python_bin=str(python_bin or web_python_path(root)),
        data_root=str(config.data_root),
        runtime_dir=str(config.runtime_dir),
        env_path=str(config.env_path),
        db_path=str(config.db_path),
        bind_host=config.bind_host,
        bind_port=config.bind_port,
        https_required="true" if config.https_required else "false",
    )
    return _normalize_generated_text(rendered)


def _install_privileged_web_control_channel() -> tuple[ServiceResult, ...]:
    return tuple(install_privileged_systemctl_channel())


def install_web_service(data_root: Path | None = None) -> WebServiceInstallResult:
    config = ensure_web_runtime(data_root)
    _validate_web_service_config(config)
    results: list[ServiceResult] = []

    runtime_result = check_web_service_runtime()
    if not runtime_result.success:
        return WebServiceInstallResult(
            config=config,
            service_name=web_service_name(),
            service_path=web_service_file(),
            results=(runtime_result,),
        )
    results.append(runtime_result)

    try:
        service_render = render_web_service_unit(config)
        with tempfile.TemporaryDirectory() as tempd:
            temp_service = Path(tempd) / web_service_name()
            temp_service.write_text(service_render, encoding="utf-8")
            install_result = install_systemd_unit_file(temp_service, web_service_file())
            results.append(install_result)
            if not install_result.success:
                return WebServiceInstallResult(
                    config=config,
                    service_name=web_service_name(),
                    service_path=web_service_file(),
                    results=tuple(results),
                )

        reload_result = daemon_reload()
        results.append(
            ServiceResult(
                reload_result.success,
                (
                    _("Systemd daemon reloaded")
                    if reload_result.success
                    else tr("Daemon reload failed: {message}", message=reload_result.message)
                ),
                reload_result.exit_code,
            )
        )
        if not reload_result.success:
            return WebServiceInstallResult(
                config=config,
                service_name=web_service_name(),
                service_path=web_service_file(),
                results=tuple(results),
            )

        helper_results = _install_privileged_web_control_channel()
        results.extend(helper_results)
        if not all(item.success for item in helper_results):
            return WebServiceInstallResult(
                config=config,
                service_name=web_service_name(),
                service_path=web_service_file(),
                results=tuple(results),
            )

        results.append(enable_web_service())
    except Exception as error:
        results.append(
            ServiceResult(
                False,
                tr("Web service install failed: {error}", error=redact_sensitive_text(error)),
                1,
            )
        )

    return WebServiceInstallResult(
        config=config,
        service_name=web_service_name(),
        service_path=web_service_file(),
        results=tuple(results),
    )


def start_web_service() -> ServiceResult:
    return start_service(web_service_name())


def stop_web_service() -> ServiceResult:
    return stop_service(web_service_name())


def restart_web_service() -> ServiceResult:
    return restart_service(web_service_name())


def _run_web_service_restart_command(timeout_seconds: int) -> ServiceResult:
    try:
        return restart_service(web_service_name(), timeout_seconds=timeout_seconds)
    except Exception as error:
        return ServiceResult(
            False,
            tr(
                "Web service restart command failed: {error}",
                error=redact_sensitive_text(error),
            ),
            1,
        )


def _run_web_http_health_wait(timeout_seconds: float) -> ServiceResult:
    try:
        return check_web_http_health(timeout_seconds=timeout_seconds)
    except Exception as error:
        return ServiceResult(
            False,
            tr(
                "Web HTTP health wait failed: {error}",
                error=redact_sensitive_text(error),
            ),
            1,
        )


def _run_web_http_readiness_wait(timeout_seconds: float) -> ServiceResult:
    try:
        return check_web_http_readiness(timeout_seconds=timeout_seconds)
    except Exception as error:
        return ServiceResult(
            False,
            tr(
                "Web HTTP readiness wait failed: {error}",
                error=redact_sensitive_text(error),
            ),
            1,
        )


def restart_web_service_and_wait_for_health(
    *,
    restart_timeout_seconds: int = WEB_SERVICE_RESTART_SYSTEMCTL_TIMEOUT_SECONDS,
    health_timeout_seconds: float = WEB_SERVICE_RESTART_HEALTH_TIMEOUT_SECONDS,
) -> WebServiceRestartResult:
    """Restart armactl-web.service and report systemctl and HTTP health separately."""
    systemctl_result = _run_web_service_restart_command(restart_timeout_seconds)
    if not systemctl_result.success:
        diagnostic_health = _run_web_http_health_wait(0.0)
        return WebServiceRestartResult(
            systemctl_result=systemctl_result,
            http_result=diagnostic_health,
            success=False,
            message=_(
                "Web service restart command failed; HTTP health was not used as success proof."
            ),
            exit_code=systemctl_result.exit_code or 1,
        )

    http_result = _run_web_http_health_wait(health_timeout_seconds)
    if http_result.success:
        return WebServiceRestartResult(
            systemctl_result=systemctl_result,
            http_result=http_result,
            success=True,
            message=_("Web service restart command succeeded and HTTP health is ready."),
            exit_code=0,
        )

    return WebServiceRestartResult(
        systemctl_result=systemctl_result,
        http_result=http_result,
        success=False,
        message=_("Web service restart command succeeded, but HTTP health wait failed."),
        exit_code=http_result.exit_code or 1,
    )


def restart_web_service_and_wait_for_readiness(
    *,
    restart_timeout_seconds: int = WEB_SERVICE_RESTART_SYSTEMCTL_TIMEOUT_SECONDS,
    health_timeout_seconds: float = WEB_SERVICE_RESTART_HEALTH_TIMEOUT_SECONDS,
    readiness_timeout_seconds: float = WEB_SERVICE_RESTART_READINESS_TIMEOUT_SECONDS,
) -> WebServiceRestartResult:
    """Restart web and prove systemd, liveness, and schema readiness separately."""
    result = restart_web_service_and_wait_for_health(
        restart_timeout_seconds=restart_timeout_seconds,
        health_timeout_seconds=health_timeout_seconds,
    )
    if not result.success:
        return result

    readiness_result = _run_web_http_readiness_wait(readiness_timeout_seconds)
    if not readiness_result.success:
        return WebServiceRestartResult(
            systemctl_result=result.systemctl_result,
            http_result=result.http_result,
            readiness_result=readiness_result,
            success=False,
            message=_("Web service restart command succeeded, but HTTP readiness wait failed."),
            exit_code=readiness_result.exit_code or 1,
        )

    return WebServiceRestartResult(
        systemctl_result=result.systemctl_result,
        http_result=result.http_result,
        readiness_result=readiness_result,
        success=True,
        message=_("Web service restart command succeeded and HTTP health/readiness are ready."),
        exit_code=0,
    )


def enable_web_service() -> ServiceResult:
    return enable_service(web_service_name())


def disable_web_service() -> ServiceResult:
    return disable_service(web_service_name())


def _safe_config_status(config: WebRuntimeConfig | None, error: str = "") -> dict[str, Any]:
    if config is None:
        return {"available": False, "error": error}
    warning = get_exposure_warning(config.bind_host, config.https_required)
    return {
        "available": True,
        "data_root": str(config.data_root),
        "runtime_dir": str(config.runtime_dir),
        "env_path": str(config.env_path),
        "db_path": str(config.db_path),
        "audit_log_path": str(config.audit_log_path),
        "bind_host": config.bind_host,
        "bind_port": config.bind_port,
        "https_required": config.https_required,
        "exposure_warning": warning.to_dict() if warning is not None else None,
    }


def get_web_service_status(data_root: Path | None = None) -> dict[str, Any]:
    config: WebRuntimeConfig | None = None
    config_error = ""
    try:
        config = load_web_runtime_config(data_root)
    except WebRuntimeConfigError as error:
        config_error = redact_sensitive_text(error)

    status = get_service_status(web_service_name())
    if config is None:
        http_result = ServiceResult(False, _("Web HTTP health check unavailable."), 1)
        readiness_result = ServiceResult(False, _("Web HTTP readiness check unavailable."), 1)
    elif status.get("active"):
        http_result = check_web_http_health(config=config)
        readiness_result = check_web_http_readiness(config=config)
    else:
        http_result = ServiceResult(False, _("Web service is not active."), 1)
        readiness_result = ServiceResult(False, _("Web service is not active."), 1)
    status.update(
        service_file=str(web_service_file()),
        installed=web_service_file().exists(),
        runtime=check_web_service_runtime().to_dict(),
        http=http_result.to_dict(),
        readiness=readiness_result.to_dict(),
        config=_safe_config_status(config, config_error),
    )
    return status
