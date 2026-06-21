"""Configuration helpers for the armactl web runtime `.env` file."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from armactl.ports import WEB_PANEL_DEFAULT_PORT
from armactl.web.launcher import DEFAULT_WEB_HOST, validate_web_port
from armactl.web.runtime.paths import (
    resolved_data_root,
    web_audit_log_file,
    web_db_file,
    web_env_file,
    web_runtime_dir,
)

TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}
SESSION_SECRET_BYTES = 48
COOKIE_NAMESPACE_BYTES = 12
COOKIE_NAMESPACE_MAX_LENGTH = 64
COOKIE_NAMESPACE_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
)
DEFAULT_HTTPS_REQUIRED = False


class WebRuntimeConfigError(ValueError):
    """Raised when the web runtime config cannot be parsed or saved."""


@dataclass
class WebRuntimeConfig:
    """Normalized web runtime settings stored under `~/armactl-data/web/`."""

    data_root: Path
    runtime_dir: Path
    env_path: Path
    db_path: Path
    audit_log_path: Path
    session_secret: str = field(repr=False)
    cookie_namespace: str = field(repr=False)
    bind_host: str = DEFAULT_WEB_HOST
    bind_port: int = WEB_PANEL_DEFAULT_PORT
    https_required: bool = DEFAULT_HTTPS_REQUIRED


def _generate_session_secret() -> str:
    """Generate a new cookie/session signing secret without exposing it."""
    return secrets.token_urlsafe(SESSION_SECRET_BYTES)


def _generate_cookie_namespace() -> str:
    """Generate a stable namespace for cookies on shared hostnames."""
    return secrets.token_urlsafe(COOKIE_NAMESPACE_BYTES).rstrip("=")


def _parse_env_mapping(text: str) -> dict[str, str]:
    """Parse a small `.env` file into a string mapping."""
    parsed: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        parsed[key.strip()] = value.strip().strip("\"'")
    return parsed


def _read_env_mapping(env_path: Path) -> dict[str, str]:
    """Read the web `.env` file, returning defaults when it is absent."""
    if not env_path.exists():
        return {}

    try:
        return _parse_env_mapping(env_path.read_text(encoding="utf-8"))
    except OSError as e:
        raise WebRuntimeConfigError(f"Failed to read web config file: {e}") from e


def _parse_bool(raw_value: str, *, field_name: str) -> bool:
    value = raw_value.strip().lower()
    if value in TRUE_VALUES:
        return True
    if value in FALSE_VALUES:
        return False
    raise WebRuntimeConfigError(f"{field_name} must be true or false.")


def _parse_port(raw_value: str) -> int:
    try:
        port = int(raw_value.strip())
    except ValueError as e:
        raise WebRuntimeConfigError("Port must be an integer between 1 and 65535.") from e

    _validate_bind_port(port)
    return port


def _validate_bind_port(port: object) -> None:
    try:
        validate_web_port(port)
    except ValueError as e:
        raise WebRuntimeConfigError(str(e)) from e


def _validate_env_value(field_name: str, value: str, *, required: bool = True) -> str:
    normalized = value.strip()
    if required and not normalized:
        raise WebRuntimeConfigError(f"{field_name} cannot be empty.")
    if "\n" in normalized or "\r" in normalized:
        raise WebRuntimeConfigError(f"{field_name} cannot contain newlines.")
    return normalized


def _validate_cookie_namespace(value: str) -> str:
    normalized = _validate_env_value("ARMACTL_WEB_COOKIE_NAMESPACE", value)
    if len(normalized) > COOKIE_NAMESPACE_MAX_LENGTH:
        raise WebRuntimeConfigError("ARMACTL_WEB_COOKIE_NAMESPACE is too long.")
    if any(character not in COOKIE_NAMESPACE_CHARS for character in normalized):
        raise WebRuntimeConfigError(
            "ARMACTL_WEB_COOKIE_NAMESPACE can contain only letters, digits, _ or -."
        )
    return normalized


def _validate_config(config: WebRuntimeConfig) -> None:
    _validate_env_value("ARMACTL_WEB_SESSION_SECRET", config.session_secret)
    _validate_cookie_namespace(config.cookie_namespace)
    _validate_env_value("ARMACTL_WEB_BIND_HOST", config.bind_host)
    if not isinstance(config.https_required, bool):
        raise WebRuntimeConfigError("ARMACTL_WEB_HTTPS_REQUIRED must be true or false.")
    _validate_bind_port(config.bind_port)


def _config_from_mapping(data_root: Path | None, data: dict[str, str]) -> WebRuntimeConfig:
    root = resolved_data_root(data_root)
    runtime_dir = web_runtime_dir(root)
    env_path = web_env_file(root)
    db_path = web_db_file(root)
    audit_log_path = web_audit_log_file(root)

    session_secret = data.get("ARMACTL_WEB_SESSION_SECRET", "").strip()
    if not session_secret:
        session_secret = _generate_session_secret()

    cookie_namespace = data.get("ARMACTL_WEB_COOKIE_NAMESPACE", "").strip()
    if not cookie_namespace:
        cookie_namespace = _generate_cookie_namespace()
    else:
        cookie_namespace = _validate_cookie_namespace(cookie_namespace)

    bind_host = data.get("ARMACTL_WEB_BIND_HOST", DEFAULT_WEB_HOST).strip() or DEFAULT_WEB_HOST
    bind_port = _parse_port(data.get("ARMACTL_WEB_BIND_PORT", str(WEB_PANEL_DEFAULT_PORT)))
    https_required = _parse_bool(
        data.get("ARMACTL_WEB_HTTPS_REQUIRED", str(DEFAULT_HTTPS_REQUIRED).lower()),
        field_name="ARMACTL_WEB_HTTPS_REQUIRED",
    )

    config = WebRuntimeConfig(
        data_root=root,
        runtime_dir=runtime_dir,
        env_path=env_path,
        db_path=db_path,
        audit_log_path=audit_log_path,
        session_secret=session_secret,
        cookie_namespace=cookie_namespace,
        bind_host=bind_host,
        bind_port=bind_port,
        https_required=https_required,
    )
    _validate_config(config)
    return config


def load_web_runtime_config(data_root: Path | None = None) -> WebRuntimeConfig:
    """Load `web.env`, generating in-memory defaults when the file is missing."""
    env_path = web_env_file(data_root)
    return _config_from_mapping(data_root, _read_env_mapping(env_path))


def _mapping_needs_normalized_save(data: dict[str, str]) -> bool:
    """Return whether a partial config should be rewritten with generated defaults."""
    return (
        not data.get("ARMACTL_WEB_SESSION_SECRET", "").strip()
        or not data.get("ARMACTL_WEB_COOKIE_NAMESPACE", "").strip()
    )


def ensure_web_runtime_config(data_root: Path | None = None) -> WebRuntimeConfig:
    """Ensure `web.env` exists and contains stable generated defaults."""
    env_path = web_env_file(data_root)
    data = _read_env_mapping(env_path)
    config = _config_from_mapping(data_root, data)

    if not env_path.exists() or _mapping_needs_normalized_save(data):
        save_web_runtime_config(config)
        return load_web_runtime_config(data_root)

    return config


def render_web_runtime_config(config: WebRuntimeConfig) -> str:
    """Render a normalized `web.env` payload."""
    _validate_config(config)
    session_secret = _validate_env_value("ARMACTL_WEB_SESSION_SECRET", config.session_secret)
    cookie_namespace = _validate_cookie_namespace(config.cookie_namespace)
    bind_host = _validate_env_value("ARMACTL_WEB_BIND_HOST", config.bind_host)
    https_required = "true" if config.https_required else "false"

    # HTTPS is false by default because armactl-web binds to localhost and is
    # normally placed behind a TLS-terminating reverse proxy.
    lines = [
        "# armactl web runtime configuration",
        "# Stored outside game instances at ~/armactl-data/web/.",
        "# Keep this file private; it contains the web session secret.",
        f"ARMACTL_WEB_BIND_HOST={bind_host}",
        f"ARMACTL_WEB_BIND_PORT={config.bind_port}",
        f"ARMACTL_WEB_HTTPS_REQUIRED={https_required}",
        f"ARMACTL_WEB_COOKIE_NAMESPACE={cookie_namespace}",
        f"ARMACTL_WEB_SESSION_SECRET={session_secret}",
    ]
    return "\n".join(lines) + "\n"


def save_web_runtime_config(config: WebRuntimeConfig) -> Path:
    """Persist `web.env` atomically with owner-only permissions."""
    payload = render_web_runtime_config(config)
    env_path = config.env_path
    env_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = env_path.with_suffix(".env.tmp")

    try:
        tmp_path.unlink(missing_ok=True)
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp_path, env_path)
        env_path.chmod(0o600)
    except OSError as e:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise WebRuntimeConfigError(f"Failed to save web config file: {e}") from e

    return env_path
