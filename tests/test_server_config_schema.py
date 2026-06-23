"""Regression tests for shared server config field/default ownership."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from armactl import server_config_schema as schema
from armactl.installer import render_default_config

REPO_ROOT = Path(__file__).resolve().parents[1]

EXPECTED_FULL_EXAMPLE_DIVERGENCES = {
    "game.gameProperties.disableThirdPerson",
    "game.gameProperties.networkViewDistance",
    "game.gameProperties.serverMinGrassDistance",
    "game.maxPlayers",
    "game.mods",
    "game.name",
    "game.passwordAdmin",
    "game.scenarioId",
    "rcon.password",
}
EXPECTED_FULL_EXAMPLE_ONLY_PATHS = {
    "game.admins",
    "game.gameProperties.VONCanTransmitCrossFaction",
    "game.gameProperties.VONDisableDirectSpeechUI",
    "game.gameProperties.VONDisableUI",
    "operating.lobbyPlayerSynchronise",
    "rcon.blacklist",
    "rcon.whitelist",
}
EXPECTED_GENERATED_ONLY_PATHS = {"rcon.maxClients"}


def _has_path(data: dict[str, Any], path: tuple[str, ...]) -> bool:
    value: Any = data
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return False
        value = value[key]
    return True


def _flatten_paths(data: Any, prefix: tuple[str, ...] = ()) -> set[str]:
    if isinstance(data, dict):
        paths: set[str] = set()
        for key, value in data.items():
            paths.update(_flatten_paths(value, (*prefix, key)))
        return paths
    return {".".join(prefix)}


def test_generated_config_template_matches_shared_registry_defaults() -> None:
    rendered = render_default_config(
        rcon_password="generated-rcon-secret",
        password_admin="generated-admin-secret",
    )

    assert json.loads(rendered) == schema.generated_default_config_values(
        rcon_password="generated-rcon-secret",
        password_admin="generated-admin-secret",
    )


def test_full_example_config_is_sample_only_not_runtime_default() -> None:
    example_path = REPO_ROOT / schema.FULL_EXAMPLE_CONFIG_PATH
    legacy_template_path = REPO_ROOT / "templates" / "config.json"

    assert example_path.is_file()
    assert not legacy_template_path.exists()

    sample = json.loads(example_path.read_text(encoding="utf-8"))
    generated = schema.generated_default_config_values(
        rcon_password="generated-rcon-secret",
        password_admin="generated-admin-secret",
    )

    divergences = {
        ".".join(field.config_path)
        for field in schema.generated_default_fields()
        if _has_path(sample, field.config_path)
        and schema.nested_value(sample, field.config_path)
        != schema.nested_value(generated, field.config_path)
    }
    assert divergences == EXPECTED_FULL_EXAMPLE_DIVERGENCES

    sample_paths = _flatten_paths(sample)
    generated_paths = _flatten_paths(generated)
    assert sample_paths - generated_paths == EXPECTED_FULL_EXAMPLE_ONLY_PATHS
    assert generated_paths - sample_paths == EXPECTED_GENERATED_ONLY_PATHS


def test_web_tui_cli_safe_fields_share_registry_paths() -> None:
    web_fields = {field.name: field for field in schema.web_config_field_descriptors()}
    tui_fields = {field.name: field for field in schema.tui_structured_config_fields()}
    cli_fields = {field.name: field for field in schema.cli_compat_config_fields()}

    assert tuple(web_fields) == (
        "name",
        "scenario_id",
        "max_players",
        "visible",
        "disable_third_person",
        "battleye",
        "server_max_view_distance",
        "server_min_grass_distance",
    )
    third_person = web_fields["disable_third_person"]
    assert third_person.config_path == ("game", "gameProperties", "disableThirdPerson")
    assert third_person.value_type == schema.VALUE_BOOLEAN
    assert third_person.generated_default is True
    assert third_person.permission == schema.SETTINGS_MANAGE_PERMISSION
    assert third_person.restart_behavior == schema.RESTART_BEHAVIOR_CHANGED_ONLY
    assert third_person.audit_field_name == "disable_third_person"
    assert third_person.ui is not None
    assert third_person.ui.label == "Disable third-person view"
    assert {"name", "scenario_id", "max_players"} <= tui_fields.keys()
    assert {"name", "scenario_id", "max_players"} <= cli_fields.keys()
    assert "disable_third_person" not in tui_fields
    assert "disable_third_person" not in cli_fields

    for field_name in web_fields.keys() & tui_fields.keys():
        assert tui_fields[field_name].config_path == web_fields[field_name].config_path
    for field_name in web_fields.keys() & cli_fields.keys():
        assert cli_fields[field_name].config_path == web_fields[field_name].config_path


def test_registry_classifies_advanced_and_secret_fields_outside_normal_web() -> None:
    web_names = {field.name for field in schema.web_config_field_descriptors()}

    assert schema.get_config_field("bind_port").risk_class == schema.RISK_ADVANCED
    assert schema.get_config_field("rcon_password").secret_behavior == (
        schema.SECRET_BEHAVIOR_GENERATED_SECRET
    )
    assert schema.get_config_field("password_admin").secret_behavior == (
        schema.SECRET_BEHAVIOR_GENERATED_SECRET
    )
    assert "bind_port" not in web_names
    assert "rcon_password" not in web_names
    assert "password_admin" not in web_names

def test_text_parser_applies_registered_string_validation() -> None:
    assert schema.parse_text_field_value("  Registry Server  ", "name") == "Registry Server"

    for invalid in ("", "bad\tname", "x" * 513):
        with pytest.raises(schema.ServerConfigSchemaError):
            schema.parse_text_field_value(invalid, "name")


def test_text_parser_preserves_secret_string_values() -> None:
    secret = "  secret value with spaces  "

    assert schema.parse_text_field_value(secret, "rcon_password") == secret
