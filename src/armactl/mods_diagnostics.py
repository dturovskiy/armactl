"""Read-only diagnostics for active, disabled, and cached Workshop mods."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from armactl.addon_cleanup import (
    extract_mod_id_from_addon_dir_name,
    normalize_mod_id,
    resolve_safe_addons_dir,
)
from armactl.config_manager import load_config
from armactl.mods_state import load_disabled_mods

KNOWN_DISABLED_MOD_MODULE_NAMES: Mapping[str, tuple[str, ...]] = {
    "65AD7C75826B46C6": ("ACE_Radio_SettingsModule",),
    "667B230F9505C8BA": ("ACE_Weather_SettingsModule",),
}

PROFILE_SETTINGS_CANDIDATES = (
    Path("profile/.save/settings/ReforgerGameSettings.conf"),
    Path(".save/settings/ReforgerGameSettings.conf"),
    Path("settings/ReforgerGameSettings.conf"),
)


@dataclass(frozen=True)
class DiagnosticModEntry:
    mod_id: str
    name: str = ""
    version: str = ""


@dataclass(frozen=True)
class DiagnosticMessage:
    level: str
    code: str
    message: str
    mod_id: str = ""
    name: str = ""


@dataclass(frozen=True)
class DisabledAddonDir:
    mod_id: str
    name: str
    addon_dir: str


@dataclass(frozen=True)
class ProfileSettingsReference:
    mod_id: str
    name: str
    module_name: str
    source: str


@dataclass(frozen=True)
class ModDiagnostics:
    active_config_count: int
    disabled_sidecar_count: int
    disabled_sidecar: tuple[DiagnosticModEntry, ...] = ()
    overlap_active_disabled: tuple[DiagnosticModEntry, ...] = ()
    installed_addon_dirs_count: int = 0
    disabled_addon_dirs_present: tuple[DisabledAddonDir, ...] = ()
    stale_profile_settings_references: tuple[ProfileSettingsReference, ...] = ()
    warnings: tuple[DiagnosticMessage, ...] = ()
    info: tuple[DiagnosticMessage, ...] = ()
    errors: tuple[DiagnosticMessage, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON/template friendly representation."""
        return asdict(self)


def _mod_entry(raw: Any) -> DiagnosticModEntry:
    if not isinstance(raw, dict):
        return DiagnosticModEntry(str(raw or "").strip())
    return DiagnosticModEntry(
        mod_id=str(raw.get("modId") or raw.get("mod_id") or "").strip().upper(),
        name=str(raw.get("name") or "").strip(),
        version=str(raw.get("version") or "").strip(),
    )


def _normalized_entries(raw_mods: Iterable[Any]) -> tuple[DiagnosticModEntry, ...]:
    return tuple(_mod_entry(raw) for raw in raw_mods)


def _entries_by_mod_id(entries: Iterable[DiagnosticModEntry]) -> dict[str, DiagnosticModEntry]:
    indexed: dict[str, DiagnosticModEntry] = {}
    for entry in entries:
        mod_id = normalize_mod_id(entry.mod_id)
        if mod_id and mod_id not in indexed:
            indexed[mod_id] = DiagnosticModEntry(mod_id, entry.name, entry.version)
    return indexed


def _load_active_mods(config_path: Path | str) -> list[Any]:
    config = load_config(config_path)
    game = config.get("game", {})
    if not isinstance(game, dict):
        return []
    mods = game.get("mods", [])
    if not isinstance(mods, list):
        return []
    return mods


def _safe_addon_dirs(config_path: Path | str) -> tuple[list[Path], DiagnosticMessage | None]:
    try:
        addons_dir = resolve_safe_addons_dir(config_path)
    except ValueError:
        return [], DiagnosticMessage(
            "warning",
            "addons_dir_unavailable",
            "Addon directory scan is unavailable.",
        )

    if not addons_dir.exists():
        return [], None
    if not addons_dir.is_dir() or addons_dir.is_symlink():
        return [], DiagnosticMessage(
            "warning",
            "addons_dir_unavailable",
            "Addon directory scan is unavailable.",
        )

    try:
        entries = [
            entry
            for entry in sorted(addons_dir.iterdir(), key=lambda item: item.name.casefold())
            if entry.is_dir() and not entry.is_symlink()
        ]
    except OSError:
        return [], DiagnosticMessage(
            "warning",
            "addons_dir_unavailable",
            "Addon directory scan is unavailable.",
        )

    return entries, None


