"""Named update profiles and operator-controlled fallback policy."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from armactl.config_manager import ConfigError, load_config
from armactl.update_compatibility import (
    DEFAULT_VANILLA_SCENARIO,
    MODDED_MODE,
    VANILLA_MODE,
    make_vanilla_profile,
)

DEFAULT_ACTIVE_PROFILE = "modded"
DEFAULT_VANILLA_PROFILE = "vanilla"
DEFAULT_AUTO_VANILLA_FALLBACK = True
PROFILE_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_-]{0,62}[a-z0-9])?$")


class UpdateProfileError(RuntimeError):
    """Raised for invalid or unsafe named-profile operations."""


@dataclass(frozen=True)
class UpdatePolicy:
    automatic_vanilla_fallback: bool = DEFAULT_AUTO_VANILLA_FALLBACK

    def to_dict(self) -> dict[str, bool]:
        return {"automatic_vanilla_fallback": self.automatic_vanilla_fallback}


@dataclass(frozen=True)
class NamedProfile:
    name: str
    active: bool
    mode: str
    scenario_id: str
    mod_count: int
    path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "active": self.active,
            "mode": self.mode,
            "scenario_id": self.scenario_id,
            "mod_count": self.mod_count,
            "path": self.path,
        }


def validate_profile_name(name: str) -> str:
    value = str(name or "").strip().casefold()
    if not PROFILE_NAME_RE.fullmatch(value):
        raise UpdateProfileError(
            "Profile name must use 1-64 lowercase letters, digits, dashes, or "
            "underscores, and start/end with a letter or digit."
        )
    return value


def profile_path(profiles_root: Path, name: str) -> Path:
    safe_name = validate_profile_name(name)
    root = profiles_root.resolve(strict=False)
    target = (profiles_root / safe_name).resolve(strict=False)
    if target.parent != root:
        raise UpdateProfileError("Refusing a profile path outside the profile store.")
    return target


def read_policy(policy_path: Path) -> UpdatePolicy:
    try:
        payload = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return UpdatePolicy()
    if not isinstance(payload, dict):
        return UpdatePolicy()
    enabled = payload.get(
        "automatic_vanilla_fallback",
        DEFAULT_AUTO_VANILLA_FALLBACK,
    )
    return UpdatePolicy(
        automatic_vanilla_fallback=(
            enabled if isinstance(enabled, bool) else DEFAULT_AUTO_VANILLA_FALLBACK
        )
    )


def write_policy(policy_path: Path, *, automatic_vanilla_fallback: bool) -> UpdatePolicy:
    policy = UpdatePolicy(automatic_vanilla_fallback=automatic_vanilla_fallback)
    policy_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = policy_path.with_name(f".{policy_path.name}.tmp")
    temporary.write_text(json.dumps(policy.to_dict(), indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(policy_path)
    return policy


def inspect_profile(name: str, profile: Path, *, active: bool) -> NamedProfile:
    try:
        config = load_config(profile / "config.json")
    except ConfigError as exc:
        raise UpdateProfileError(f"Profile {name} has an invalid config: {exc}") from exc
    game = config.get("game")
    if not isinstance(game, dict):
        raise UpdateProfileError(f"Profile {name} is missing the game object.")
    mods = game.get("mods", [])
    mod_count = len(mods) if isinstance(mods, list) else 0
    scenario_id = str(game.get("scenarioId") or "")
    return NamedProfile(
        name=name,
        active=active,
        mode=(
            MODDED_MODE
            if mod_count or scenario_id != DEFAULT_VANILLA_SCENARIO
            else VANILLA_MODE
        ),
        scenario_id=scenario_id,
        mod_count=mod_count,
        path=str(profile),
    )


def list_profiles(
    profiles_root: Path,
    active_profile: Path,
    *,
    active_name: str,
) -> list[NamedProfile]:
    active_name = validate_profile_name(active_name)
    result = [inspect_profile(active_name, active_profile, active=True)]
    if profiles_root.is_dir() and not profiles_root.is_symlink():
        for child in sorted(profiles_root.iterdir(), key=lambda item: item.name):
            if child.name == active_name or not child.is_dir() or child.is_symlink():
                continue
            try:
                name = validate_profile_name(child.name)
                result.append(inspect_profile(name, child, active=False))
            except UpdateProfileError:
                continue
    return result


def create_profile(
    profiles_root: Path,
    active_profile: Path,
    *,
    name: str,
    vanilla: bool,
) -> NamedProfile:
    name = validate_profile_name(name)
    destination = profile_path(profiles_root, name)
    if destination.exists() or destination.is_symlink():
        raise UpdateProfileError(f"Profile already exists: {name}")
    profiles_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        if vanilla:
            make_vanilla_profile(active_profile, destination)
            addons = destination / "addons"
            if addons.is_dir():
                addons.rmdir()
        else:
            config = active_profile / "config.json"
            if not config.is_file() or config.is_symlink():
                raise UpdateProfileError("Active profile config is missing or unsafe.")
            destination.mkdir(mode=0o700)
            destination_config = destination / "config.json"
            destination_config.write_bytes(config.read_bytes())
            os.chmod(destination_config, 0o600)
            active_mods_state = active_profile.parent / "mods-state.json"
            if active_mods_state.is_file() and not active_mods_state.is_symlink():
                destination_state = destination / "mods-state.json"
                destination_state.write_bytes(active_mods_state.read_bytes())
                os.chmod(destination_state, 0o600)
    except Exception:
        if destination.is_dir():
            for child in destination.iterdir():
                child.unlink(missing_ok=True)
            destination.rmdir()
        raise
    return inspect_profile(name, destination, active=False)


def remove_profile(profiles_root: Path, name: str) -> None:
    """Remove an explicitly selected inactive profile store entry."""
    target = profile_path(profiles_root, name)
    if not target.is_dir() or target.is_symlink():
        raise UpdateProfileError(f"Stored profile does not exist: {name}")
    for child in target.iterdir():
        if child.is_dir() or child.is_symlink():
            raise UpdateProfileError("Profile bundle contains unexpected nested content.")
        child.unlink()
    target.rmdir()
