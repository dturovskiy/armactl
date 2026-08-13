"""Evidence-backed Workshop mod compatibility records per server build/profile."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from armactl.redaction import redact_sensitive_text
from armactl.update_compatibility import configured_mods

COMPATIBLE = "compatible"
INCOMPATIBLE = "incompatible"
STACK_UNKNOWN = "stack_unknown"
BLOCKED_DEPENDENCY = "blocked_dependency"
NOT_TESTED = "not_tested"
VALID_STATUSES = frozenset(
    {COMPATIBLE, INCOMPATIBLE, STACK_UNKNOWN, BLOCKED_DEPENDENCY, NOT_TESTED}
)
MAX_RESULTS = 50
MAX_REASON_LENGTH = 500


@dataclass(frozen=True)
class ModCompatibility:
    status: str
    build_id: str
    profile: str
    tested_at: str
    evidence: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "status": self.status,
            "build_id": self.build_id,
            "profile": self.profile,
            "tested_at": self.tested_at,
            "evidence": self.evidence,
            "reason": self.reason,
        }


def _mod_key(value: Any) -> str:
    return re.sub(r"[^0-9A-F]", "", str(value or "").upper())


def _load(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "results": []}
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        return {"version": 1, "results": []}
    return payload


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _failure_matches(mods: list[dict[str, str]], reason: str) -> set[str]:
    folded = reason.casefold()
    id_matches = {
        _mod_key(mod.get("modId"))
        for mod in mods
        if _mod_key(mod.get("modId")) and _mod_key(mod.get("modId")).casefold() in folded
    }
    if id_matches:
        return id_matches
    name_matches = {
        _mod_key(mod.get("modId"))
        for mod in mods
        if len(str(mod.get("name") or "").strip()) >= 4
        and str(mod.get("name") or "").strip().casefold() in folded
    }
    # Name evidence is accepted only when it identifies one configured mod.
    return name_matches if len(name_matches) == 1 else set()


def _profile_dependencies(addons_path: Path) -> dict[str, set[str]]:
    """Read exact Workshop dependency IDs from bounded addon.gproj metadata."""
    if not addons_path.is_dir() or addons_path.is_symlink():
        return {}
    result: dict[str, set[str]] = {}
    for addon_dir in addons_path.iterdir():
        if not addon_dir.is_dir() or addon_dir.is_symlink():
            continue
        match = re.search(r"_([0-9A-Fa-f]{16})\Z", addon_dir.name)
        if match is None:
            continue
        metadata = addon_dir / "addon.gproj"
        try:
            if metadata.stat().st_size > 1024 * 1024:
                continue
            text = metadata.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        dependency_block = re.search(
            r"\bDependencies\s*\{(?P<ids>[^}]*)\}",
            text,
            re.IGNORECASE | re.DOTALL,
        )
        dependencies = (
            {
                item.upper()
                for item in re.findall(
                    r'"([0-9A-Fa-f]{16})"',
                    dependency_block.group("ids"),
                )
            }
            if dependency_block is not None
            else set()
        )
        result[match.group(1).upper()] = dependencies
    return result


def _blocked_mods(
    configured_ids: set[str],
    dependencies: dict[str, set[str]],
    incompatible: set[str],
) -> set[str]:
    blocked: set[str] = set()
    changed = True
    while changed:
        changed = False
        unavailable = incompatible | blocked
        for mod_id in configured_ids - unavailable:
            if dependencies.get(mod_id, set()) & unavailable:
                blocked.add(mod_id)
                changed = True
    return blocked


def record_profile_canary(
    state_path: Path,
    profile_path: Path,
    *,
    build_id: str,
    profile_name: str,
    compatible: bool,
    reason: str = "",
    addons_path: Path | None = None,
) -> None:
    """Record current-build evidence without claiming more than the canary proved."""
    mods = configured_mods(profile_path)
    if not mods:
        return
    safe_reason = redact_sensitive_text(reason)[:MAX_REASON_LENGTH]
    matched = set() if compatible else _failure_matches(mods, safe_reason)
    configured_ids = {_mod_key(mod.get("modId")) for mod in mods}
    blocked = (
        set()
        if compatible or not matched
        else _blocked_mods(
            configured_ids,
            _profile_dependencies(addons_path or profile_path / "addons"),
            matched,
        )
    )
    records: list[dict[str, str]] = []
    for mod in mods:
        mod_id = _mod_key(mod.get("modId"))
        if compatible:
            status = COMPATIBLE
            evidence = "profile_canary_passed"
        elif mod_id in matched:
            status = INCOMPATIBLE
            evidence = "fatal_log_attribution"
        elif mod_id in blocked:
            status = BLOCKED_DEPENDENCY
            evidence = "declared_dependency_incompatible"
        else:
            status = STACK_UNKNOWN
            evidence = "profile_canary_failed_unattributed"
        records.append(
            {
                "mod_id": mod_id,
                "name": str(mod.get("name") or ""),
                "version": str(mod.get("version") or ""),
                "status": status,
                "evidence": evidence,
            }
        )

    payload = _load(state_path)
    prior_results = [
        item
        for item in payload["results"]
        if isinstance(item, dict)
        and not (
            str(item.get("build_id") or "") == str(build_id)
            and str(item.get("profile") or "") == str(profile_name)
        )
    ]
    config = json.loads((profile_path / "config.json").read_text(encoding="utf-8"))
    game = config.get("game") if isinstance(config.get("game"), dict) else {}
    result = {
        "build_id": str(build_id),
        "profile": str(profile_name),
        "tested_at": datetime.now(timezone.utc).isoformat(),
        "scenario_id": str(game.get("scenarioId") or ""),
        "stack_status": COMPATIBLE if compatible else INCOMPATIBLE,
        "reason": safe_reason,
        "mods": records,
    }
    payload = {"version": 1, "results": [*prior_results, result][-MAX_RESULTS:]}
    _write(state_path, payload)


def compatibility_for_mods(
    state_path: Path,
    mods: list[dict[str, Any]],
    *,
    build_id: str,
    profile_name: str,
) -> dict[str, ModCompatibility]:
    """Return latest exact build/profile evidence for requested mod IDs."""
    payload = _load(state_path)
    result_record: dict[str, Any] | None = None
    for item in reversed(payload["results"]):
        if not isinstance(item, dict):
            continue
        if (
            str(item.get("build_id") or "") == str(build_id)
            and str(item.get("profile") or "") == str(profile_name)
        ):
            result_record = item
            break
    evidence_by_id: dict[str, dict[str, Any]] = {}
    if result_record is not None:
        raw_records = result_record.get("mods", [])
        if isinstance(raw_records, list):
            evidence_by_id = {
                _mod_key(item.get("mod_id")): item
                for item in raw_records
                if isinstance(item, dict) and _mod_key(item.get("mod_id"))
            }

    output: dict[str, ModCompatibility] = {}
    for mod in mods:
        mod_id = _mod_key(mod.get("modId") or mod.get("mod_id"))
        record = evidence_by_id.get(mod_id)
        current_version = str(mod.get("version") or "")
        recorded_version = str(record.get("version") or "") if record else ""
        if record is None or (
            current_version and recorded_version and current_version != recorded_version
        ):
            output[mod_id] = ModCompatibility(
                status=NOT_TESTED,
                build_id=str(build_id),
                profile=str(profile_name),
                tested_at="",
                evidence="no_exact_canary_evidence",
                reason="",
            )
            continue
        status = str(record.get("status") or NOT_TESTED)
        if status not in VALID_STATUSES:
            status = NOT_TESTED
        output[mod_id] = ModCompatibility(
            status=status,
            build_id=str(build_id),
            profile=str(profile_name),
            tested_at=str(result_record.get("tested_at") or ""),
            evidence=str(record.get("evidence") or ""),
            reason=str(result_record.get("reason") or "")[:MAX_REASON_LENGTH],
        )
    return output
