"""Lifecycle-aware dashboard view model helpers."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

ACTIVE_LIFECYCLES = frozenset({"stopped", "starting", "running"})


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


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _byte_count(value: Any) -> int | None:
    number = _number(value)
    if number is None or number < 0:
        return None
    return int(number)


def _safe_percent(value: Any) -> float | None:
    number = _number(value)
    if number is None:
        return None
    return round(min(max(number, 0.0), 100.0), 2)


def _ratio_percent(used: Any, total: Any) -> float | None:
    used_number = _number(used)
    total_number = _number(total)
    if used_number is None or total_number is None or total_number <= 0:
        return None
    return _safe_percent((used_number / total_number) * 100.0)


def _fps_percent(value: Any) -> float | None:
    fps_value = _number(value)
    if fps_value is None:
        return None
    return _safe_percent((fps_value / 60.0) * 100.0)


def _item(
    label: str,
    value: Any,
    *,
    translate_value: bool = False,
    field: str = "",
) -> dict[str, Any]:
    return {
        "label": label,
        "value": _text(value),
        "translate_value": translate_value,
        "field": field,
    }


def _summary_items(snapshot: Mapping[str, Any], lifecycle: str) -> list[dict[str, Any]]:
    overview = _section(snapshot, "overview")
    service = _section(snapshot, "service")
    players = _section(snapshot, "players")
    fps = _section(snapshot, "fps_metrics")
    host = _section(snapshot, "host_metrics")
    config = _section(snapshot, "config")
    mods = _section(snapshot, "mods")

    items = [
        _item("Instance", snapshot.get("instance"), field="overview.instance"),
        _item(
            "Lifecycle",
            overview.get("label") or lifecycle,
            translate_value=True,
            field="overview.lifecycle",
        ),
    ]
    if lifecycle == "running":
        service_value = service.get("active_state") or "active"
        items.extend(
            [
                _item("Service", service_value, translate_value=True, field="overview.service"),
                _item(
                    "Players",
                    players.get("count_text", "unavailable"),
                    field="overview.players",
                ),
                _item("FPS", fps.get("fps_text", "unavailable"), field="overview.fps"),
            ]
        )
    elif lifecycle in {"stopped", "starting"}:
        service_value = "starting" if lifecycle == "starting" else "stopped"
        items.extend(
            [
                _item(
                    "Service",
                    service_value or lifecycle,
                    translate_value=True,
                    field="overview.service",
                ),
                _item("Name", config.get("server_name", "unknown"), field="overview.name"),
                _item("Mods", mods.get("count", 0), field="overview.mods"),
            ]
        )
    else:
        items.extend(
            [_item("Host CPU", host.get("cpu_text", "unknown"), field="overview.host_cpu")]
        )
    return items


def _action_forms(lifecycle: str, can_run_actions: bool) -> list[dict[str, Any]]:
    if not can_run_actions:
        return []
    if lifecycle == "not_installed":
        return [
            {
                "name": "install",
                "action_path": "/jobs/server/install",
                "label": "Install",
                "danger": False,
                "confirm_label": "",
                "confirm_value": "",
            }
        ]
    if lifecycle == "incomplete":
        return [
            {
                "name": "repair",
                "action_path": "/jobs/server/repair",
                "label": "Repair",
                "danger": False,
                "confirm_label": "",
                "confirm_value": "",
            }
        ]
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
        return "Install is available as a background job."
    if lifecycle == "incomplete":
        return "Repair is available as a background job."
    if lifecycle == "starting":
        return "Server is starting; actions are unavailable until telemetry is ready."
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
    can_view_schedule: bool = False,
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
    if can_view_schedule:
        links.append(
            {
                "href": "/schedule",
                "label": "Schedule",
                "description": "Restart timer controls",
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


def _metric_payload(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    fps = _section(snapshot, "fps_metrics")
    host = _section(snapshot, "host_metrics")

    fps_value = _number(fps.get("fps"))
    fps_available = fps.get("available") is not False and fps_value is not None

    host_available = host.get("available") is not False
    cpu_percent = _safe_percent(host.get("cpu_percent"))

    memory_used = _byte_count(host.get("memory_used_bytes"))
    memory_total = _byte_count(host.get("memory_total_bytes"))
    memory_percent = _ratio_percent(memory_used, memory_total)

    disk_used = _byte_count(host.get("disk_used_bytes"))
    disk_total = _byte_count(host.get("disk_total_bytes"))
    disk_percent = _ratio_percent(disk_used, disk_total)

    return {
        "fps": {
            "available": fps_available,
            "value": round(fps_value, 2) if fps_available else None,
            "percent": _fps_percent(fps_value) if fps_available else None,
            "text": _text(fps.get("fps_text"), "unavailable"),
        },
        "cpu": {
            "available": host_available and cpu_percent is not None,
            "percent": cpu_percent if host_available else None,
            "text": _text(host.get("cpu_text"), "unknown"),
        },
        "memory": {
            "available": host_available and memory_percent is not None,
            "percent": memory_percent if host_available else None,
            "used_bytes": memory_used if host_available else None,
            "total_bytes": memory_total if host_available else None,
            "text": _text(host.get("memory_text"), "unknown"),
        },
        "disk": {
            "available": host_available and disk_percent is not None,
            "percent": disk_percent if host_available else None,
            "used_bytes": disk_used if host_available else None,
            "total_bytes": disk_total if host_available else None,
            "text": _text(host.get("disk_text"), "unknown"),
        },
    }


def _metric_meter(
    metric_id: str,
    label: str,
    metric: Mapping[str, Any],
    *,
    kind: str = "bar",
) -> dict[str, Any]:
    percent = _safe_percent(metric.get("percent"))
    return {
        "id": metric_id,
        "label": label,
        "text": _text(metric.get("text"), "unknown"),
        "percent": percent if percent is not None else 0.0,
        "available": bool(metric.get("available")),
        "kind": kind,
    }


def _host_meters(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    metrics = _metric_payload(snapshot)
    return [
        _metric_meter("cpu", "CPU", metrics["cpu"]),
        _metric_meter("memory", "Memory", metrics["memory"]),
        _metric_meter("disk", "Disk", metrics["disk"]),
    ]


def _fps_meter(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    metrics = _metric_payload(snapshot)
    return _metric_meter("fps", "Server FPS", metrics["fps"])


def _server_cards(snapshot: Mapping[str, Any], lifecycle: str) -> list[dict[str, Any]]:
    if lifecycle not in ACTIVE_LIFECYCLES:
        return []

    service = _section(snapshot, "service")
    operational = _section(snapshot, "operational_status")
    players = _section(snapshot, "players")
    fps = _section(snapshot, "fps_metrics")
    config = _section(snapshot, "config")
    mods = _section(snapshot, "mods")

    cards = [
        {
            "title": "Server config",
            "layout": "wide",
            "href": "/config",
            "items": [
                _item("Name", config.get("server_name", "unknown"), field="config.name"),
                _item("Scenario", config.get("scenario_id", "unknown"), field="config.scenario"),
                _item(
                    "Max players",
                    config.get("max_players", "unknown"),
                    field="config.max_players",
                ),
            ],
        },
        {
            "title": "Service health",
            "layout": "compact",
            "items": [
                _item(
                    "State",
                    "starting"
                    if lifecycle == "starting"
                    else service.get("active_state")
                    or ("running" if lifecycle == "running" else "stopped"),
                    translate_value=True,
                    field="service.state",
                ),
                _item(
                    "Operational state",
                    operational.get("message", "unavailable"),
                    translate_value=True,
                    field="service.operational_state",
                ),
                _item(
                    "Operational age",
                    operational.get("age_text", "unknown"),
                    field="service.operational_age",
                ),
            ],
        },
        {
            "title": "Active mods",
            "layout": "compact",
            "href": "/mods",
            "items": [
                _item("Count", mods.get("count", 0), field="mods.count"),
                _item(
                    "Preview",
                    ", ".join(mods.get("preview_labels") or []) or "none",
                    translate_value=not bool(mods.get("preview_labels")),
                    field="mods.preview",
                ),
            ],
        },
    ]

    if lifecycle == "running":
        cards.insert(
            2,
            {
                "title": "Live server",
                "layout": "compact",
                "items": [
                    _item(
                        "Players",
                        players.get("count_text", "unavailable"),
                        field="live.players",
                    ),
                    _item("Server FPS", fps.get("fps_text", "unavailable"), field="live.fps"),
                    _item(
                        "Telemetry age",
                        fps.get("age_text", "unknown"),
                        field="live.telemetry_age",
                    ),
                ],
                "meters": [_fps_meter(snapshot)],
            },
        )

    return cards


def _host_items(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    host = _section(snapshot, "host_metrics")
    return [
        _item("CPU", host.get("cpu_text", "unknown"), field="host.cpu"),
        _item("Memory", host.get("memory_text", "unknown"), field="host.memory"),
        _item("Disk", host.get("disk_text", "unknown"), field="host.disk"),
        _item("Uptime", host.get("uptime_text", "unknown"), field="host.uptime"),
    ]


def _diagnostics(snapshot: Mapping[str, Any], lifecycle: str) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    overview = _section(snapshot, "overview")
    paths = _section(snapshot, "paths")

    if overview.get("empty_state"):
        diagnostics.append(
            {
                "severity": "warning" if lifecycle == "incomplete" else "notice",
                "title": overview.get("empty_title", "Dashboard"),
                "message": overview.get("empty_message", ""),
                "items": [],
            }
        )

    if lifecycle == "incomplete":
        diagnostics.append(
            {
                "severity": "warning",
                "title": "Repair diagnostics",
                "message": "Repair is available as a background job.",
                "items": [
                    _item("Instance root", paths.get("instance_root", "unknown")),
                    _item("Install dir", paths.get("install_dir", "unknown")),
                    _item("Config path", paths.get("config_path", "unknown")),
                ],
            }
        )

    service = _section(snapshot, "service")
    if (
        lifecycle in ACTIVE_LIFECYCLES
        and service.get("available") is not False
        and service.get("enabled") is False
    ):
        diagnostics.append(
            {
                "severity": "warning",
                "title": "Boot Policy",
                "message": (
                    "Game service is disabled; scheduled restarts will not guarantee "
                    "boot-start after host reboot."
                ),
                "items": [
                    _item("Service unit", service.get("service_name", "unknown")),
                    _item("Autostart", "disabled", translate_value=True),
                ],
            }
        )

    sat = _section(snapshot, "sat")
    if lifecycle in ACTIVE_LIFECYCLES:
        sat_warning = _text(sat.get("warning"), "")
        if sat_warning:
            diagnostics.append(
                {
                    "severity": "warning",
                    "title": "ServerAdminTools",
                    "message": sat_warning,
                    "items": [
                        _item("Status", "available", translate_value=True),
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
    can_view_schedule: bool = False,
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
        can_view_schedule=can_view_schedule,
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
        "host_meters": _host_meters(snapshot),
        "host_items": _host_items(snapshot),
        "diagnostics": _diagnostics(snapshot, lifecycle),
        "show_recent_jobs": can_view_jobs,
    }


def _display_value(item: Mapping[str, Any], translate: Any) -> str:
    value = _text(item.get("value"))
    if item.get("translate_value"):
        return str(translate(value))
    return value


def _collect_display_fields(dashboard: Mapping[str, Any], translate: Any) -> dict[str, str]:
    fields: dict[str, str] = {}

    def collect(items: Any) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, Mapping):
                continue
            field = str(item.get("field") or "").strip()
            if field:
                fields[field] = _display_value(item, translate)

    collect(dashboard.get("overview_items"))
    collect(dashboard.get("host_items"))
    for card in dashboard.get("server_cards") or []:
        if isinstance(card, Mapping):
            collect(card.get("items"))
    quick_action_note = _text(dashboard.get("quick_action_note"), "")
    if quick_action_note:
        fields["quick_actions.note"] = str(translate(quick_action_note))
    return fields


def build_dashboard_status_payload(
    snapshot: Mapping[str, Any],
    dashboard: Mapping[str, Any],
    *,
    translate: Any = lambda value: value,
) -> dict[str, Any]:
    """Return a small secret-free DTO for live dashboard polling."""
    lifecycle = _text(dashboard.get("lifecycle") or snapshot.get("lifecycle"))
    service = _section(snapshot, "service")
    players = _section(snapshot, "players")
    fps = _section(snapshot, "fps_metrics")
    host = _section(snapshot, "host_metrics")
    mods = _section(snapshot, "mods")
    operational = _section(snapshot, "operational_status")
    heading = _text(dashboard.get("heading"), "Dashboard")
    if heading == "Dashboard":
        heading = str(translate("Dashboard"))

    fields = _collect_display_fields(dashboard, translate)
    fields["heading"] = heading

    return {
        "ok": True,
        "installed": bool(snapshot.get("installed")),
        "running": bool(snapshot.get("running")),
        "lifecycle": lifecycle,
        "heading": heading,
        "fields": fields,
        "service": {
            "state": _text(service.get("active_state")),
            "substate": _text(service.get("sub_state")),
        },
        "players": {"text": _text(players.get("count_text"), "unavailable")},
        "fps": {"text": _text(fps.get("fps_text"), "unavailable")},
        "telemetry": {
            "age": _text(fps.get("age_text"), "unknown"),
            "state": _text(operational.get("message"), "unavailable"),
        },
        "host": {
            "cpu": _text(host.get("cpu_text"), "unknown"),
            "memory": _text(host.get("memory_text"), "unknown"),
            "disk": _text(host.get("disk_text"), "unknown"),
            "uptime": _text(host.get("uptime_text"), "unknown"),
        },
        "mods": {"count": mods.get("count", 0)},
        "metrics": _metric_payload(snapshot),
        "actions": [
            {
                "name": str(action.get("name", "")),
                "label": str(translate(str(action.get("label", "")))),
            }
            for action in dashboard.get("actions") or []
            if isinstance(action, Mapping)
        ],
        "quick_action_note": str(translate(_text(dashboard.get("quick_action_note"), ""))),
    }
