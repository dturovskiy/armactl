"""Systemd service helpers for the armactl web panel."""

from __future__ import annotations

import subprocess
import tempfile
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


@dataclass(frozen=True)
class WebServiceInstallResult:
    config: WebRuntimeConfig
    service_name: str
    service_path: Path
    results: tuple[ServiceResult, ...]


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
            [str(python_bin), "-c", "import armactl, fastapi, uvicorn, argon2"],
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
    status.update(
        service_file=str(web_service_file()),
        installed=web_service_file().exists(),
        runtime=check_web_service_runtime().to_dict(),
        config=_safe_config_status(config, config_error),
    )
    return status
