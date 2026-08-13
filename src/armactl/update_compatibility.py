"""Persistent configuration safeguards for compatibility-first server updates."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from armactl.config_manager import ConfigError, load_config

MODDED_MODE = "modded"
VANILLA_MODE = "vanilla"
VALID_MODES = frozenset({MODDED_MODE, VANILLA_MODE})

# Official Conflict (Everon). This is deliberately an official, addon-free
# scenario so a server can remain playable while Workshop authors catch up.
DEFAULT_VANILLA_SCENARIO = "{ECC61978EDCC2B5A}Missions/23_Campaign.conf"
VANILLA_NAME_SUFFIX = " [vanilla compatibility]"
MAX_SERVER_NAME_LENGTH = 100

_BACKUP_METADATA_SUFFIXES = frozenset(
    {".conf", ".gproj", ".json", ".md", ".txt", ".xml", ".yaml", ".yml"}
)
_BACKUP_EXCLUDED_NAMES = frozenset(
    {"resourceDatabase.rdb", "thumbnail.png"}
)
_MAX_BACKUP_METADATA_FILE_BYTES = 1024 * 1024


class CompatibilityConfigError(RuntimeError):
    """Raised when a safe vanilla profile or baseline cannot be created."""


@dataclass(frozen=True)
class CompatibilityStatus:
    """Operator-facing compatibility state for one instance."""

    active_mode: str
    active_build: str
    parked_modded_profile: str
    parked_modded_available: bool
    rollback_available: bool
    fallback_scenario: str
    disabled_mod_count: int
    last_modded_failure: str
    baseline_path: str
    phase: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_mode": self.active_mode,
            "active_build": self.active_build,
            "parked_modded_profile": self.parked_modded_profile,
            "parked_modded_available": self.parked_modded_available,
            "rollback_available": self.rollback_available,
            "fallback_scenario": self.fallback_scenario,
            "disabled_mod_count": self.disabled_mod_count,
            "last_modded_failure": self.last_modded_failure,
            "baseline_path": self.baseline_path,
            "phase": self.phase,
        }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def configured_mods(profile: Path) -> list[dict[str, str]]:
    """Return a sanitized inventory of mods referenced by a profile config."""
    try:
        config = load_config(profile / "config.json")
    except ConfigError as exc:
        raise CompatibilityConfigError(f"Could not read profile config: {exc}") from exc
    game = config.get("game")
    if not isinstance(game, dict):
        raise CompatibilityConfigError("Profile config is missing the game object.")
    raw_mods = game.get("mods", [])
    if not isinstance(raw_mods, list):
        raise CompatibilityConfigError("Profile game.mods must be a list.")

    result: list[dict[str, str]] = []
    for item in raw_mods:
        if not isinstance(item, dict):
            continue
        mod_id = str(item.get("modId") or "").strip()
        name = str(item.get("name") or "").strip()
        version = str(item.get("version") or "").strip()
        result.append({"modId": mod_id, "name": name, "version": version})
    return result


def make_vanilla_profile(
    source_profile: Path,
    destination: Path,
    *,
    scenario_id: str = DEFAULT_VANILLA_SCENARIO,
) -> int:
    """Create a fresh addon-free profile while preserving safe host settings."""
    if destination.exists() or destination.is_symlink():
        raise CompatibilityConfigError(
            f"Refusing to overwrite an existing vanilla profile: {destination}"
        )
    try:
        source = load_config(source_profile / "config.json")
    except ConfigError as exc:
        raise CompatibilityConfigError(f"Could not read source config: {exc}") from exc
    game = source.get("game")
    if not isinstance(game, dict):
        raise CompatibilityConfigError("Source config is missing the game object.")

    disabled_count = len(game.get("mods", [])) if isinstance(game.get("mods", []), list) else 0
    vanilla = deepcopy(source)
    vanilla_game = vanilla["game"]
    vanilla_game["scenarioId"] = scenario_id
    vanilla_game["mods"] = []

    name = str(vanilla_game.get("name") or "Arma Reforger")
    name = re.sub(r"(?: \[vanilla compatibility\])+\Z", "", name)
    vanilla_game["name"] = f"{name}{VANILLA_NAME_SUFFIX}"[:MAX_SERVER_NAME_LENGTH]

    properties = vanilla_game.get("gameProperties")
    if not isinstance(properties, dict):
        properties = {}
        vanilla_game["gameProperties"] = properties
    # A modded persistent save can reference entities absent from vanilla. Newer
    # Reforger schemas use an object here; older schemas accepted a boolean.
    persistence = properties.get("persistence")
    if isinstance(persistence, dict):
        persistence["loadSessionSave"] = False
    else:
        properties["persistence"] = False

    destination.mkdir(parents=True, mode=0o700)
    (destination / "addons").mkdir(mode=0o700)
    _write_json(destination / "config.json", vanilla)
    return disabled_count


def _copy_profile_config(source: Path, destination: Path) -> None:
    for current, dir_names, file_names in os.walk(source, followlinks=False):
        current_path = Path(current)
        if current_path == source:
            dir_names[:] = [name for name in dir_names if name != "addons"]
        for name in [*dir_names, *file_names]:
            if (current_path / name).is_symlink():
                raise CompatibilityConfigError(
                    f"Configuration baseline refuses symlinked content: "
                    f"{current_path / name}"
                )

    def ignore(current: str, names: list[str]) -> set[str]:
        if Path(current).resolve(strict=False) == source.resolve(strict=False):
            return {"addons"} if "addons" in names else set()
        return set()

    shutil.copytree(
        source,
        destination,
        symlinks=False,
        copy_function=shutil.copy2,
        ignore=ignore,
    )


def _copy_addon_metadata(addons: Path, destination: Path) -> int:
    copied = 0
    if not addons.is_dir() or addons.is_symlink():
        return copied
    for source in addons.rglob("*"):
        if source.is_symlink() or not source.is_file():
            continue
        if source.name in _BACKUP_EXCLUDED_NAMES or source.suffix.casefold() == ".pak":
            continue
        if source.suffix.casefold() not in _BACKUP_METADATA_SUFFIXES:
            continue
        try:
            if source.stat().st_size > _MAX_BACKUP_METADATA_FILE_BYTES:
                continue
        except OSError:
            continue
        relative = source.relative_to(addons)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied += 1
    return copied


def _harden_backup_tree(root: Path) -> None:
    for current, dir_names, file_names in os.walk(root, followlinks=False):
        current_path = Path(current)
        os.chmod(current_path, 0o700)
        for name in dir_names:
            target = current_path / name
            if not target.is_symlink():
                os.chmod(target, 0o700)
        for name in file_names:
            target = current_path / name
            if not target.is_symlink():
                os.chmod(target, 0o600)


def _config_sha256(config_file: Path) -> str:
    digest = hashlib.sha256()
    with config_file.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _next_baseline_dir(instance_root: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    parent = instance_root / "backups" / "update-baselines"
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    candidate = parent / timestamp
    suffix = 1
    while candidate.exists():
        candidate = parent / f"{timestamp}-{suffix}"
        suffix += 1
    return candidate


def create_update_baseline(
    instance_root: Path,
    active_profile: Path,
    *,
    parked_modded_profile: Path | None = None,
    active_build: str = "",
) -> Path:
    """Create a compact immutable-before-update config and metadata baseline."""
    root = _next_baseline_dir(instance_root)
    root.mkdir(mode=0o700)
    try:
        active_destination = root / "profile-config"
        _copy_profile_config(active_profile, active_destination)
        addon_names = sorted(
            item.name
            for item in (active_profile / "addons").iterdir()
            if item.is_dir() and not item.is_symlink()
        ) if (active_profile / "addons").is_dir() else []
        (root / "addon-directories.txt").write_text(
            "".join(f"{name}\n" for name in addon_names),
            encoding="utf-8",
        )
        metadata_count = _copy_addon_metadata(
            active_profile / "addons", root / "addon-metadata"
        )

        parked_present = bool(
            parked_modded_profile is not None and parked_modded_profile.is_dir()
        )
        parked_mods: list[dict[str, str]] = []
        if parked_present and parked_modded_profile is not None:
            _copy_profile_config(
                parked_modded_profile,
                root / "parked-modded-profile-config",
            )
            parked_mods = configured_mods(parked_modded_profile)
            _copy_addon_metadata(
                parked_modded_profile / "addons",
                root / "parked-modded-addon-metadata",
            )

        config_hash = _config_sha256(active_profile / "config.json")
        (root / "config.json.sha256").write_text(
            f"{config_hash}  profile-config/config.json\n",
            encoding="ascii",
        )
        config = load_config(active_profile / "config.json")
        game = config.get("game") if isinstance(config.get("game"), dict) else {}
        manifest = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "active_build": active_build,
            "active_profile": str(active_profile),
            "active_config_sha256": config_hash,
            "active_scenario": str(game.get("scenarioId") or ""),
            "active_mods": configured_mods(active_profile),
            "active_addon_directories": len(addon_names),
            "active_addon_metadata_files": metadata_count,
            "parked_modded_profile": (
                str(parked_modded_profile) if parked_present else ""
            ),
            "parked_mods": parked_mods,
            "payload_note": (
                "Workshop .pak payload is not duplicated here. The canonical shared "
                "config/addons pool remains in place during profile switching."
            ),
        }
        _write_json(root / "manifest.json", manifest)
        _harden_backup_tree(root)
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise
    return root


def read_compatibility_status(
    metadata_path: Path,
    parked_modded_profile: Path,
    *,
    active_build: str = "",
    rollback_available: bool = False,
) -> CompatibilityStatus:
    """Load conservative operator state, inferring vanilla from a parked profile."""
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}

    parked = parked_modded_profile.is_dir() and not parked_modded_profile.is_symlink()
    configured_mode = str(metadata.get("active_mode") or "")
    active_mode = (
        configured_mode
        if configured_mode in VALID_MODES
        else (VANILLA_MODE if parked else MODDED_MODE)
    )
    disabled_count = metadata.get("disabled_mod_count", 0)
    if not isinstance(disabled_count, int) or disabled_count < 0:
        disabled_count = 0
    return CompatibilityStatus(
        active_mode=active_mode,
        active_build=active_build or str(metadata.get("active_build") or ""),
        parked_modded_profile=str(parked_modded_profile),
        parked_modded_available=parked,
        rollback_available=rollback_available,
        fallback_scenario=str(
            metadata.get("fallback_scenario") or DEFAULT_VANILLA_SCENARIO
        ),
        disabled_mod_count=disabled_count,
        last_modded_failure=str(metadata.get("last_modded_failure") or ""),
        baseline_path=str(metadata.get("baseline_path") or ""),
        phase=str(metadata.get("phase") or "unmanaged"),
    )