def _disabled_addon_dirs(
    addon_dirs: Iterable[Path],
    disabled_by_id: Mapping[str, DiagnosticModEntry],
) -> tuple[DisabledAddonDir, ...]:
    matches: list[DisabledAddonDir] = []
    for entry in addon_dirs:
        mod_id = extract_mod_id_from_addon_dir_name(entry.name)
        if not mod_id or mod_id not in disabled_by_id:
            continue
        disabled_entry = disabled_by_id[mod_id]
        matches.append(DisabledAddonDir(mod_id, disabled_entry.name, entry.name))
    return tuple(matches)


def _is_candidate_file_under_config(candidate: Path, config_dir: Path) -> bool:
    if candidate.is_symlink():
        return False
    try:
        candidate.resolve(strict=False).relative_to(config_dir.resolve(strict=True))
    except (OSError, ValueError):
        return False
    return candidate.is_file()


def _profile_settings_references(
    config_path: Path | str,
    disabled_by_id: Mapping[str, DiagnosticModEntry],
    known_modules: Mapping[str, tuple[str, ...]],
) -> tuple[ProfileSettingsReference, ...]:
    config_dir = Path(config_path).parent
    references: list[ProfileSettingsReference] = []
    for relative_path in PROFILE_SETTINGS_CANDIDATES:
        candidate = config_dir / relative_path
        if not _is_candidate_file_under_config(candidate, config_dir):
            continue
        try:
            text = candidate.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for mod_id, disabled_entry in disabled_by_id.items():
            for module_name in known_modules.get(mod_id, ()):
                if module_name not in text:
                    continue
                references.append(
                    ProfileSettingsReference(
                        mod_id,
                        disabled_entry.name,
                        module_name,
                        relative_path.as_posix(),
                    )
                )
    return tuple(references)


def collect_mod_diagnostics(
    config_path: Path | str,
    *,
    active_mods: Iterable[Any] | None = None,
    disabled_mods: Iterable[Any] | None = None,
    known_modules: Mapping[str, tuple[str, ...]] = KNOWN_DISABLED_MOD_MODULE_NAMES,
) -> ModDiagnostics:
    """Return read-only diagnostics for disabled mod state and local residue."""
    active_entries = _normalized_entries(
        active_mods if active_mods is not None else _load_active_mods(config_path)
    )
    disabled_entries = _normalized_entries(
        disabled_mods if disabled_mods is not None else load_disabled_mods(config_path)
    )
    active_by_id = _entries_by_mod_id(active_entries)
    disabled_by_id = _entries_by_mod_id(disabled_entries)
    overlap_ids = sorted(active_by_id.keys() & disabled_by_id.keys())
    overlap = tuple(disabled_by_id[mod_id] for mod_id in overlap_ids)

    addon_dirs, addon_error = _safe_addon_dirs(config_path)
    disabled_dirs = _disabled_addon_dirs(addon_dirs, disabled_by_id)
    profile_refs = _profile_settings_references(config_path, disabled_by_id, known_modules)

    warnings: list[DiagnosticMessage] = []
    info: list[DiagnosticMessage] = []
    errors: list[DiagnosticMessage] = []

    for entry in overlap:
        warnings.append(
            DiagnosticMessage(
                "warning",
                "active_disabled_overlap",
                "Disabled mod is still present in game.mods.",
                entry.mod_id,
                entry.name,
            )
        )

    for reference in profile_refs:
        warnings.append(
            DiagnosticMessage(
                "warning",
                "disabled_profile_settings_reference",
                "Profile settings still reference a known module from a disabled mod.",
                reference.mod_id,
                reference.name,
            )
        )

    for addon_dir in disabled_dirs:
        info.append(
            DiagnosticMessage(
                "info",
                "disabled_addon_dir_present",
                "Local addon files for a disabled mod are present on disk.",
                addon_dir.mod_id,
                addon_dir.name,
            )
        )

    if addon_error is not None:
        errors.append(addon_error)

    return ModDiagnostics(
        active_config_count=len(active_entries),
        disabled_sidecar_count=len(disabled_entries),
        disabled_sidecar=disabled_entries,
        overlap_active_disabled=overlap,
        installed_addon_dirs_count=len(addon_dirs),
        disabled_addon_dirs_present=disabled_dirs,
        stale_profile_settings_references=profile_refs,
        warnings=tuple(warnings),
        info=tuple(info),
        errors=tuple(errors),
    )
