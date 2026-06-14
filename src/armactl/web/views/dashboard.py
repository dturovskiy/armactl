"""Lifecycle-aware dashboard view model helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

ACTIVE_LIFECYCLES = frozenset({"stopped", "running"})


def _section(value: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    raw = value.get(name)
    return raw if isinstance(raw, Mapping) else {}


def _text(value: Any, default: str = "unknown") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _bool_text(value: Any) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "unknown"


def _item(label: str, value: Any, *, translate_value: bool = False) -> dict[str, Any]:
    return {
        "label": label,
        "value": _text(value),
        "translate_value": translate_value,
    }


def _summary_items(snapshot: Mapping[str, Any], lifecycle: str) -> list[dict[str, Any]]:
    overview = _section(snapshot, "overview")
    service = _section(snapshot, "service")
    players = _section(snapshot, "players")
    fps = _section(snapshot, "fps_metrics")
    host = _section(snapshot, "host_metrics")
    web = _section(snapshot, "web")
    config = _section(snapshot, "config")
    mods = _section(snapshot, "mods")

    items = [
        _item("Instance", snapshot.get("instance")),
        _item("Lifecycle", overview.get("label") or lifecycle, translate_value=True),
    ]
    if lifecycle == "running":
        service_value = service.get("active_state") or "active"
        items.extend(
            [
                _item("Service", service_value, translate_value=True),
                _item("Players", players.get("count_text", "unavailable")),
                _item("FPS", fps.get("fps_text", "unavailable")),
            ]
        )
    elif lifecycle == "stopped":
        items.extend(
            [
                _item("Service", "stopped", translate_value=True),
                _item("Name", config.get("server_name", "unknown")),
                _item("Mods", mods.get("count", 0)),
            ]
        )
    else:
        bind_host = web.get("bind_host", "unknown")
        bind_port = web.get("bind_port", "unknown")
        items.extend(
            [
                _item("Host CPU", host.get("cpu_text", "unknown")),
                _item("Web bind", f"{bind_host}:{bind_port}"),
            ]
        )
    return items


def _action_forms(lifecycle: str, can_run_actions: bool) -> list[dict[str, Any]]:
    if not can_run_actions:
        return []
    if lifecycle == "stopped":
        return [
            {
                "name": "start",
                "action_path": "/service/start",
                "label": "Start",
                "danger": False,
                "confirm_label": "",
                "confirm_value": "",
            }
        ]
    if lifecycle == "running":
        return [
            {
                "name": "stop",
                "action_path": "/service/stop",
                "label": "Stop",
                "danger": True,
                "confirm_label": "Confirm stop",
                "confirm_value": "stop",
            },
            {
                "name": "restart",
                "action_path": "/service/restart",
                "label": "Restart",
                "danger": True,
                "confirm_label": "Confirm restart",
                "confirm_value": "restart",
            },
        ]
    return []


def _quick_action_note(lifecycle: str, actions: list[dict[str, Any]]) -> str:
    if actions:
        return ""
    if lifecycle == "not_installed":
        return "Install from web is planned."
    if lifecycle == "incomplete":
        return "Repair from web is planned."
    return "No server actions available."


def _management_links(
    lifecycle: str,
    *,
    can_view_config: bool,
    can_view_mods: bool,
    can_view_admins: bool,
    can_view_bot: bool,
    can_view_files: bool,
    can_view_logs: bool,
) -> tuple[list[dict[str, str]], str]:
    if lifecycle not in ACTIVE_LIFECYCLES:
        if lifecycle == "not_installed":
            return [], "Management pages are available after the server is installed."
        return [], "Management pages are limited until the server config is repaired."

    links: list[dict[str, str]] = []
    if can_view_config:
        links.append(
            {
                "href": "/config",
                "label": "Config",
                "description": "Server settings summary",
            }
        )
    if can_view_mods:
        links.append({"href": "/mods", "label": "Mods", "description": "Active mod list"})
    if can_view_admins:
        links.append(
            {
                "href": "/admins",
                "label": "Admins",
                "description": "Game admin IDs and labels",
            }
        )
    if can_view_bot:
        links.append({"href": "/bot", "label": "Bot", "description": "Telegram bot status"})
    if can_view_files:
        links.append(
            {
                "href": "/files",
                "label": "Files",
                "description": "File browser",
            }
        )
    if can_view_logs:
        links.append(
            {
                "href": "/logs",
                "label": "Logs",
                "description": "Read-only logs and diagnostic report",
            }
        )
    return links, "" if links else "No management pages available."


def _server_cards(snapshot: Mapping[str, Any], lifecycle: str) -> list[dict[str, Any]]:
    if lifecycle not in ACTIVE_LIFECYCLES:
        return []

    service = _section(snapshot, "service")
    operational = _section(snapshot, "operational_status")
    players = _section(snapshot, "players")
    fps = _section(snapshot, "fps_metrics")
    config = _section(snapshot, "config")
    mods = _section(snapshot, "mods")
    sat = _section(snapshot, "sat")

    cards = [
        {
            "title": "Service health",
            "items": [
                _item(
                    "State",
                    service.get("active_state")
                    or ("running" if lifecycle == "running" else "stopped"),
                    translate_value=True,
                ),
                _item(
                    "Operational state",
                    operational.get("message", "unavailable"),
                    translate_value=True,
                ),
                _item("Operational age", operational.get("age_text", "unknown")),
            ],
        },
        {
            "title": "Server config",
            "href": "/config",
            "items": [
                _item("Name", config.get("server_name", "unknown")),
                _item("Scenario", config.get("scenario_id", "unknown")),
                _item("Max players", config.get("max_players", "unknown")),
            ],
        },
        {
            "title": "Active mods",
            "href": "/mods",
            "items": [
                _item("Count", mods.get("count", 0)),
                _item(
                    "Preview",
                    ", ".join(mods.get("preview_labels") or []) or "none",
                    translate_value=not bool(mods.get("preview_labels")),
                ),
            ],
        },
    ]

    if lifecycle == "running":
        cards.insert(
            1,
            {
                "title": "Live server",
                "items": [
                    _item("Players", players.get("count_text", "unavailable")),
                    _item("Server FPS", fps.get("fps_text", "unavailable")),
                    _item("Telemetry age", fps.get("age_text", "unknown")),
                ],
            },
        )

    sat_warning = sat.get("warning") or "none"
    cards.append(
        {
            "title": "Diagnostics summary",
            "items": [
                _item(
                    "ServerAdminTools",
                    "available" if sat.get("available") else "unavailable",
                    translate_value=True,
                ),
                _item("Warning", sat_warning, translate_value=sat_warning == "none"),
            ],
        }
    )
    return cards


def _host_items(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    host = _section(snapshot, "host_metrics")
    return [
        _item("CPU", host.get("cpu_text", "unknown")),
        _item("Memory", host.get("memory_text", "unknown")),
        _item("Disk", host.get("disk_text", "unknown")),
        _item("Uptime", host.get("uptime_text", "unknown")),
    ]


def _web_items(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    web = _section(snapshot, "web")
    bind_host = web.get("bind_host", "unknown")
    bind_port = web.get("bind_port", "unknown")
    return [
        _item("Bind", f"{bind_host}:{bind_port}"),
        _item("HTTPS required", web.get("https_required_text", "unknown"), translate_value=True),
        _item("Runtime dir", web.get("runtime_dir", "unknown")),
        _item("Database", web.get("db_path", "unknown")),
    ]


def _diagnostics(snapshot: Mapping[str, Any], lifecycle: str) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    overview = _section(snapshot, "overview")
    paths = _section(snapshot, "paths")
    web = _section(snapshot, "web")

    if overview.get("empty_state"):
        diagnostics.append(
            {
                "severity": "warning" if lifecycle == "incomplete" else "notice",
                "title": overview.get("empty_title", "Dashboard"),
                "message": overview.get("empty_message", ""),
                "items": [],
            }
        )

    exposure_warning = web.get("exposure_warning")
    if isinstance(exposure_warning, Mapping):
        diagnostics.append(
            {
                "severity": exposure_warning.get("severity", "warning"),
                "title": "Exposure warning",
                "message": exposure_warning.get("message", ""),
                "items": [],
            }
        )

    if lifecycle == "incomplete":
        diagnostics.append(
            {
                "severity": "warning",
                "title": "Repair diagnostics",
                "message": "Repair from web is planned.",
                "items": [
                    _item("Instance root", paths.get("instance_root", "unknown")),
                    _item("Install dir", paths.get("install_dir", "unknown")),
                    _item("Config path", paths.get("config_path", "unknown")),
                ],
            }
        )

    errors = snapshot.get("errors") or []
    if errors:
        diagnostics.append(
            {
                "severity": "warning",
                "title": "Partial data",
                "message": "Some dashboard sections are unavailable.",
                "items": [
                    _item(str(error.get("section", "unknown")), error.get("message", "unknown"))
                    for error in errors
                    if isinstance(error, Mapping)
                ],
            }
        )
    return diagnostics


def build_dashboard_view(
    snapshot: Mapping[str, Any],
    *,
    can_run_actions: bool,
    can_view_config: bool,
    can_view_mods: bool,
    can_view_admins: bool,
    can_view_bot: bool,
    can_view_jobs: bool,
    can_view_files: bool = False,
    can_view_logs: bool = False,
) -> dict[str, Any]:
    """Shape a raw dashboard snapshot into a lifecycle-aware template model."""
    lifecycle = _text(snapshot.get("lifecycle"), "unknown")
    config = _section(snapshot, "config")
    server_name = _text(config.get("server_name"), "")
    heading = server_name if lifecycle in ACTIVE_LIFECYCLES and server_name else "Dashboard"
    actions = _action_forms(lifecycle, can_run_actions)
    management_links, management_note = _management_links(
        lifecycle,
        can_view_config=can_view_config,
        can_view_mods=can_view_mods,
        can_view_admins=can_view_admins,
        can_view_bot=can_view_bot,
        can_view_files=can_view_files,
        can_view_logs=can_view_logs,
    )
    return {
        "heading": heading,
        "lifecycle": lifecycle,
        "overview_items": _summary_items(snapshot, lifecycle),
        "actions": actions,
        "quick_action_note": _quick_action_note(lifecycle, actions),
        "management_links": management_links,
        "management_note": management_note,
        "server_cards": _server_cards(snapshot, lifecycle),
        "host_items": _host_items(snapshot),
        "web_items": _web_items(snapshot),
        "diagnostics": _diagnostics(snapshot, lifecycle),
        "show_recent_jobs": can_view_jobs,
    }
