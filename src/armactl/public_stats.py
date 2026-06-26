"""Read-only public server statistics for community channels."""

from __future__ import annotations

import json
import textwrap
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from armactl import discovery, metrics, paths, player_view, status_summary
from armactl.redaction import redact_sensitive_text
from armactl.service_manager import get_service_status, service_unit_name

PLAYER_QUERY_TIMEOUT_SECONDS = 0.75
ROSTER_QUERY_TIMEOUT_SECONDS = 0.75
MAX_TEXT_LENGTH = 160
MAX_DISCORD_MESSAGE_LENGTH = 1900
MAX_PLAYER_PREVIEW = 8


@dataclass(frozen=True)
class PublicStatsSnapshot:
    """Safe public statistics intended for Discord/website-style status messages."""

    instance: str
    generated_at: str
    lifecycle: str
    running: bool
    service_state: str
    server_name: str
    scenario_id: str
    map_name: str
    players_available: bool
    player_count: int | None
    max_players: int | None
    player_names: tuple[str, ...]
    roster_available: bool
    fps_available: bool
    fps_stale: bool
    fps_text: str
    telemetry_age_text: str
    mods_available: bool
    mod_count: int | None
    mod_preview: tuple[str, ...]
    remaining_mod_count: int

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)


def _now_utc_text() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_text(value: Any, default: str = "unknown", *, max_length: int = MAX_TEXT_LENGTH) -> str:
    text = redact_sensitive_text(value).replace("\r", " ").replace("\n", " ").strip()
    text = text.replace("`", "'").replace("@", "@ ")
    if not text:
        return default
    return textwrap.shorten(text, width=max_length, placeholder="...")


def _lifecycle_from_state(state: Any, service: dict[str, Any]) -> str:
    if not getattr(state, "server_installed", False):
        if getattr(state, "has_install_evidence", lambda: False)():
            return "incomplete"
        return "not_installed"
    if getattr(state, "server_running", False):
        return "running"
    active_state = str(service.get("active_state") or "").strip().lower()
    sub_state = str(service.get("sub_state") or "").strip().lower()
    if active_state == "activating" or sub_state in {"start", "auto-restart"}:
        return "starting"
    return "stopped"


def _player_count_text(snapshot: PublicStatsSnapshot) -> str:
    if not snapshot.players_available or snapshot.player_count is None:
        return "unavailable"
    if snapshot.max_players is None:
        return str(snapshot.player_count)
    return f"{snapshot.player_count}/{snapshot.max_players}"


def _mods_text(snapshot: PublicStatsSnapshot) -> str:
    if not snapshot.mods_available or snapshot.mod_count is None:
        return "unavailable"
    return str(snapshot.mod_count)


def _clean_map_label(value: str) -> str:
    cleaned = value.strip()
    if cleaned.startswith("{") and "}" in cleaned:
        cleaned = cleaned.split("}", 1)[1]
    cleaned = cleaned.replace("\\", "/").rsplit("/", 1)[-1]
    if cleaned.lower().endswith(".conf"):
        cleaned = cleaned[:-5]

    for marker in ("ScenarioName_", "scenarioName_"):
        if marker in cleaned:
            cleaned = cleaned.split(marker, 1)[1]
            break

    for prefix in ("ARM-Campaign_", "Campaign_", "DOE_"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]

    return _safe_text(cleaned.replace("_", " "), "unknown", max_length=80)


def _scenario_fallback_label(scenario_id: str) -> str:
    return _clean_map_label(scenario_id)


def _map_text(snapshot: PublicStatsSnapshot) -> str:
    return _clean_map_label(snapshot.map_name or snapshot.scenario_id)


def _player_list_text(snapshot: PublicStatsSnapshot) -> str:
    if snapshot.player_names:
        return ", ".join(snapshot.player_names)
    if not snapshot.players_available:
        return "unavailable"
    if snapshot.player_count == 0:
        return "none"
    if not snapshot.roster_available:
        return "roster unavailable"
    return "unavailable"


def _discord_player_lines(snapshot: PublicStatsSnapshot) -> list[str]:
    if snapshot.player_names:
        return [f"- {name}" for name in snapshot.player_names]
    return [f"- {_player_list_text(snapshot)}"]


