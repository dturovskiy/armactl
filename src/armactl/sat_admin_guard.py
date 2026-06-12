"""Guard ServerAdminTools admin roles from default/runtime resets."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from armactl.admins_manager import (
    admins_state_path_for_config,
    get_admins,
    load_admins,
    merge_admins,
)
from armactl.config_manager import ConfigError, load_config
from armactl.redaction import redact_sensitive_text

SAT_CONFIG_FILENAME = "ServerAdminTools_Config.json"
SAT_UUID_MAP_FILENAME = "sat-admin-uuid-map.json"
ZERO_UUID = "00000000-0000-0000-0000-000000000000"
ADMIN_PLACEHOLDERS = frozenset({ZERO_UUID})
GAMEMASTER_PLACEHOLDERS = frozenset({"example", ZERO_UUID})
BANS_PLACEHOLDERS = frozenset({"example", "example-ban", ZERO_UUID})


class SatAdminGuardError(RuntimeError):
    """Raised when the SAT admin guard cannot complete a required repair."""


@dataclass(frozen=True)
class SatAdminInspection:
    """Read-only status for ServerAdminTools admin role health."""

    available: bool
    valid_json: bool
    sat_config_path: str
    uuid_map_path: str
    desired_admins: tuple[str, ...]
    missing_mappings: tuple[str, ...]
    default_only_admins: bool = False
    default_only_game_masters: bool = False
    missing_admins: tuple[str, ...] = ()
    missing_game_masters: tuple[str, ...] = ()
    warning: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-friendly inspection data."""
        return asdict(self)


