"""Public read-only server status route for external websites."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse, Response

from armactl.redaction import redact_sensitive_text
from armactl.web.auth.dependencies import get_web_runtime_config
from armactl.web.page_models import dashboard as dashboard_page_model

router = APIRouter()


def _section(snapshot: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = snapshot.get(key)
    return value if isinstance(value, Mapping) else {}


def _safe_text(value: Any, default: str = "") -> str:
    text = redact_sensitive_text(value).replace("\r", " ").replace("\n", " ").strip()
    return text or default


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _public_status_payload(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    config = _section(snapshot, "config")
    players = _section(snapshot, "players")
    fps = _section(snapshot, "fps_metrics")
    operational = _section(snapshot, "operational_status")

    current_players = _safe_int(players.get("current"))
    max_players = _safe_int(players.get("max_players")) or _safe_int(config.get("max_players"))

    return {
        "ok": True,
        "instance": _safe_text(snapshot.get("instance"), "default"),
        "installed": bool(snapshot.get("installed")),
        "running": bool(snapshot.get("running")),
        "lifecycle": _safe_text(snapshot.get("lifecycle"), "unknown"),
        "server": {
            "name": _safe_text(config.get("server_name"), "Unknown server"),
            "scenario": _safe_text(config.get("scenario_id"), "unknown"),
        },
        "players": {
            "available": bool(players.get("available")),
            "current": current_players,
            "max": max_players,
            "text": _safe_text(players.get("count_text"), "unavailable"),
        },
        "performance": {
            "fps_available": bool(fps.get("available")),
            "fps": _safe_int(fps.get("fps")),
            "fps_text": _safe_text(fps.get("fps_text"), "unavailable"),
            "telemetry_age": _safe_text(fps.get("age_text"), "unknown"),
        },
        "status": {
            "state": _safe_text(operational.get("state"), "unknown"),
            "severity": _safe_text(operational.get("severity"), "unknown"),
            "message": _safe_text(operational.get("message"), "unavailable"),
        },
    }


@router.get("/public/server-status.json")
def public_server_status(request: Request) -> Response:
    """Return a safe public DTO for website server-status widgets."""
    config = get_web_runtime_config(request)
    try:
        snapshot = dashboard_page_model.load_dashboard_snapshot(
            "default", web_config=config
        )
    except Exception:  # noqa: BLE001 - public status must fail closed.
        return JSONResponse(
            {"ok": False, "error": "Server status is unavailable."},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return JSONResponse(_public_status_payload(snapshot))
