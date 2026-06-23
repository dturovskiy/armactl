from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ServerConfigSchemaError(ValueError):
    pass


@dataclass(frozen=True)
class MissingDefault:
    pass


DEFAULT_MISSING = MissingDefault()
STRING_LIMIT = 512
SETTINGS_MANAGE_PERMISSION = "settings:manage"
MODS_MANAGE_PERMISSION = "mods:manage"
ADMINS_MANAGE_PERMISSION = "admins:manage"

VALUE_STRING = "string"
VALUE_INTEGER = "integer"
VALUE_BOOLEAN = "boolean"
VALUE_LIST = "list"

RISK_SAFE = "safe"
RISK_ADVANCED = "advanced"
RISK_DANGEROUS = "dangerous"
RISK_SECRET = "secret"

PERMISSION_WEB = "web"
PERMISSION_FUTURE_WEB = "future-web"
PERMISSION_DOMAIN = "domain"
PERMISSION_FUTURE_POLICY = "future-policy"

SECRET_BEHAVIOR_NOT_SECRET = "not-secret"
SECRET_BEHAVIOR_SECRET = "secret"
SECRET_BEHAVIOR_GENERATED_SECRET = "generated-secret"

RESTART_BEHAVIOR_CHANGED_ONLY = "changed-values-mark-restart-pending"
RESTART_BEHAVIOR_TUI_OPTIONAL = "tui-save-or-save-and-restart"
RESTART_BEHAVIOR_MANAGED_FLOW = "managed-flow-marks-restart-pending"
RESTART_BEHAVIOR_UNKNOWN = "unknown"

UI_GROUP_FORM_GRID = "form_grid"
UI_GROUP_CHECKBOX_GRID = "checkbox_grid"

DEFAULT_CONFIG_TEMPLATE_NAME = "config.json.j2"
FULL_EXAMPLE_CONFIG_PATH = Path("docs/examples/config.full-example.json")


@dataclass(frozen=True)
class ServerConfigFieldUi:
    label: str
    control: str
    group: str
    css_class: str = ""
    required: bool = False
    max_length: int | None = None
    min_value: int | None = None
    step: int | None = None
    helper_text: str = ""


@dataclass(frozen=True)
class ServerConfigField:
    name: str
    config_path: tuple[str, ...]
    value_type: str
    validation_message: str
    risk_class: str
    permission_class: str
    secret_behavior: str
    restart_behavior: str
    audit_field_name: str | None = None
    permission_name: str | None = None
    generated_default: Any = DEFAULT_MISSING
    template_variable: str | None = None
    generated_secret_variable: str | None = None
    required: bool = False
    minimum: int | None = None
    maximum: int | None = None
    max_length: int | None = None
    ui: ServerConfigFieldUi | None = None
    web_editable: bool = False
    tui_input_id: str | None = None
    cli_command: str | None = None
    linked_config_paths: tuple[tuple[str, ...], ...] = ()

    @property
    def form_name(self) -> str:
        return self.name

    @property
    def permission(self) -> str:
        return self.permission_name or ""


F = ServerConfigField
U = ServerConfigFieldUi
SAFE_WEB = dict(
    risk_class=RISK_SAFE,
    permission_class=PERMISSION_WEB,
    permission_name=SETTINGS_MANAGE_PERMISSION,
    secret_behavior=SECRET_BEHAVIOR_NOT_SECRET,
    restart_behavior=RESTART_BEHAVIOR_CHANGED_ONLY,
    web_editable=True,
)
ADVANCED_FUTURE = dict(
    risk_class=RISK_ADVANCED,
    permission_class=PERMISSION_FUTURE_WEB,
    secret_behavior=SECRET_BEHAVIOR_NOT_SECRET,
    restart_behavior=RESTART_BEHAVIOR_UNKNOWN,
)

