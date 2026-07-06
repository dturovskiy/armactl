"""Instance-scoped runtime settings for generated armactl launch files.

These values are armactl metadata. They are intentionally stored outside the
server-facing config.json because the Arma Reforger server does not read them
from that schema.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from armactl import paths

DEFAULT_MAX_FPS_PROFILE = 60
ALLOWED_MAX_FPS_PROFILES = frozenset({60, 120})
RUNTIME_SETTINGS_VERSION = 1
RUNTIME_SETTINGS_SCOPE = "armactl-generated-runtime"
MAX_FPS_ARG_RE = re.compile(r"(?:^|\s)-maxFPS\s+(\d+)(?:\s|$)")


class RuntimeSettingsError(ValueError):
    """Raised when armactl runtime settings are invalid or unavailable."""


@dataclass(frozen=True)
class RuntimeMaxFpsStatus:
    """Safe view of the configured and generated max FPS profile."""

    configured: int
    generated: int | None
    settings_exists: bool
    generated_exists: bool

    @property
    def generated_matches(self) -> bool:
        return self.generated == self.configured


def normalize_max_fps_profile(value: int | str) -> int:
    """Return a supported max FPS profile or raise RuntimeSettingsError."""
    if isinstance(value, bool):
        raise RuntimeSettingsError("Unsupported max FPS profile. Choose 60 or 120.")
    if isinstance(value, int):
        profile = value
    else:
        raw = str(value or "").strip()
        if raw not in {"60", "120"}:
            raise RuntimeSettingsError("Unsupported max FPS profile. Choose 60 or 120.")
        profile = int(raw)
    if profile not in ALLOWED_MAX_FPS_PROFILES:
        raise RuntimeSettingsError("Unsupported max FPS profile. Choose 60 or 120.")
    return profile


def _runtime_settings_path(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
) -> Path:
    return paths.runtime_settings_file(instance, data_root)


def load_runtime_settings(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
) -> dict[str, Any]:
    """Load armactl-only generated runtime settings for an instance."""
    settings_path = _runtime_settings_path(instance, data_root)
    if not settings_path.is_file():
        return {}
    try:
        payload = json.loads(settings_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeSettingsError("Runtime settings file is not valid JSON.") from exc
    except OSError as exc:
        raise RuntimeSettingsError("Runtime settings file could not be read.") from exc
    if not isinstance(payload, dict):
        raise RuntimeSettingsError("Runtime settings file must contain a JSON object.")
    return dict(payload)


def load_max_fps_profile(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
) -> int:
    """Load the configured max FPS profile, defaulting new instances to 60."""
    payload = load_runtime_settings(instance, data_root=data_root)
    if "max_fps" not in payload:
        return DEFAULT_MAX_FPS_PROFILE
    return normalize_max_fps_profile(payload.get("max_fps"))


def save_max_fps_profile(
    instance: str,
    max_fps: int | str,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
) -> Path:
    """Persist the max FPS profile as armactl-only instance metadata."""
    profile = normalize_max_fps_profile(max_fps)
    settings_path = _runtime_settings_path(instance, data_root)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": RUNTIME_SETTINGS_VERSION,
        "scope": RUNTIME_SETTINGS_SCOPE,
        "max_fps": profile,
    }
    temp_path = settings_path.with_suffix(settings_path.suffix + ".tmp")
    try:
        temp_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        temp_path.chmod(0o600)
        os.replace(temp_path, settings_path)
        settings_path.chmod(0o600)
    except OSError as exc:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeSettingsError("Runtime settings file could not be saved.") from exc
    return settings_path


def parse_generated_max_fps(script_text: str) -> int | None:
    """Parse a generated start script max FPS value without accepting arbitrary args."""
    matches = MAX_FPS_ARG_RE.findall(script_text)
    if not matches:
        return None
    return normalize_max_fps_profile(matches[-1])


def read_generated_max_fps_profile(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
) -> int | None:
    """Read the generated start script max FPS profile, if present."""
    start_script = paths.start_script(instance, data_root)
    if not start_script.is_file():
        return None
    try:
        return parse_generated_max_fps(start_script.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeSettingsError("Generated start script could not be read.") from exc


def read_max_fps_status(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    data_root: Path = paths.DEFAULT_DATA_ROOT,
) -> RuntimeMaxFpsStatus:
    """Return a safe configured/generated max FPS status for service code."""
    settings_path = _runtime_settings_path(instance, data_root)
    start_script = paths.start_script(instance, data_root)
    return RuntimeMaxFpsStatus(
        configured=load_max_fps_profile(instance, data_root=data_root),
        generated=read_generated_max_fps_profile(instance, data_root=data_root),
        settings_exists=settings_path.is_file(),
        generated_exists=start_script.is_file(),
    )
