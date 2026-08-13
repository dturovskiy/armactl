"""Workshop mods page DTO loader."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from armactl import (
    mod_compatibility,
    mods_diagnostics,
    mods_manager,
    mods_state,
    safe_update,
)
from armactl.web.page_models.common import (
    DISABLED_MODS_STATE_DISPLAY,
    UNAVAILABLE_LABEL,
    _discover_management_state,
    _label_display,
    _missing_config_page,
    _paths,
    _safe_error_message,
    _state_status,
)

_COMPATIBILITY_PRESENTATION = {
    mod_compatibility.COMPATIBLE: ("Compatible", "success"),
    mod_compatibility.INCOMPATIBLE: ("Incompatible", "error"),
    mod_compatibility.BLOCKED_DEPENDENCY: ("Blocked by dependency", "warning"),
    mod_compatibility.STACK_UNKNOWN: ("Stack failed; mod unknown", "warning"),
    mod_compatibility.NOT_TESTED: ("Not tested for current build", "unavailable"),
}


def _mod_entry(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        raw = {"modId": str(raw or "").strip()}
    return {
        "mod_id": str(raw.get("modId") or raw.get("mod_id") or "").strip(),
        "name": str(raw.get("name") or "").strip(),
        "version": str(raw.get("version") or "").strip(),
        "compatibility_status": mod_compatibility.NOT_TESTED,
        "compatibility_label": "Not tested for current build",
        "compatibility_css_class": "unavailable",
        "compatibility_build": "",
        "compatibility_profile": "",
        "compatibility_tested_at": "",
        "compatibility_evidence": "",
        "compatibility_reason": "",
    }


def _enrich_compatibility(
    mods: list[dict[str, str]],
    evidence: dict[str, mod_compatibility.ModCompatibility],
) -> None:
    for mod in mods:
        record = evidence.get(mod["mod_id"].upper())
        if record is None:
            continue
        label, css_class = _COMPATIBILITY_PRESENTATION[record.status]
        mod.update(
            {
                "compatibility_status": record.status,
                "compatibility_label": label,
                "compatibility_css_class": css_class,
                "compatibility_build": record.build_id,
                "compatibility_profile": record.profile,
                "compatibility_tested_at": record.tested_at,
                "compatibility_evidence": record.evidence,
                "compatibility_reason": record.reason,
            }
        )


def _inactive_profile_group(
    profile: safe_update.NamedProfile,
    *,
    state_path: Path,
    build_id: str,
    addons_path: Path,
) -> dict[str, Any]:
    raw_mods = safe_update.configured_mods(Path(profile.path))
    mods = [_mod_entry(raw) for raw in raw_mods]
    evidence = mod_compatibility.compatibility_for_mods(
        state_path,
        raw_mods,
        build_id=build_id,
        profile_name=profile.name,
        addons_path=addons_path,
    )
    _enrich_compatibility(mods, evidence)
    return {
        "name": profile.name,
        "mode": profile.mode,
        "scenario_id": profile.scenario_id,
        "mod_count": profile.mod_count,
        "mods": mods,
    }


def load_mods_page(instance: str) -> dict[str, Any]:
    """Return active and disabled mod list details for a web page."""
    state, error_page = _discover_management_state(instance)
    if error_page is not None:
        return error_page
    assert state is not None
    if not state.config_path:
        return _missing_config_page(instance, state, "config path is not available")

    disabled_mods_state = UNAVAILABLE_LABEL
    disabled_mods: list[dict[str, str]] = []
    disabled_mods_error = ""
    diagnostics: dict[str, Any] = {}
    diagnostics_error = ""
    compatibility_build = ""
    compatibility_profile = ""
    compatibility_error = ""
    inactive_profile_groups: list[dict[str, Any]] = []
    try:
        raw_mods = mods_manager.get_mods(state.config_path)
        if not isinstance(raw_mods, list):
            raw_mods = []
        mods = [_mod_entry(raw) for raw in raw_mods]
    except Exception as error:
        return _missing_config_page(instance, state, _safe_error_message(error))

    try:
        state_path = mods_state.mods_state_path_for_config(state.config_path)
        raw_disabled_mods = mods_state.load_disabled_mods(state.config_path)
        disabled_mods_state = _label_display(state_path, DISABLED_MODS_STATE_DISPLAY)
        if not isinstance(raw_disabled_mods, list):
            raw_disabled_mods = []
        disabled_mods = [_mod_entry(raw) for raw in raw_disabled_mods]
    except Exception as error:
        disabled_mods_error = _safe_error_message(error)
        raw_disabled_mods = []

    try:
        install_dir = Path(state.install_dir)
        config_path = Path(state.config_path)
        update_paths = safe_update.resolve_update_paths(install_dir, config_path)
        compatibility_build = safe_update.read_build_id(install_dir)
        compatibility_profile = next(
            (
                profile.name
                for profile in safe_update.get_named_profiles(install_dir, config_path)
                if profile.active
            ),
            "modded",
        )
        all_raw_mods = [*raw_mods, *raw_disabled_mods]
        evidence = mod_compatibility.compatibility_for_mods(
            update_paths.mod_compatibility,
            all_raw_mods,
            build_id=compatibility_build,
            profile_name=compatibility_profile,
            addons_path=update_paths.profile / "addons",
        )
        _enrich_compatibility(mods, evidence)
        _enrich_compatibility(disabled_mods, evidence)

        inactive_profiles = [
            profile
            for profile in safe_update.get_named_profiles(install_dir, config_path)
            if not profile.active and profile.mod_count
        ]
        parked = safe_update.get_parked_modded_profile(install_dir, config_path)
        if parked is not None and parked.mod_count:
            inactive_profiles.append(parked)
        seen_profiles: set[str] = set()
        for profile in inactive_profiles:
            if profile.name in seen_profiles:
                continue
            seen_profiles.add(profile.name)
            inactive_profile_groups.append(
                _inactive_profile_group(
                    profile,
                    state_path=update_paths.mod_compatibility,
                    build_id=compatibility_build,
                    addons_path=update_paths.profile / "addons",
                )
            )
    except Exception as error:
        compatibility_error = _safe_error_message(error)

    try:
        diagnostics = mods_diagnostics.collect_mod_diagnostics(
            state.config_path,
            active_mods=raw_mods,
            disabled_mods=raw_disabled_mods,
        ).to_dict()
    except Exception as error:
        diagnostics_error = _safe_error_message(error)

    return {
        "instance": instance,
        "available": True,
        "error": "",
        "status": _state_status(state),
        "paths": _paths(state),
        "count": len(mods),
        "mods": mods,
        "disabled_count": len(disabled_mods),
        "disabled_mods": disabled_mods,
        "disabled_mods_state": disabled_mods_state,
        "disabled_mods_state_display": disabled_mods_state,
        "disabled_mods_error": disabled_mods_error,
        "compatibility_build": compatibility_build,
        "compatibility_profile": compatibility_profile,
        "compatibility_error": compatibility_error,
        "inactive_profile_groups": inactive_profile_groups,
        "diagnostics": diagnostics,
        "diagnostics_error": diagnostics_error,
    }