SERVER_CONFIG_FIELDS: tuple[ServerConfigField, ...] = (
    F(
        "bind_address",
        ("bindAddress",),
        VALUE_STRING,
        "bindAddress must be a string.",
        RISK_DANGEROUS,
        PERMISSION_FUTURE_WEB,
        SECRET_BEHAVIOR_NOT_SECRET,
        RESTART_BEHAVIOR_UNKNOWN,
        generated_default="0.0.0.0",
        template_variable="bind_address",
        required=True,
    ),
    F(
        "bind_port",
        ("bindPort",),
        VALUE_INTEGER,
        "bindPort must be an integer between 1 and 65535.",
        RISK_ADVANCED,
        PERMISSION_FUTURE_WEB,
        SECRET_BEHAVIOR_NOT_SECRET,
        RESTART_BEHAVIOR_TUI_OPTIONAL,
        generated_default=2001,
        template_variable="bind_port",
        required=True,
        minimum=1,
        maximum=65535,
        tui_input_id="inp_game_port",
        linked_config_paths=(("publicPort",),),
    ),
    F(
        "public_address",
        ("publicAddress",),
        VALUE_STRING,
        "publicAddress must be a string.",
        **ADVANCED_FUTURE,
        generated_default="",
        template_variable="public_address",
    ),
    F(
        "public_port",
        ("publicPort",),
        VALUE_INTEGER,
        "publicPort must be an integer between 1 and 65535.",
        RISK_ADVANCED,
        PERMISSION_FUTURE_WEB,
        SECRET_BEHAVIOR_NOT_SECRET,
        RESTART_BEHAVIOR_TUI_OPTIONAL,
        generated_default=2001,
        template_variable="public_port",
        minimum=1,
        maximum=65535,
    ),
    F(
        "a2s_address",
        ("a2s", "address"),
        VALUE_STRING,
        "a2s.address must be a string.",
        **ADVANCED_FUTURE,
        generated_default="0.0.0.0",
        template_variable="a2s_address",
    ),
    F(
        "a2s_port",
        ("a2s", "port"),
        VALUE_INTEGER,
        "a2s.port must be an integer between 1 and 65535.",
        RISK_ADVANCED,
        PERMISSION_FUTURE_WEB,
        SECRET_BEHAVIOR_NOT_SECRET,
        RESTART_BEHAVIOR_TUI_OPTIONAL,
        generated_default=17777,
        template_variable="a2s_port",
        minimum=1,
        maximum=65535,
        tui_input_id="inp_a2s_port",
    ),
    F(
        "rcon_address",
        ("rcon", "address"),
        VALUE_STRING,
        "rcon.address must be a string.",
        RISK_DANGEROUS,
        PERMISSION_FUTURE_POLICY,
        SECRET_BEHAVIOR_NOT_SECRET,
        RESTART_BEHAVIOR_UNKNOWN,
        generated_default="0.0.0.0",
        template_variable="rcon_address",
    ),
    F(
        "rcon_port",
        ("rcon", "port"),
        VALUE_INTEGER,
        "rcon.port must be an integer between 1 and 65535.",
        RISK_ADVANCED,
        PERMISSION_FUTURE_WEB,
        SECRET_BEHAVIOR_NOT_SECRET,
        RESTART_BEHAVIOR_TUI_OPTIONAL,
        generated_default=19999,
        template_variable="rcon_port",
        minimum=1,
        maximum=65535,
        tui_input_id="inp_rcon_port",
    ),
    F(
        "rcon_password",
        ("rcon", "password"),
        VALUE_STRING,
        "rcon.password must be a string.",
        RISK_SECRET,
        PERMISSION_FUTURE_POLICY,
        SECRET_BEHAVIOR_GENERATED_SECRET,
        RESTART_BEHAVIOR_TUI_OPTIONAL,
        generated_secret_variable="rcon_password",
        template_variable="rcon_password",
        tui_input_id="inp_rcon_pass",
        cli_command="set-rcon-password",
    ),
    F(
        "rcon_permission",
        ("rcon", "permission"),
        VALUE_STRING,
        "rcon.permission must be a string.",
        RISK_DANGEROUS,
        PERMISSION_FUTURE_POLICY,
        SECRET_BEHAVIOR_NOT_SECRET,
        RESTART_BEHAVIOR_UNKNOWN,
        generated_default="admin",
        template_variable="rcon_permission",
    ),
    F(
        "rcon_max_clients",
        ("rcon", "maxClients"),
        VALUE_INTEGER,
        "rcon.maxClients must be a positive integer.",
        **ADVANCED_FUTURE,
        generated_default=16,
        template_variable="rcon_max_clients",
        minimum=1,
    ),
    F(
        "name",
        ("game", "name"),
        VALUE_STRING,
        "game.name is required.",
        **SAFE_WEB,
        audit_field_name="name",
        generated_default="Arma Reforger Server",
        template_variable="server_name",
        required=True,
        max_length=STRING_LIMIT,
        ui=U("Server name", "text", UI_GROUP_FORM_GRID, "field-wide", True, STRING_LIMIT),
        tui_input_id="inp_name",
        cli_command="set-name",
    ),
    F(
        "game_password",
        ("game", "password"),
        VALUE_STRING,
        "game.password must be a string.",
        RISK_SECRET,
        PERMISSION_FUTURE_POLICY,
        SECRET_BEHAVIOR_SECRET,
        RESTART_BEHAVIOR_TUI_OPTIONAL,
        generated_default="",
        template_variable="game_password",
        tui_input_id="inp_game_pass",
    ),
    F(
        "password_admin",
        ("game", "passwordAdmin"),
        VALUE_STRING,
        "game.passwordAdmin must be a string.",
        RISK_SECRET,
        PERMISSION_FUTURE_POLICY,
        SECRET_BEHAVIOR_GENERATED_SECRET,
        RESTART_BEHAVIOR_TUI_OPTIONAL,
        generated_secret_variable="password_admin",
        template_variable="password_admin",
        tui_input_id="inp_admin_pass",
        cli_command="set-password-admin",
    ),
    F(
        "scenario_id",
        ("game", "scenarioId"),
        VALUE_STRING,
        "game.scenarioId is required.",
        **SAFE_WEB,
        audit_field_name="scenario_id",
        generated_default="{ECC61978EDCC2B5A}Missions/23_Campaign.conf",
        template_variable="scenario_id",
        required=True,
        max_length=STRING_LIMIT,
        ui=U("Scenario ID", "text", UI_GROUP_FORM_GRID, "field-wide", True, STRING_LIMIT),
        tui_input_id="inp_scenario",
        cli_command="set-scenario",
    ),
    F(
        "max_players",
        ("game", "maxPlayers"),
        VALUE_INTEGER,
        "game.maxPlayers must be a positive integer.",
        **SAFE_WEB,
        audit_field_name="max_players",
        generated_default=64,
        template_variable="max_players",
        required=True,
        minimum=1,
        ui=U("Max players", "number", UI_GROUP_FORM_GRID, required=True, min_value=1, step=1),
        tui_input_id="inp_players",
        cli_command="set-maxplayers",
    ),
    F(
        "visible",
        ("game", "visible"),
        VALUE_BOOLEAN,
        "Boolean field value is invalid.",
        **SAFE_WEB,
        audit_field_name="visible",
        generated_default=True,
        template_variable="visible",
        ui=U(
            "Show server in server browser",
            "checkbox",
            UI_GROUP_CHECKBOX_GRID,
        ),
    ),
    F(
        "server_max_view_distance",
        ("game", "gameProperties", "serverMaxViewDistance"),
        VALUE_INTEGER,
        "game.gameProperties.serverMaxViewDistance must be a positive integer.",
        **SAFE_WEB,
        audit_field_name="server_max_view_distance",
        generated_default=2500,
        template_variable="server_max_view_distance",
        required=True,
        minimum=1,
        ui=U(
            "Server max view distance",
            "number",
            UI_GROUP_FORM_GRID,
            required=True,
            min_value=1,
            step=1,
        ),
    ),
    F(
        "server_min_grass_distance",
        ("game", "gameProperties", "serverMinGrassDistance"),
        VALUE_INTEGER,
        "game.gameProperties.serverMinGrassDistance must be a non-negative integer.",
        **SAFE_WEB,
        audit_field_name="server_min_grass_distance",
        generated_default=50,
        template_variable="server_min_grass_distance",
        required=True,
        minimum=0,
        ui=U(
            "Server min grass distance",
            "number",
            UI_GROUP_FORM_GRID,
            required=True,
            min_value=0,
            step=1,
        ),
    ),
    F(
        "network_view_distance",
        ("game", "gameProperties", "networkViewDistance"),
        VALUE_INTEGER,
        "game.gameProperties.networkViewDistance must be an integer.",
        **ADVANCED_FUTURE,
        generated_default=1000,
        template_variable="network_view_distance",
        minimum=0,
    ),
    F(
        "disable_third_person",
        ("game", "gameProperties", "disableThirdPerson"),
        VALUE_BOOLEAN,
        "Boolean field value is invalid.",
        **SAFE_WEB,
        audit_field_name="disable_third_person",
        generated_default=True,
        template_variable="disable_third_person",
        ui=U("Disable third-person view", "checkbox", UI_GROUP_CHECKBOX_GRID),
    ),
    F(
        "fast_validation",
        ("game", "gameProperties", "fastValidation"),
        VALUE_BOOLEAN,
        "Boolean field value is invalid.",
        **ADVANCED_FUTURE,
        generated_default=True,
        template_variable="fast_validation",
    ),
    F(
        "battleye",
        ("game", "gameProperties", "battlEye"),
        VALUE_BOOLEAN,
        "Boolean field value is invalid.",
        **SAFE_WEB,
        audit_field_name="battleye",
        generated_default=True,
        template_variable="battleye",
        ui=U("BattlEye", "checkbox", UI_GROUP_CHECKBOX_GRID),
    ),
    F(
        "game_mods",
        ("game", "mods"),
        VALUE_LIST,
        "game.mods must be a list.",
        RISK_ADVANCED,
        PERMISSION_DOMAIN,
        SECRET_BEHAVIOR_NOT_SECRET,
        RESTART_BEHAVIOR_MANAGED_FLOW,
        permission_name=MODS_MANAGE_PERMISSION,
        generated_default=[],
        template_variable="game_mods",
    ),
    F(
        "game_admins",
        ("game", "admins"),
        VALUE_LIST,
        "game.admins must be a list.",
        RISK_DANGEROUS,
        PERMISSION_DOMAIN,
        SECRET_BEHAVIOR_NOT_SECRET,
        RESTART_BEHAVIOR_MANAGED_FLOW,
        permission_name=ADMINS_MANAGE_PERMISSION,
    ),
)