@dataclass(frozen=True)
class SatAdminGuardResult:
    """Result of a pre-start SAT admin guard run."""

    checked: bool
    changed: bool
    sat_config_path: str
    uuid_map_path: str
    backup_path: str = ""
    desired_admins: tuple[str, ...] = ()
    missing_mappings: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-friendly guard data."""
        return asdict(self)


def sat_uuid_map_path_for_config(config_path: Path | str) -> Path:
    """Return the SAT UUID map path for an instance config.json path."""
    return admins_state_path_for_config(config_path).with_name(SAT_UUID_MAP_FILENAME)


def sat_config_path_for_config(config_path: Path | str) -> Path:
    """Return the expected ServerAdminTools runtime config path."""
    return Path(config_path).parent / SAT_CONFIG_FILENAME


def _canonical_uuid(value: object) -> str | None:
    raw = str(value or "").strip().strip("{}")
    if not raw:
        return None
    try:
        parsed = uuid.UUID(raw)
    except ValueError:
        return None
    return str(parsed)


def _is_zero_uuid(value: object) -> bool:
    return _canonical_uuid(value) == ZERO_UUID


def _map_key(value: object) -> str:
    return str(value or "").strip().casefold()


def _iter_uuid_map_entries(payload: Any) -> list[tuple[str, str]]:
    if isinstance(payload, dict):
        raw_entries = payload.get("identities", payload.get("players", payload))
        if isinstance(raw_entries, dict):
            return [(str(key), str(value)) for key, value in raw_entries.items()]
        payload = raw_entries

    if isinstance(payload, list):
        entries: list[tuple[str, str]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            uuid_value = item.get("uuid") or item.get("identityId") or item.get("satUuid")
            for key_name in ("name", "label", "steamId64", "source", "id"):
                key_value = item.get(key_name)
                if key_value and uuid_value:
                    entries.append((str(key_value), str(uuid_value)))
            if uuid_value:
                entries.append((str(uuid_value), str(uuid_value)))
        return entries

    return []


def load_sat_uuid_map(config_path: Path | str) -> dict[str, str]:
    """Load optional SAT UUID map for SteamID/name to SAT UUID conversion."""
    map_path = sat_uuid_map_path_for_config(config_path)
    if not map_path.is_file():
        return {}

    try:
        payload = json.loads(map_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SatAdminGuardError(f"Invalid JSON in {map_path}: {exc}") from exc
    except OSError as exc:
        raise SatAdminGuardError(f"Failed to read {map_path}: {exc}") from exc

    mapping: dict[str, str] = {}
    for raw_key, raw_uuid in _iter_uuid_map_entries(payload):
        key = _map_key(raw_key)
        sat_uuid = _canonical_uuid(raw_uuid)
        if key and sat_uuid and sat_uuid != ZERO_UUID:
            mapping[key] = sat_uuid
    return mapping


def _admin_lookup_keys(entry: dict[str, str]) -> list[str]:
    keys = [
        entry.get("identityId", ""),
        entry.get("name", ""),
        entry.get("source", ""),
    ]
    return [_map_key(key) for key in keys if _map_key(key)]


def _server_admin_entries_for_sat(config_path: Path | str) -> list[dict[str, str]]:
    config = load_config(config_path)
    game = config.get("game", {})
    if not isinstance(game, dict):
        raise ConfigError("game section must be a JSON object.")

    raw_admins = game.get("admins", [])
    if raw_admins in (None, ""):
        raw_admins = []
    if not isinstance(raw_admins, list):
        raise ConfigError("game.admins must be a JSON list.")

    entries: list[dict[str, str]] = []
    for raw in raw_admins:
        if isinstance(raw, dict):
            identity = (
                raw.get("identityId")
                or raw.get("steamId64")
                or raw.get("steamid")
                or raw.get("playerId")
                or raw.get("uid")
                or raw.get("id")
                or ""
            )
            name = raw.get("name") or raw.get("displayName") or ""
            source = raw.get("source") or "game.admins"
        else:
            identity = raw
            name = ""
            source = "game.admins"
        entries.append(
            {
                "identityId": str(identity or "").strip(),
                "name": str(name or "").strip(),
                "source": str(source or "").strip(),
            }
        )
    return merge_admins(entries, load_admins(config_path))


def _desired_sat_admins(
    config_path: Path | str,
    *,
    migrate: bool,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    try:
        official_admins = (
            get_admins(config_path) if migrate else _server_admin_entries_for_sat(config_path)
        )
    except ConfigError as exc:
        raise SatAdminGuardError(str(exc)) from exc

    uuid_map = load_sat_uuid_map(config_path)
    desired: list[str] = []
    missing: list[str] = []
    seen: set[str] = set()

    for entry in official_admins:
        identity = str(entry.get("identityId", "")).strip()
        sat_uuid = _canonical_uuid(identity)
        if sat_uuid == ZERO_UUID:
            sat_uuid = None

        if sat_uuid is None:
            for key in _admin_lookup_keys(entry):
                sat_uuid = uuid_map.get(key)
                if sat_uuid:
                    break

        if sat_uuid:
            if sat_uuid not in seen:
                desired.append(sat_uuid)
                seen.add(sat_uuid)
            continue

        label = str(entry.get("name") or identity or entry.get("source") or "unknown").strip()
        if label:
            missing.append(label)

    return tuple(desired), tuple(missing)


def _read_json_file(path: Path) -> tuple[dict[str, Any] | None, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, f"Invalid JSON: {exc}"
    except OSError as exc:
        raise SatAdminGuardError(f"Failed to read {path}: {exc}") from exc

    if not isinstance(payload, dict):
        return None, "SAT config root must be a JSON object."
    return payload, ""


def _normalized_values(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _placeholder_only(values: list[str], placeholders: frozenset[str]) -> bool:
    if not values:
        return False
    normalized = {_map_key(value) for value in values}
    return bool(normalized) and normalized <= {_map_key(value) for value in placeholders}


def _clean_role_values(values: list[str], placeholders: frozenset[str]) -> list[str]:
    placeholder_keys = {_map_key(value) for value in placeholders}
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = _map_key(value)
        if not key or key in placeholder_keys:
            continue
        canonical = _canonical_uuid(value) or value
        dedupe_key = _map_key(canonical)
        if dedupe_key in seen:
            continue
        cleaned.append(canonical)
        seen.add(dedupe_key)
    return cleaned


def _merge_role_values(
    current: Any,
    desired: tuple[str, ...],
    *,
    placeholders: frozenset[str],
) -> tuple[list[str], bool, bool]:
    values = _normalized_values(current)
    default_only = _placeholder_only(values, placeholders)
    existing = [] if default_only else _clean_role_values(values, placeholders)
    merged = list(existing)
    seen = {_map_key(value) for value in merged}
    changed = not isinstance(current, list) or default_only or len(existing) != len(values)

    for sat_uuid in desired:
        key = _map_key(sat_uuid)
        if key in seen:
            continue
        merged.append(sat_uuid)
        seen.add(key)
        changed = True

    return merged, changed, default_only


def _backup_path(path: Path) -> Path:
    timestamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    candidate = path.with_name(f"{path.name}.before-sat-admin-guard-{timestamp}.bak")
    suffix = 1
    while candidate.exists():
        candidate = path.with_name(
            f"{path.name}.before-sat-admin-guard-{timestamp}.{suffix}.bak"
        )
        suffix += 1
    return candidate


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=4) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp_path, path)
    except OSError as exc:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise SatAdminGuardError(f"Failed to save {path}: {exc}") from exc


def inspect_sat_admin_config(
    config_path: Path | str,
    *,
    sat_config_path: Path | str | None = None,
) -> SatAdminInspection:
    """Inspect SAT role health without modifying files."""
    config = Path(config_path)
    sat_path = (
        Path(sat_config_path)
        if sat_config_path is not None
        else sat_config_path_for_config(config)
    )
    map_path = sat_uuid_map_path_for_config(config)
    desired, missing_mappings = _desired_sat_admins(config, migrate=False)

    if not sat_path.is_file():
        return SatAdminInspection(
            available=False,
            valid_json=False,
            sat_config_path=str(sat_path),
            uuid_map_path=str(map_path),
            desired_admins=desired,
            missing_mappings=missing_mappings,
        )

    payload, error = _read_json_file(sat_path)
    if payload is None:
        return SatAdminInspection(
            available=True,
            valid_json=False,
            sat_config_path=str(sat_path),
            uuid_map_path=str(map_path),
            desired_admins=desired,
            missing_mappings=missing_mappings,
            warning=f"ServerAdminTools config is invalid: {redact_sensitive_text(error)}",
        )

    admins = _normalized_values(payload.get("admins"))
    game_masters = _normalized_values(payload.get("gameMasters"))
    default_only_admins = _placeholder_only(admins, ADMIN_PLACEHOLDERS)
    default_only_game_masters = _placeholder_only(game_masters, GAMEMASTER_PLACEHOLDERS)
    admin_keys = {_map_key(_canonical_uuid(value) or value) for value in admins}
    gm_keys = {_map_key(_canonical_uuid(value) or value) for value in game_masters}
    missing_admins = tuple(value for value in desired if _map_key(value) not in admin_keys)
    missing_game_masters = tuple(value for value in desired if _map_key(value) not in gm_keys)

    warning = ""
    if default_only_admins or default_only_game_masters:
        warning = "ServerAdminTools admins are default/example only."
    elif missing_admins or missing_game_masters:
        warning = "ServerAdminTools admin roles are missing required UUIDs."
    elif missing_mappings:
        warning = "ServerAdminTools UUID map is missing entries for official admins."

    return SatAdminInspection(
        available=True,
        valid_json=True,
        sat_config_path=str(sat_path),
        uuid_map_path=str(map_path),
        desired_admins=desired,
        missing_mappings=missing_mappings,
        default_only_admins=default_only_admins,
        default_only_game_masters=default_only_game_masters,
        missing_admins=missing_admins,
        missing_game_masters=missing_game_masters,
        warning=warning,
    )


def guard_sat_admin_config(
    config_path: Path | str,
    *,
    sat_config_path: Path | str | None = None,
) -> SatAdminGuardResult:
    """Repair default/missing SAT admin roles before the server starts."""
    config = Path(config_path)
    sat_path = (
        Path(sat_config_path)
        if sat_config_path is not None
        else sat_config_path_for_config(config)
    )
    map_path = sat_uuid_map_path_for_config(config)
    desired, missing_mappings = _desired_sat_admins(config, migrate=True)
    warnings = list(missing_mappings)

    if not sat_path.is_file():
        return SatAdminGuardResult(
            checked=False,
            changed=False,
            sat_config_path=str(sat_path),
            uuid_map_path=str(map_path),
            desired_admins=desired,
            missing_mappings=missing_mappings,
            warnings=tuple(warnings),
        )

    payload, error = _read_json_file(sat_path)
    invalid = payload is None
    if payload is None:
        if not desired:
            warnings.append(redact_sensitive_text(error))
            return SatAdminGuardResult(
                checked=True,
                changed=False,
                sat_config_path=str(sat_path),
                uuid_map_path=str(map_path),
                desired_admins=desired,
                missing_mappings=missing_mappings,
                warnings=tuple(warnings),
            )
        payload = {}

    changed = invalid
    if desired:
        admins, admins_changed, _admins_default = _merge_role_values(
            payload.get("admins"),
            desired,
            placeholders=ADMIN_PLACEHOLDERS,
        )
        game_masters, game_masters_changed, _gms_default = _merge_role_values(
            payload.get("gameMasters"),
            desired,
            placeholders=GAMEMASTER_PLACEHOLDERS,
        )
        changed = changed or admins_changed or game_masters_changed

        if admins_changed:
            payload["admins"] = admins
        if game_masters_changed:
            payload["gameMasters"] = game_masters

    bans = _normalized_values(payload.get("bans"))
    if _placeholder_only(bans, BANS_PLACEHOLDERS):
        payload["bans"] = []
        changed = True

    if not changed:
        return SatAdminGuardResult(
            checked=True,
            changed=False,
            sat_config_path=str(sat_path),
            uuid_map_path=str(map_path),
            desired_admins=desired,
            missing_mappings=missing_mappings,
            warnings=tuple(warnings),
        )

    try:
        backup = _backup_path(sat_path)
        shutil.copy2(sat_path, backup)
        _write_json_atomic(sat_path, payload)
    except OSError as exc:
        raise SatAdminGuardError(f"Failed to repair {sat_path}: {exc}") from exc

    return SatAdminGuardResult(
        checked=True,
        changed=True,
        sat_config_path=str(sat_path),
        uuid_map_path=str(map_path),
        backup_path=str(backup),
        desired_admins=desired,
        missing_mappings=missing_mappings,
        warnings=tuple(warnings),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Guard ServerAdminTools admin roles.")
    parser.add_argument("--config", required=True, help="Path to armactl config.json.")
    parser.add_argument("--sat-config", help="Override ServerAdminTools config path.")
    parser.add_argument("--check", action="store_true", help="Inspect without changing files.")
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    parser.add_argument("--quiet", action="store_true", help="Suppress no-op output.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point used by generated start scripts."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.check:
            inspection = inspect_sat_admin_config(args.config, sat_config_path=args.sat_config)
            payload = inspection.to_dict()
            warning = inspection.warning
        else:
            result = guard_sat_admin_config(args.config, sat_config_path=args.sat_config)
            payload = result.to_dict()
            warning = "; ".join(result.warnings)
    except SatAdminGuardError as exc:
        if args.json:
            print(json.dumps({"success": False, "error": redact_sensitive_text(exc)}))
        elif not args.quiet:
            print(f"SAT admin guard failed: {redact_sensitive_text(exc)}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif not args.quiet:
        if warning:
            print(f"SAT admin guard warning: {warning}", file=sys.stderr)
        else:
            print("SAT admin guard ok.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
