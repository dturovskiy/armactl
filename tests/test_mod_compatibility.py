"""Tests for build/profile-scoped mod compatibility evidence."""

from __future__ import annotations

import json
from pathlib import Path

from armactl import mod_compatibility


def _profile(tmp_path: Path) -> Path:
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "config.json").write_text(
        json.dumps(
            {
                "game": {
                    "scenarioId": "{CUSTOM}Missions/Test.conf",
                    "mods": [
                        {
                            "modId": "0123456789ABCDEF",
                            "name": "WCS Core",
                            "version": "1.2.3",
                        },
                        {
                            "modId": "FEDCBA9876543210",
                            "name": "Map Pack",
                            "version": "4.5.6",
                        },
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    wcs = profile / "addons" / "WCS_Core_0123456789ABCDEF"
    map_pack = profile / "addons" / "Map_Pack_FEDCBA9876543210"
    wcs.mkdir(parents=True)
    map_pack.mkdir(parents=True)
    (wcs / "addon.gproj").write_text(
        'GameProject { GUID "0123456789ABCDEF" Dependencies { } }',
        encoding="utf-8",
    )
    (map_pack / "addon.gproj").write_text(
        'GameProject { GUID "FEDCBA9876543210" '
        'Dependencies { "0123456789ABCDEF" } }',
        encoding="utf-8",
    )
    return profile


def _mods() -> list[dict[str, str]]:
    return [
        {"modId": "0123456789ABCDEF", "name": "WCS Core", "version": "1.2.3"},
        {"modId": "FEDCBA9876543210", "name": "Map Pack", "version": "4.5.6"},
    ]


def test_passed_profile_canary_marks_every_member_compatible(tmp_path: Path):
    profile = _profile(tmp_path)
    state = tmp_path / "mod-compatibility.json"

    mod_compatibility.record_profile_canary(
        state,
        profile,
        build_id="24501482",
        profile_name="zakarpattia",
        compatible=True,
    )
    result = mod_compatibility.compatibility_for_mods(
        state,
        _mods(),
        build_id="24501482",
        profile_name="zakarpattia",
    )

    assert {item.status for item in result.values()} == {mod_compatibility.COMPATIBLE}
    assert {item.evidence for item in result.values()} == {"profile_canary_passed"}


def test_failed_canary_attributes_one_named_mod_without_guessing_others(tmp_path: Path):
    profile = _profile(tmp_path)
    state = tmp_path / "mod-compatibility.json"

    mod_compatibility.record_profile_canary(
        state,
        profile,
        build_id="24501482",
        profile_name="zakarpattia",
        compatible=False,
        reason='WCS Core: Can\'t compile "Game" script module',
    )
    result = mod_compatibility.compatibility_for_mods(
        state,
        _mods(),
        build_id="24501482",
        profile_name="zakarpattia",
    )

    assert result["0123456789ABCDEF"].status == mod_compatibility.INCOMPATIBLE
    assert result["FEDCBA9876543210"].status == mod_compatibility.BLOCKED_DEPENDENCY


def test_evidence_is_not_reused_for_another_build_profile_or_mod_version(tmp_path: Path):
    profile = _profile(tmp_path)
    state = tmp_path / "mod-compatibility.json"
    mod_compatibility.record_profile_canary(
        state,
        profile,
        build_id="24501482",
        profile_name="zakarpattia",
        compatible=True,
    )

    changed = _mods()
    changed[0]["version"] = "2.0.0"
    changed_result = mod_compatibility.compatibility_for_mods(
        state,
        changed,
        build_id="24501482",
        profile_name="zakarpattia",
    )
    other_build = mod_compatibility.compatibility_for_mods(
        state,
        _mods(),
        build_id="24599999",
        profile_name="zakarpattia",
    )
    other_profile = mod_compatibility.compatibility_for_mods(
        state,
        _mods(),
        build_id="24501482",
        profile_name="chervonopillia",
    )

    assert changed_result["0123456789ABCDEF"].status == mod_compatibility.NOT_TESTED
    assert changed_result["FEDCBA9876543210"].status == mod_compatibility.COMPATIBLE
    assert {item.status for item in other_build.values()} == {mod_compatibility.NOT_TESTED}
    assert {item.status for item in other_profile.values()} == {mod_compatibility.NOT_TESTED}