_FIELD_BY_NAME = {field.name: field for field in SERVER_CONFIG_FIELDS}
_FIELD_BY_PATH = {field.config_path: field for field in SERVER_CONFIG_FIELDS}
WEB_CONFIG_FIELD_NAMES = (
    "name",
    "scenario_id",
    "max_players",
    "visible",
    "disable_third_person",
    "battleye",
    "server_max_view_distance",
    "server_min_grass_distance",
)
TUI_STRUCTURED_CONFIG_FIELD_NAMES = tuple(
    field.name for field in SERVER_CONFIG_FIELDS if field.tui_input_id is not None
)
CLI_COMPAT_CONFIG_FIELD_NAMES = tuple(
    field.name for field in SERVER_CONFIG_FIELDS if field.cli_command is not None
)


def get_config_field(name: str) -> ServerConfigField:
    try:
        return _FIELD_BY_NAME[name]
    except KeyError as exc:
        raise ServerConfigSchemaError(f"Unknown server config field: {name}") from exc


def get_config_field_by_path(path: tuple[str, ...]) -> ServerConfigField:
    try:
        return _FIELD_BY_PATH[path]
    except KeyError as exc:
        dotted = ".".join(path)
        raise ServerConfigSchemaError(f"Unknown server config path: {dotted}") from exc