def _parse_generated_at(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _format_generated_at(value: str) -> str:
    parsed = _parse_generated_at(value)
    if parsed is None:
        return value
    return parsed.strftime("%Y-%m-%d %H:%M UTC")


def _discord_timestamp(value: str) -> str:
    parsed = _parse_generated_at(value)
    if parsed is None:
        return _format_generated_at(value)
    unix_timestamp = int(parsed.timestamp())
    return f"<t:{unix_timestamp}:f> (<t:{unix_timestamp}:R>)"


def _status_label(snapshot: PublicStatsSnapshot) -> str:
    if snapshot.running:
        return "Online"
    return {
        "starting": "Starting",
        "stopped": "Offline",
        "incomplete": "Incomplete install",
        "not_installed": "Not installed",
    }.get(snapshot.lifecycle, "Unknown")


def _status_emoji(snapshot: PublicStatsSnapshot) -> str:
    if snapshot.running:
        return "🟢"
    return {
        "starting": "🟡",
        "stopped": "🔴",
        "incomplete": "🟠",
        "not_installed": "⚪",
    }.get(snapshot.lifecycle, "⚪")


def _load_config_and_mods(
    config_path: str,
) -> tuple[status_summary.ConfigSummary, status_summary.ModsSummary]:
    if not config_path:
        return status_summary.ConfigSummary(False), status_summary.ModsSummary(False)
    return status_summary.load_status_summaries(config_path)


def load_public_stats(instance: str = paths.DEFAULT_INSTANCE_NAME) -> PublicStatsSnapshot:
    """Collect a bounded read-only statistics snapshot for public community use."""
    state = discovery.discover(instance=instance, save=False)
    service_name = getattr(state, "service_name", "") or service_unit_name(instance)
    service = get_service_status(service_name)
    config, mods = _load_config_and_mods(
        getattr(state, "config_path", "") if getattr(state, "config_exists", False) else ""
    )
    if getattr(state, "server_installed", False):
        players = player_view.query_player_view(
            instance,
            timeout=PLAYER_QUERY_TIMEOUT_SECONDS,
            roster_timeout=ROSTER_QUERY_TIMEOUT_SECONDS,
            state=state,
            include_roster=True,
        )
        fps = metrics.query_server_fps_metrics(paths.config_dir(instance))
    else:
        players = player_view.PlayerView(False, None, None)
        fps = metrics.ServerFpsMetrics(False)

    mod_preview = tuple(_safe_text(item.label, max_length=80) for item in mods.preview[:3])
    player_names = tuple(
        _safe_text(name, max_length=48) for name in players.player_lines[:MAX_PLAYER_PREVIEW]
    )

    return PublicStatsSnapshot(
        instance=_safe_text(instance, "default", max_length=64),
        generated_at=_now_utc_text(),
        lifecycle=_lifecycle_from_state(state, service),
        running=bool(getattr(state, "server_running", False)),
        service_state=_safe_text(service.get("active_state"), "unknown", max_length=64),
        server_name=_safe_text(config.server_name, "Unknown server"),
        scenario_id=_safe_text(config.scenario_id, "unknown"),
        map_name=_safe_text(players.map_name, "", max_length=80),
        players_available=bool(players.available),
        player_count=players.current if isinstance(players.current, int) else None,
        max_players=players.max_players if isinstance(players.max_players, int) else None,
        player_names=player_names,
        roster_available=bool(players.roster_available),
        fps_available=bool(fps.available),
        fps_stale=bool(fps.stale),
        fps_text=(
            metrics.format_fps(fps.fps)
            if fps.available
            else ("stale" if fps.stale else "unavailable")
        ),
        telemetry_age_text=metrics.format_duration(fps.age_seconds),
        mods_available=bool(mods.available),
        mod_count=mods.count if isinstance(mods.count, int) else None,
        mod_preview=mod_preview,
        remaining_mod_count=mods.remaining_count,
    )


def render_public_stats_text(snapshot: PublicStatsSnapshot) -> str:
    """Render public stats as plain text for terminals/logs."""
    lines = [
        f"Server: {snapshot.server_name}",
        f"Status: {_status_label(snapshot)}",
        f"Map: {_map_text(snapshot)}",
        f"Players: {_player_count_text(snapshot)}",
        f"Online: {_player_list_text(snapshot)}",
        f"FPS: {snapshot.fps_text}",
        f"Scenario: {snapshot.scenario_id}",
        f"Mods: {_mods_text(snapshot)}",
        f"Updated: {_format_generated_at(snapshot.generated_at)}",
    ]
    return "\n".join(lines)


def _truncate_discord_message(message: str) -> str:
    if len(message) <= MAX_DISCORD_MESSAGE_LENGTH:
        return message
    return message[: MAX_DISCORD_MESSAGE_LENGTH - 3].rstrip() + "..."


def _discord_table_values(snapshot: PublicStatsSnapshot) -> str:
    values = [
        _status_label(snapshot),
        _map_text(snapshot),
        _player_count_text(snapshot),
        snapshot.fps_text,
        _mods_text(snapshot),
    ]
    return " | ".join(values)


def render_discord_stats_message(snapshot: PublicStatsSnapshot) -> str:
    """Render public stats as a Discord-safe markdown message."""
    table_rows = [
        "Status | Map | Players | FPS | Mods",
        _discord_table_values(snapshot),
        "",
        "Online:",
        *_discord_player_lines(snapshot),
    ]
    lines = [
        f"**{snapshot.server_name}**",
        f"{_status_emoji(snapshot)} Server statistics",
        "```text",
        *table_rows,
        "```",
        f"🕒 Updated: {_discord_timestamp(snapshot.generated_at)}",
    ]
    return _truncate_discord_message("\n".join(lines))


def render_public_stats_json(snapshot: PublicStatsSnapshot) -> str:
    """Render public stats as stable JSON."""
    return json.dumps(snapshot.to_dict(), indent=2, sort_keys=True)