def web_config_field_descriptors() -> tuple[ServerConfigField, ...]:
    return tuple(get_config_field(name) for name in WEB_CONFIG_FIELD_NAMES)


def tui_structured_config_fields() -> tuple[ServerConfigField, ...]:
    return tuple(get_config_field(name) for name in TUI_STRUCTURED_CONFIG_FIELD_NAMES)


def cli_compat_config_fields() -> tuple[ServerConfigField, ...]:
    return tuple(get_config_field(name) for name in CLI_COMPAT_CONFIG_FIELD_NAMES)


def generated_default_fields() -> tuple[ServerConfigField, ...]:
    return tuple(
        field
        for field in SERVER_CONFIG_FIELDS
        if field.template_variable is not None
        and (
            field.generated_default is not DEFAULT_MISSING
            or field.generated_secret_variable is not None
        )
    )


def _secret_default_value(field: ServerConfigField, generated_secrets: Mapping[str, Any]) -> Any:
    if field.generated_secret_variable is None:
        return copy.deepcopy(field.generated_default)
    if field.generated_secret_variable not in generated_secrets:
        raise ServerConfigSchemaError(
            f"Missing generated secret for {field.generated_secret_variable}."
        )
    return generated_secrets[field.generated_secret_variable]


def generated_default_template_context(
    *,
    rcon_password: str,
    password_admin: str,
) -> dict[str, Any]:
    generated_secrets = {
        "rcon_password": rcon_password,
        "password_admin": password_admin,
    }
    context: dict[str, Any] = {}
    for field in generated_default_fields():
        assert field.template_variable is not None
        context[field.template_variable] = _secret_default_value(field, generated_secrets)
    return context


def generated_default_config_values(
    *,
    rcon_password: str,
    password_admin: str,
) -> dict[str, Any]:
    generated_secrets = {
        "rcon_password": rcon_password,
        "password_admin": password_admin,
    }
    data: dict[str, Any] = {}
    for field in generated_default_fields():
        set_nested_value(
            data,
            field.config_path,
            _secret_default_value(field, generated_secrets),
            create_missing=True,
            include_linked=False,
        )
    return data


def nested_value(data: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = data
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def set_nested_value(
    data: dict[str, Any],
    path: tuple[str, ...],
    value: Any,
    *,
    create_missing: bool = False,
    include_linked: bool = False,
) -> None:
    target = data
    for key in path[:-1]:
        next_value = target.get(key)
        if next_value is None and create_missing:
            next_value = {}
            target[key] = next_value
        if not isinstance(next_value, dict):
            dotted = ".".join(path[:-1])
            raise ServerConfigSchemaError(f"{dotted} must be an object.")
        target = next_value
    target[path[-1]] = value
    if include_linked:
        field = _FIELD_BY_PATH.get(path)
        if field is not None:
            for linked_path in field.linked_config_paths:
                set_nested_value(
                    data,
                    linked_path,
                    value,
                    create_missing=create_missing,
                    include_linked=False,
                )


def set_config_field(
    data: dict[str, Any],
    field_name: str,
    value: Any,
    *,
    create_missing: bool = True,
    include_linked: bool = True,
) -> None:
    field = get_config_field(field_name)
    set_nested_value(
        data,
        field.config_path,
        value,
        create_missing=create_missing,
        include_linked=include_linked,
    )


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _parse_string_value(value: Any, field: ServerConfigField) -> str:
    if field.secret_behavior != SECRET_BEHAVIOR_NOT_SECRET:
        return "" if value is None else str(value)
    value = _safe_text(value)
    if field.required and not value:
        raise ServerConfigSchemaError(field.validation_message)
    if field.max_length is not None and len(value) > field.max_length:
        raise ServerConfigSchemaError(field.validation_message)
    if any(ord(char) < 32 for char in value):
        raise ServerConfigSchemaError(field.validation_message)
    return value


def _parse_string_form_value(form: Mapping[str, Any], field: ServerConfigField) -> str:
    return _parse_string_value(form.get(field.form_name), field)


def _parse_int_text(raw: str, field: ServerConfigField) -> int:
    try:
        value = int(raw, 10)
    except (TypeError, ValueError) as exc:
        raise ServerConfigSchemaError(field.validation_message) from exc
    if str(value) != raw and raw not in {f"+{value}", f"0{value}"}:
        raise ServerConfigSchemaError(field.validation_message)
    if field.minimum is not None and value < field.minimum:
        raise ServerConfigSchemaError(field.validation_message)
    if field.maximum is not None and value > field.maximum:
        raise ServerConfigSchemaError(field.validation_message)
    return value


def _parse_int_form_value(form: Mapping[str, Any], field: ServerConfigField) -> int:
    return _parse_int_text(_safe_text(form.get(field.form_name)), field)


def _parse_bool_form_value(form: Mapping[str, Any], field: ServerConfigField) -> bool:
    if field.form_name not in form or form.get(field.form_name) in (None, ""):
        return False
    value = _safe_text(form.get(field.form_name)).lower()
    if value in {"1", "true", "on", "yes"}:
        return True
    if value in {"0", "false", "off", "no"}:
        return False
    raise ServerConfigSchemaError(field.validation_message)


def parse_form_field_value(form: Mapping[str, Any], field: ServerConfigField) -> Any:
    if field.value_type == VALUE_STRING:
        return _parse_string_form_value(form, field)
    if field.value_type == VALUE_INTEGER:
        return _parse_int_form_value(form, field)
    if field.value_type == VALUE_BOOLEAN:
        return _parse_bool_form_value(form, field)
    raise ServerConfigSchemaError(f"Field {field.name} is not form-editable.")


def parse_text_field_value(value: Any, field_name: str) -> Any:
    field = get_config_field(field_name)
    if field.value_type == VALUE_INTEGER:
        return _parse_int_text(str(value), field)
    if field.value_type == VALUE_BOOLEAN:
        raw = str(value).strip().lower()
        if raw in {"1", "true", "on", "yes"}:
            return True
        if raw in {"0", "false", "off", "no"}:
            return False
        raise ServerConfigSchemaError(field.validation_message)
    if field.value_type == VALUE_STRING:
        return _parse_string_value(value, field)
    raise ServerConfigSchemaError(f"Field {field.name} is not scalar-editable.")


def _generated_input_default(field: ServerConfigField) -> Any:
    if field.generated_default is DEFAULT_MISSING:
        return ""
    return copy.deepcopy(field.generated_default)


def config_field_input_value(data: Mapping[str, Any], field_name: str) -> Any:
    field = get_config_field(field_name)
    value = nested_value(data, field.config_path)
    if value is None:
        value = _generated_input_default(field)
    if field.value_type == VALUE_BOOLEAN:
        return value if isinstance(value, bool) else False
    if field.value_type == VALUE_INTEGER:
        return value if isinstance(value, int) and not isinstance(value, bool) else ""
    if field.value_type == VALUE_STRING:
        return "" if value is None else str(value)
    return copy.deepcopy(value)


def save_registered_config_value(
    config_path: Path | str,
    field_name: str,
    value: Any,
) -> None:
    from armactl import config_manager

    field = get_config_field(field_name)
    parsed = value
    if field.value_type in {VALUE_INTEGER, VALUE_BOOLEAN, VALUE_STRING}:
        parsed = parse_text_field_value(value, field.name)
    data = config_manager.load_config(config_path)
    set_config_field(data, field.name, parsed, create_missing=True, include_linked=True)
    config_manager.save_config(config_path, data)
