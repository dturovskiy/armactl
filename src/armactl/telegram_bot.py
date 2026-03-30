"""Runtime for the optional Telegram admin bot."""

from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import armactl.metrics as metrics
import armactl.status_summary as status_summary
from armactl.a2s import query_player_status
from armactl.bot_config import BotConfigError, load_bot_config
from armactl.discovery import discover
from armactl.i18n import tr_for_lang, translate_for_lang, using_lang
from armactl.rcon import query_player_roster
from armactl.redaction import redact_sensitive_text
from armactl.service_manager import (
    disable_service,
    enable_service,
    format_schedule_for_input,
    get_service_status,
    get_timer_status,
    normalize_on_calendar_entries,
    restart_service,
    restart_service_unit_name,
    service_unit_name,
    start_service,
    stop_service,
    timer_unit_name,
    update_restart_timer_schedule,
)

LOGGER = logging.getLogger(__name__)

ROBOT = "\U0001F916"
GREEN = "\U0001F7E2"
RED = "\U0001F534"
CLOCK = "\u23F0"
PEOPLE = "\U0001F465"
PENCIL = "\u270D\uFE0F"
CHART = "\U0001F4CA"
COMPUTER = "\U0001F5A5\uFE0F"
GEAR = "\u2699\uFE0F"
PUZZLE = "\U0001F9E9"
PLAY = "\u25B6\uFE0F"
STOP_ICON = "\u23F9\uFE0F"
RESTART = "\U0001F504"
REFRESH = "\u267B\uFE0F"
CHECK = "\u2705"
PAUSE = "\u23F8\uFE0F"
ALERT = "\U0001F6A8"
BACK = "\u21A9\uFE0F"
DENY = "\u26D4"
ID_BADGE = "\U0001F194"
BULLET = "\u2022"
DETAILS = "\U0001F4CB"
TIME_INPUT_RE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")
SNAPSHOT_CACHE_TTL_SECONDS = 3.0


def admin_chat_allowed(chat_id: int | str, admin_chat_ids: list[str]) -> bool:
    """Check whether a Telegram chat is in the configured admin allowlist."""
    return str(chat_id) in admin_chat_ids


def _icon_line(icon: str, text: str) -> str:
    """Prefix a Telegram-friendly line with an emoji/icon."""
    return f"{icon} {text}"


def _bullet_line(text: str) -> str:
    """Render a simple bullet line for Telegram text blocks."""
    return f"{BULLET} {text}"


@dataclass
class BotStatusSnapshot:
    """Small, test-friendly status payload for rendering bot responses."""

    instance: str
    server_running: bool
    service_name: str
    service_active_state: str
    service_enabled: bool
    timer_name: str
    schedule: str
    next_run: str
    player_count: int | None
    max_players: int | None
    main_pid: int = 0
    cpu_percent: float | None = None
    memory_rss_bytes: int | None = None
    config_summary: status_summary.ConfigSummary = field(
        default_factory=lambda: status_summary.ConfigSummary(False)
    )
    mods_summary: status_summary.ModsSummary = field(
        default_factory=lambda: status_summary.ModsSummary(False)
    )
    host_metrics: metrics.HostMetrics = field(
        default_factory=lambda: metrics.HostMetrics(False)
    )
    player_lines: list[str] = field(default_factory=list)
    roster_available: bool = False
    roster_configured: bool = False
    roster_error: str = ""


def _player_count_text(snapshot: BotStatusSnapshot, lang: str) -> str:
    """Render a localized player-count string shared by bot pages."""
    if snapshot.player_count is None:
        return translate_for_lang(lang, "Players: unavailable")
    if snapshot.max_players is None:
        return tr_for_lang(
            lang,
            "Players: {current}",
            current=snapshot.player_count,
        )
    return tr_for_lang(
        lang,
        "Players: {current}/{max}",
        current=snapshot.player_count,
        max=snapshot.max_players,
    )


def _player_count_value_text(snapshot: BotStatusSnapshot, lang: str) -> str:
    """Render only the player count value for the dedicated players page."""
    if snapshot.player_count is None:
        return translate_for_lang(lang, "Unavailable")
    if snapshot.max_players is None:
        return str(snapshot.player_count)
    return f"{snapshot.player_count}/{snapshot.max_players}"


def render_bot_status_text(snapshot: BotStatusSnapshot, lang: str) -> str:
    """Render a localized status response for Telegram."""
    unknown_text = translate_for_lang(lang, "Unknown")
    running_text = (
        translate_for_lang(lang, "Running")
        if snapshot.server_running
        else translate_for_lang(lang, "Stopped")
    )
    schedule_text = snapshot.schedule.strip() or unknown_text
    next_run_text = snapshot.next_run.strip() or unknown_text
    enabled_text = (
        translate_for_lang(lang, "Yes")
        if snapshot.service_enabled
        else translate_for_lang(lang, "No")
    )
    server_icon = GREEN if snapshot.server_running else RED
    players_text = _player_count_text(snapshot, lang)

    lines = [
        _icon_line(
            ROBOT,
            tr_for_lang(lang, "ArmaCtl Telegram Bot [{instance}]", instance=snapshot.instance),
        ),
        "",
        _icon_line(server_icon, tr_for_lang(lang, "Server: {value}", value=running_text)),
        _bullet_line(tr_for_lang(lang, "Service: {value}", value=snapshot.service_name)),
        _bullet_line(
            tr_for_lang(
                lang,
                "Service active state: {value}",
                value=snapshot.service_active_state,
            )
        ),
        _bullet_line(tr_for_lang(lang, "Service enabled: {value}", value=enabled_text)),
        "",
        _icon_line(CLOCK, translate_for_lang(lang, "Restart Schedule")),
        _bullet_line(tr_for_lang(lang, "Timer: {value}", value=snapshot.timer_name)),
        _bullet_line(tr_for_lang(lang, "Current schedule: {value}", value=schedule_text)),
        _bullet_line(tr_for_lang(lang, "Next run: {value}", value=next_run_text)),
        "",
        _icon_line(PEOPLE, players_text),
    ]

    return "\n".join(lines)


def render_bot_metrics_text(snapshot: BotStatusSnapshot, lang: str) -> str:
    """Render runtime server metrics plus host/VM metrics for Telegram."""
    unknown_text = translate_for_lang(lang, "Unknown")
    pid_text = str(snapshot.main_pid) if snapshot.main_pid > 0 else unknown_text
    server_cpu_text = (
        metrics.format_cpu_percent(snapshot.cpu_percent)
        if snapshot.cpu_percent is not None
        else unknown_text
    )
    server_ram_text = (
        metrics.format_bytes(snapshot.memory_rss_bytes)
        if snapshot.memory_rss_bytes is not None
        else unknown_text
    )
    host = snapshot.host_metrics
    host_ram_text = (
        f"{metrics.format_bytes(host.memory_used_bytes)} / "
        f"{metrics.format_bytes(host.memory_total_bytes)}"
        if host.memory_used_bytes is not None and host.memory_total_bytes is not None
        else unknown_text
    )
    host_cpu_text = (
        metrics.format_cpu_percent(host.cpu_percent)
        if host.cpu_percent is not None
        else unknown_text
    )
    host_disk_text = (
        f"{metrics.format_bytes(host.disk_used_bytes)} / "
        f"{metrics.format_bytes(host.disk_total_bytes)}"
        if host.disk_used_bytes is not None and host.disk_total_bytes is not None
        else unknown_text
    )
    host_load_text = metrics.format_load_average(
        host.load_average_1m,
        host.load_average_5m,
        host.load_average_15m,
    )
    host_uptime_text = metrics.format_duration(host.uptime_seconds)

    return "\n".join(
        [
            _icon_line(
                CHART,
                tr_for_lang(lang, "Metrics: {instance}", instance=snapshot.instance),
            ),
            "",
            _icon_line(CHART, translate_for_lang(lang, "Runtime Metrics")),
            _bullet_line(tr_for_lang(lang, "Main PID: {value}", value=pid_text)),
            _bullet_line(tr_for_lang(lang, "Server CPU: {value}", value=server_cpu_text)),
            _bullet_line(tr_for_lang(lang, "Server RAM: {value}", value=server_ram_text)),
            "",
            _icon_line(COMPUTER, translate_for_lang(lang, "Host / VM Metrics")),
            _bullet_line(tr_for_lang(lang, "Host CPU: {value}", value=host_cpu_text)),
            _bullet_line(tr_for_lang(lang, "Host RAM: {value}", value=host_ram_text)),
            _bullet_line(tr_for_lang(lang, "Host Disk: {value}", value=host_disk_text)),
            _bullet_line(tr_for_lang(lang, "Host Load Avg: {value}", value=host_load_text)),
            _bullet_line(tr_for_lang(lang, "Host uptime: {value}", value=host_uptime_text)),
        ]
    )


def render_bot_details_text(snapshot: BotStatusSnapshot, lang: str) -> str:
    """Render config and mods details for Telegram."""
    unknown_text = translate_for_lang(lang, "Unknown")
    yes_text = translate_for_lang(lang, "Yes")
    no_text = translate_for_lang(lang, "No")

    def bool_text(value: bool | None) -> str:
        if value is True:
            return yes_text
        if value is False:
            return no_text
        return unknown_text

    lines = [
        _icon_line(
            DETAILS,
            tr_for_lang(lang, "Server Details: {instance}", instance=snapshot.instance),
        ),
        "",
        _icon_line(GEAR, translate_for_lang(lang, "Config Summary")),
    ]

    if snapshot.config_summary.available:
        config = snapshot.config_summary
        lines.extend(
            [
                _bullet_line(
                    tr_for_lang(
                        lang,
                        "Server name: {value}",
                        value=config.server_name or unknown_text,
                    )
                ),
                _bullet_line(
                    tr_for_lang(
                        lang,
                        "Scenario: {value}",
                        value=config.scenario_id or unknown_text,
                    )
                ),
                _bullet_line(
                    tr_for_lang(
                        lang,
                        "Max players: {value}",
                        value=(
                            config.max_players
                            if config.max_players is not None
                            else unknown_text
                        ),
                    )
                ),
                _bullet_line(
                    tr_for_lang(
                        lang,
                        "Ports: game {game} / A2S {a2s} / RCON {rcon}",
                        game=(
                            config.bind_port
                            if config.bind_port is not None
                            else unknown_text
                        ),
                        a2s=(
                            config.a2s_port
                            if config.a2s_port is not None
                            else unknown_text
                        ),
                        rcon=(
                            config.rcon_port
                            if config.rcon_port is not None
                            else unknown_text
                        ),
                    )
                ),
                _bullet_line(
                    tr_for_lang(
                        lang,
                        "Visible: {value}",
                        value=bool_text(config.visible),
                    )
                ),
                _bullet_line(
                    tr_for_lang(
                        lang,
                        "BattlEye: {value}",
                        value=bool_text(config.battleye),
                    )
                ),
            ]
        )
    else:
        lines.append(_bullet_line(translate_for_lang(lang, "Config summary unavailable.")))

    lines.extend(["", _icon_line(PUZZLE, translate_for_lang(lang, "Mods Summary"))])

    if snapshot.mods_summary.available:
        mods = snapshot.mods_summary
        lines.append(
            _bullet_line(
                tr_for_lang(
                    lang,
                    "Installed mods: {count}",
                    count=mods.count if mods.count is not None else 0,
                )
            )
        )
        if mods.preview:
            lines.extend(_bullet_line(entry.label) for entry in mods.preview)
        elif mods.count == 0:
            lines.append(_bullet_line(translate_for_lang(lang, "No mods configured.")))
        if mods.remaining_count > 0:
            lines.append(
                _bullet_line(
                    tr_for_lang(
                        lang,
                        "+ {count} more mod(s)",
                        count=mods.remaining_count,
                    )
                )
            )
    else:
        lines.append(_bullet_line(translate_for_lang(lang, "Mods summary unavailable.")))

    return "\n".join(lines)


def render_bot_players_text(snapshot: BotStatusSnapshot, lang: str) -> str:
    """Render a dedicated player-roster page for Telegram."""
    unknown_text = translate_for_lang(lang, "Unknown")
    lines = [
        _icon_line(
            PEOPLE,
            tr_for_lang(lang, "Players: {instance}", instance=snapshot.instance),
        ),
        "",
        _bullet_line(
            tr_for_lang(
                lang,
                "Count: {value}",
                value=_player_count_value_text(snapshot, lang),
            )
        ),
    ]

    if snapshot.player_count and snapshot.player_count > 0:
        if snapshot.player_lines:
            for index, player_line in enumerate(snapshot.player_lines, start=1):
                lines.append(f"{index}. {player_line}")
        elif not snapshot.roster_configured:
            lines.append(
                _bullet_line(
                    translate_for_lang(lang, "RCON player roster is not configured.")
                )
            )
        else:
            lines.append(
                _bullet_line(
                    tr_for_lang(
                        lang,
                        "Player roster unavailable: {value}",
                        value=snapshot.roster_error or unknown_text,
                    )
                )
            )
            lines.append(
                _bullet_line(
                    translate_for_lang(
                        lang,
                        "Check local RCON address, port, and password.",
                    )
                )
            )
    elif snapshot.player_count == 0:
        lines.append(_bullet_line(translate_for_lang(lang, "No players online.")))

    return "\n".join(lines)


def render_bot_control_text(snapshot: BotStatusSnapshot, lang: str) -> str:
    """Render a compact control page header for Telegram."""
    running_text = (
        translate_for_lang(lang, "Running")
        if snapshot.server_running
        else translate_for_lang(lang, "Stopped")
    )
    schedule_text = snapshot.schedule.strip() or translate_for_lang(lang, "Unknown")
    return "\n".join(
        [
            _icon_line(
                RESTART,
                tr_for_lang(lang, "Server Control: {instance}", instance=snapshot.instance),
            ),
            "",
            _bullet_line(tr_for_lang(lang, "Server: {value}", value=running_text)),
            _bullet_line(tr_for_lang(lang, "Service: {value}", value=snapshot.service_name)),
            _bullet_line(tr_for_lang(lang, "Current schedule: {value}", value=schedule_text)),
        ]
    )


def render_bot_schedule_text(instance: str, timer_status: dict[str, Any], lang: str) -> str:
    """Render a localized schedule response for Telegram."""
    schedule_text = timer_status.get("schedule", "").strip() or translate_for_lang(
        lang,
        "Unknown",
    )
    next_run_text = timer_status.get("next_run", "").strip() or translate_for_lang(
        lang,
        "Unknown",
    )
    enabled_text = (
        translate_for_lang(lang, "Yes")
        if timer_status.get("enabled")
        else translate_for_lang(lang, "No")
    )
    enabled_icon = GREEN if timer_status.get("enabled") else RED

    lines = [
        _icon_line(
            CLOCK,
            tr_for_lang(lang, "Restart Schedule: {instance}", instance=instance),
        ),
        "",
        _icon_line(enabled_icon, tr_for_lang(lang, "Enabled: {value}", value=enabled_text)),
        _bullet_line(tr_for_lang(lang, "Current schedule: {value}", value=schedule_text)),
        _bullet_line(tr_for_lang(lang, "Next run: {value}", value=next_run_text)),
        "",
        _icon_line(PENCIL, translate_for_lang(lang, "To change the schedule:")),
        _bullet_line(
            translate_for_lang(
                lang,
                "Press Change Time, then send something like 08:00, 20:00.",
            )
        ),
    ]
    return "\n".join(lines)


def render_schedule_input_prompt(current_schedule: str, lang: str) -> str:
    """Render the prompt shown before the bot waits for a time list message."""
    schedule_text = current_schedule.strip() or translate_for_lang(lang, "Unknown")
    lines = [
        _icon_line(PENCIL, translate_for_lang(lang, "Send restart times in your next message.")),
        _bullet_line(tr_for_lang(lang, "Current schedule: {value}", value=schedule_text)),
        _bullet_line(
            translate_for_lang(
                lang,
                "Example: 08:00, 20:00 or 06:00 18:00.",
            )
        ),
    ]
    return "\n".join(lines)


def parse_friendly_schedule_input(value: str) -> list[str]:
    """Parse simple HH:MM[:SS] user input into normalized schedule entries."""
    raw_value = value.strip()
    if not raw_value:
        return []

    separators_normalized = re.sub(r"[\n,;]+", " ", raw_value)
    tokens = [token.strip() for token in separators_normalized.split() if token.strip()]
    if not tokens or not all(TIME_INPUT_RE.match(token) for token in tokens):
        return []
    return normalize_on_calendar_entries(tokens)


def _build_status_snapshot(
    instance: str,
    *,
    include_runtime_metrics: bool = False,
    include_summaries: bool = False,
    include_host_metrics: bool = False,
    include_roster: bool = False,
) -> BotStatusSnapshot:
    """Collect service/timer state into a test-friendly snapshot."""
    state = discover(instance, save=False)
    service_status = get_service_status(service_unit_name(instance))
    timer_status = get_timer_status(timer_unit_name(instance))
    player_status = query_player_status(instance, state=state)
    runtime_metrics = (
        metrics.query_service_runtime_metrics(service_status)
        if include_runtime_metrics
        else metrics.ProcessMetrics(False, int(service_status.get("main_pid", 0) or 0))
    )
    main_pid = runtime_metrics.pid
    if include_summaries and state.config_exists and state.config_path:
        config_summary, mods_summary = status_summary.load_status_summaries(
            state.config_path
        )
    else:
        config_summary = status_summary.ConfigSummary(False)
        mods_summary = status_summary.ModsSummary(False)
    roster_lines: list[str] = []
    roster_available = False
    roster_configured = False
    roster_error = ""
    if include_roster and player_status.player_count and player_status.player_count > 0:
        roster = query_player_roster(instance)
        roster_available = roster.available
        roster_configured = roster.configured
        roster_error = roster.error
        roster_lines = [entry.name for entry in roster.entries]
    return BotStatusSnapshot(
        instance=instance,
        server_running=state.server_running,
        service_name=service_unit_name(instance),
        service_active_state=service_status.get("active_state", "unknown"),
        service_enabled=bool(service_status.get("enabled")),
        timer_name=timer_unit_name(instance),
        schedule=timer_status.get("schedule", ""),
        next_run=timer_status.get("next_run", ""),
        player_count=player_status.player_count,
        max_players=player_status.max_players,
        main_pid=main_pid,
        cpu_percent=runtime_metrics.cpu_percent,
        memory_rss_bytes=runtime_metrics.memory_rss_bytes,
        config_summary=config_summary,
        mods_summary=mods_summary,
        host_metrics=(
            metrics.query_host_metrics()
            if include_host_metrics
            else metrics.HostMetrics(False)
        ),
        player_lines=roster_lines,
        roster_available=roster_available,
        roster_configured=roster_configured,
        roster_error=roster_error,
    )


class ArmaCtlTelegramBot:
    """Small Telegram admin bot around the existing armactl backend."""

    def __init__(self, instance: str):
        self.config = load_bot_config(instance)
        self.instance = self.config.instance
        self.lang = self.config.language
        self._pending_schedule_chats: set[str] = set()
        self._snapshot_cache: dict[str, tuple[float, BotStatusSnapshot]] = {}

    def t(self, text: str) -> str:
        """Translate a literal for the configured bot language."""
        return translate_for_lang(self.lang, text)

    def tr(self, text: str, **kwargs: object) -> str:
        """Translate and format for the configured bot language."""
        return tr_for_lang(self.lang, text, **kwargs)

    def button_label(self, icon: str, text: str) -> str:
        """Render a localized Telegram button label with an emoji prefix."""
        return _icon_line(icon, self.t(text))

    def menu_text(self) -> str:
        """Render the default bot screen."""
        return self._status_text()

    def action_result_text(self, result, details: str) -> str:
        """Render a backend action result followed by a refreshed detail block."""
        icon = CHECK if getattr(result, "success", False) else "\u274C"
        return "\n".join([_icon_line(icon, result.message), "", details])

    def ensure_runtime_config(self) -> None:
        """Validate mandatory runtime bot settings before polling starts."""
        if not self.config.enabled:
            raise BotConfigError(self.t("Telegram bot is disabled in the config."))
        if not self.config.token.strip():
            raise BotConfigError(self.t("Bot token is missing in the config."))
        if not self.config.admin_chat_ids:
            raise BotConfigError(self.t("Admin Chat IDs are not configured."))

    def _main_keyboard(self):
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        keyboard = [
            [
                InlineKeyboardButton(self.button_label(CHART, "Status"), callback_data="status"),
                InlineKeyboardButton(
                    self.button_label(COMPUTER, "Metrics"),
                    callback_data="metrics",
                ),
            ],
            [
                InlineKeyboardButton(
                    self.button_label(DETAILS, "Details"),
                    callback_data="details",
                ),
                InlineKeyboardButton(
                    self.button_label(PEOPLE, "Players"),
                    callback_data="players",
                ),
            ],
            [
                InlineKeyboardButton(
                    self.button_label(CLOCK, "Schedule"),
                    callback_data="schedule",
                ),
                InlineKeyboardButton(
                    self.button_label(RESTART, "Control"),
                    callback_data="control",
                ),
            ],
            [
                InlineKeyboardButton(
                    self.button_label(REFRESH, "Refresh Status"),
                    callback_data="refresh",
                ),
            ],
        ]
        return InlineKeyboardMarkup(keyboard)

    def _schedule_keyboard(self):
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        keyboard = [
            [
                InlineKeyboardButton(
                    self.button_label(CHECK, "Enable Timer"),
                    callback_data="schedule:enable",
                ),
                InlineKeyboardButton(
                    self.button_label(PAUSE, "Disable Timer"),
                    callback_data="schedule:disable",
                ),
            ],
            [
                InlineKeyboardButton(
                    self.button_label(PENCIL, "Change Time"),
                    callback_data="schedule:edit",
                ),
                InlineKeyboardButton(
                    self.button_label(ALERT, "Restart Now"),
                    callback_data="schedule:restart-now",
                ),
            ],
            [
                InlineKeyboardButton(
                    self.button_label(BACK, "Back to Menu"),
                    callback_data="menu",
                ),
            ],
        ]
        return InlineKeyboardMarkup(keyboard)

    def _control_keyboard(self):
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        keyboard = [
            [
                InlineKeyboardButton(
                    self.button_label(PLAY, "Start Server"),
                    callback_data="start",
                ),
                InlineKeyboardButton(
                    self.button_label(STOP_ICON, "Stop Server"),
                    callback_data="stop:confirm",
                ),
            ],
            [
                InlineKeyboardButton(
                    self.button_label(RESTART, "Restart Server"),
                    callback_data="restart:confirm",
                ),
                InlineKeyboardButton(
                    self.button_label(BACK, "Back to Menu"),
                    callback_data="menu",
                ),
            ],
        ]
        return InlineKeyboardMarkup(keyboard)

    def _confirm_keyboard(self, action: str):
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        keyboard = [
            [
                InlineKeyboardButton(
                    self.button_label(CHECK, "Yes"),
                    callback_data=f"{action}:run",
                ),
                InlineKeyboardButton(self.button_label("\u274C", "Cancel"), callback_data="menu"),
            ]
        ]
        return InlineKeyboardMarkup(keyboard)

    def _status_text(self, *, force_refresh: bool = False) -> str:
        return render_bot_status_text(
            self._snapshot_for_view("status", force_refresh=force_refresh),
            self.lang,
        )

    def _metrics_text(self, *, force_refresh: bool = False) -> str:
        return render_bot_metrics_text(
            self._snapshot_for_view("metrics", force_refresh=force_refresh),
            self.lang,
        )

    def _details_text(self, *, force_refresh: bool = False) -> str:
        return render_bot_details_text(
            self._snapshot_for_view("details", force_refresh=force_refresh),
            self.lang,
        )

    def _players_text(self, *, force_refresh: bool = False) -> str:
        return render_bot_players_text(
            self._snapshot_for_view("players", force_refresh=force_refresh),
            self.lang,
        )

    def _control_text(self, *, force_refresh: bool = False) -> str:
        return render_bot_control_text(
            self._snapshot_for_view("control", force_refresh=force_refresh),
            self.lang,
        )

    def _schedule_text(self) -> str:
        return render_bot_schedule_text(
            self.instance,
            get_timer_status(timer_unit_name(self.instance)),
            self.lang,
        )

    def _chat_key(self, update) -> str | None:
        chat = getattr(update, "effective_chat", None)
        if chat is None:
            return None
        return str(chat.id)

    def _clear_pending_schedule_input(self, update) -> None:
        chat_key = self._chat_key(update)
        if chat_key is not None:
            self._pending_schedule_chats.discard(chat_key)

    def _set_pending_schedule_input(self, update) -> None:
        chat_key = self._chat_key(update)
        if chat_key is not None:
            self._pending_schedule_chats.add(chat_key)

    def _has_pending_schedule_input(self, update) -> bool:
        chat_key = self._chat_key(update)
        return chat_key in self._pending_schedule_chats if chat_key is not None else False

    def _invalidate_snapshot_cache(self) -> None:
        """Drop cached view snapshots after a state-changing action."""
        self._snapshot_cache.clear()

    def _snapshot_for_view(
        self,
        view: str,
        *,
        force_refresh: bool = False,
    ) -> BotStatusSnapshot:
        """Return a short-lived cached snapshot for the requested bot view."""
        now = time.monotonic()
        cached = self._snapshot_cache.get(view)
        if (
            not force_refresh
            and cached is not None
            and now - cached[0] <= SNAPSHOT_CACHE_TTL_SECONDS
        ):
            return cached[1]

        if view == "metrics":
            snapshot = _build_status_snapshot(
                self.instance,
                include_runtime_metrics=True,
                include_host_metrics=True,
            )
        elif view == "details":
            snapshot = _build_status_snapshot(
                self.instance,
                include_summaries=True,
            )
        elif view == "players":
            snapshot = _build_status_snapshot(
                self.instance,
                include_roster=True,
            )
        else:
            snapshot = _build_status_snapshot(self.instance)

        self._snapshot_cache[view] = (now, snapshot)
        return snapshot

    async def _deny_access(self, update) -> None:
        chat_id = getattr(update.effective_chat, "id", "unknown")
        message = "\n".join(
            [
                _icon_line(
                    DENY,
                    self.tr("Bot access denied for chat ID {chat_id}.", chat_id=chat_id),
                ),
                _icon_line(
                    ID_BADGE,
                    self.tr(
                        "Your chat ID is {chat_id}. Add it to "
                        "ARMACTL_BOT_ADMIN_CHAT_IDS to authorize this chat.",
                        chat_id=chat_id,
                    ),
                ),
            ]
        )

        if getattr(update, "callback_query", None) is not None:
            await update.callback_query.answer(
                self.t("Access denied."),
                show_alert=True,
            )
        elif getattr(update, "effective_message", None) is not None:
            await update.effective_message.reply_text(message)

    async def _ensure_allowed(self, update) -> bool:
        chat = getattr(update, "effective_chat", None)
        if chat is None or not admin_chat_allowed(chat.id, self.config.admin_chat_ids):
            await self._deny_access(update)
            return False
        return True

    async def _reply_with_menu(self, update, text: str, markup=None) -> None:
        markup = markup or self._main_keyboard()
        if getattr(update, "callback_query", None) is not None:
            await update.callback_query.edit_message_text(text=text, reply_markup=markup)
        else:
            await update.effective_message.reply_text(text, reply_markup=markup)

    async def _apply_schedule_input(self, update, raw_value: str) -> None:
        schedule_entries = parse_friendly_schedule_input(raw_value)
        if not schedule_entries:
            await self._reply_with_menu(
                update,
                self.t("Could not parse restart times. Send something like 08:00, 20:00."),
                self._schedule_keyboard(),
            )
            return

        results = self._call_backend(
            update_restart_timer_schedule,
            self.instance,
            schedule_entries,
        )
        failures = [result.message for result in results if not result.success]
        if failures:
            await self._reply_with_menu(update, failures[0], self._schedule_keyboard())
            return

        self._invalidate_snapshot_cache()
        self._clear_pending_schedule_input(update)
        pretty_schedule = format_schedule_for_input(schedule_entries)
        text = "\n\n".join(
            [
                _icon_line(
                    CHECK,
                    self.tr(
                        "Restart schedule updated to {schedule}.",
                        schedule=pretty_schedule,
                    ),
                ),
                self._schedule_text(),
            ]
        )
        await self._reply_with_menu(update, text, self._schedule_keyboard())

    async def _edit_message(self, query, text: str, markup) -> None:
        """Edit an existing callback message with an explicit keyboard."""
        await query.edit_message_text(text=text, reply_markup=markup)

    def _call_backend(self, fn, *args):
        with using_lang(self.lang):
            return fn(*args)

    async def start_command(self, update, context) -> None:
        if not await self._ensure_allowed(update):
            return
        self._clear_pending_schedule_input(update)
        await self._reply_with_menu(update, self._status_text(force_refresh=True))

    async def status_command(self, update, context) -> None:
        if not await self._ensure_allowed(update):
            return
        self._clear_pending_schedule_input(update)
        await self._reply_with_menu(update, self._status_text(force_refresh=True))

    async def stop_command(self, update, context) -> None:
        if not await self._ensure_allowed(update):
            return
        self._clear_pending_schedule_input(update)
        result = self._call_backend(stop_service, service_unit_name(self.instance))
        self._invalidate_snapshot_cache()
        await self._reply_with_menu(
            update,
            self.action_result_text(result, self._status_text(force_refresh=True)),
        )

    async def restart_command(self, update, context) -> None:
        if not await self._ensure_allowed(update):
            return
        self._clear_pending_schedule_input(update)
        result = self._call_backend(restart_service, service_unit_name(self.instance))
        self._invalidate_snapshot_cache()
        await self._reply_with_menu(
            update,
            self.action_result_text(result, self._status_text(force_refresh=True)),
        )

    async def schedule_command(self, update, context) -> None:
        if not await self._ensure_allowed(update):
            return

        if getattr(context, "args", None):
            self._clear_pending_schedule_input(update)
            raw_schedule = " ".join(context.args).strip()
            schedule_entries = normalize_on_calendar_entries(raw_schedule)
            if not schedule_entries:
                text = self.t("At least one restart time is required.")
                await self._reply_with_menu(update, text, self._schedule_keyboard())
                return

            results = self._call_backend(
                update_restart_timer_schedule,
                self.instance,
                schedule_entries,
            )
            failures = [result.message for result in results if not result.success]
            if failures:
                await self._reply_with_menu(
                    update,
                    failures[0],
                    self._schedule_keyboard(),
                )
                return

            self._invalidate_snapshot_cache()
            pretty_schedule = format_schedule_for_input(schedule_entries)
            text = "\n\n".join(
                [
                    _icon_line(
                        CHECK,
                        self.tr(
                            "Restart schedule updated to {schedule}.",
                            schedule=pretty_schedule,
                        ),
                    ),
                    self._schedule_text(),
                ]
            )
            await self._reply_with_menu(update, text, self._schedule_keyboard())
            return

        self._clear_pending_schedule_input(update)
        await self._reply_with_menu(
            update,
            self._schedule_text(),
            self._schedule_keyboard(),
        )

    async def text_message_handler(self, update, context) -> None:
        if not await self._ensure_allowed(update):
            return
        if not self._has_pending_schedule_input(update):
            return

        message = getattr(update, "effective_message", None)
        text = "" if message is None else str(getattr(message, "text", "")).strip()
        await self._apply_schedule_input(update, text)

    async def callback_handler(self, update, context) -> None:
        if not await self._ensure_allowed(update):
            return

        query = update.callback_query
        await query.answer()
        data = query.data or ""
        if data != "schedule:edit":
            self._clear_pending_schedule_input(update)

        if data == "menu":
            await self._reply_with_menu(update, self.menu_text())
            return

        if data == "refresh":
            self._invalidate_snapshot_cache()
            await self._reply_with_menu(update, self._status_text(force_refresh=True))
            return

        if data == "status":
            await self._reply_with_menu(update, self._status_text())
            return

        if data == "metrics":
            await self._reply_with_menu(update, self._metrics_text())
            return

        if data == "details":
            await self._reply_with_menu(update, self._details_text())
            return

        if data == "players":
            await self._reply_with_menu(update, self._players_text())
            return

        if data == "control":
            await self._reply_with_menu(
                update,
                self._control_text(),
                self._control_keyboard(),
            )
            return

        if data == "schedule":
            await self._reply_with_menu(
                update,
                self._schedule_text(),
                self._schedule_keyboard(),
            )
            return

        if data == "start":
            result = self._call_backend(start_service, service_unit_name(self.instance))
            self._invalidate_snapshot_cache()
            await self._reply_with_menu(
                update,
                self.action_result_text(result, self._control_text(force_refresh=True)),
                self._control_keyboard(),
            )
            return

        if data == "stop:confirm":
            await self._edit_message(
                query,
                self.t("Are you sure you want to STOP the server?"),
                self._confirm_keyboard("stop"),
            )
            return

        if data == "stop:run":
            result = self._call_backend(stop_service, service_unit_name(self.instance))
            self._invalidate_snapshot_cache()
            await self._reply_with_menu(
                update,
                self.action_result_text(result, self._control_text(force_refresh=True)),
                self._control_keyboard(),
            )
            return

        if data == "restart:confirm":
            await self._edit_message(
                query,
                self.t("Are you sure you want to RESTART the server?"),
                self._confirm_keyboard("restart"),
            )
            return

        if data == "restart:run":
            result = self._call_backend(restart_service, service_unit_name(self.instance))
            self._invalidate_snapshot_cache()
            await self._reply_with_menu(
                update,
                self.action_result_text(result, self._control_text(force_refresh=True)),
                self._control_keyboard(),
            )
            return

        if data == "schedule:enable":
            result = self._call_backend(enable_service, timer_unit_name(self.instance))
            self._invalidate_snapshot_cache()
            await self._reply_with_menu(
                update,
                self.action_result_text(result, self._schedule_text()),
                self._schedule_keyboard(),
            )
            return

        if data == "schedule:disable":
            result = self._call_backend(disable_service, timer_unit_name(self.instance))
            self._invalidate_snapshot_cache()
            await self._reply_with_menu(
                update,
                self.action_result_text(result, self._schedule_text()),
                self._schedule_keyboard(),
            )
            return

        if data == "schedule:edit":
            self._set_pending_schedule_input(update)
            await self._reply_with_menu(
                update,
                render_schedule_input_prompt(
                    get_timer_status(timer_unit_name(self.instance)).get("schedule", ""),
                    self.lang,
                ),
                self._schedule_keyboard(),
            )
            return

        if data == "schedule:restart-now":
            result = self._call_backend(
                start_service,
                restart_service_unit_name(self.instance),
            )
            self._invalidate_snapshot_cache()
            await self._reply_with_menu(
                update,
                self.action_result_text(result, self._schedule_text()),
                self._schedule_keyboard(),
            )

    def build_application(self):
        """Create and configure the PTB Application."""
        try:
            from telegram import Update
            from telegram.ext import (
                Application,
                CallbackQueryHandler,
                CommandHandler,
                MessageHandler,
                filters,
            )
        except ImportError as e:
            raise RuntimeError(
                self.t(
                    "python-telegram-bot is not installed in the repo virtualenv. "
                    "Re-run ./scripts/bootstrap.sh --prod or --dev."
                )
            ) from e

        logging.basicConfig(
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            level=logging.INFO,
        )
        logging.getLogger("httpx").setLevel(logging.WARNING)

        application = Application.builder().token(self.config.token).build()
        application.add_handler(CommandHandler("start", self.start_command))
        application.add_handler(CommandHandler("status", self.status_command))
        application.add_handler(CommandHandler("stop", self.stop_command))
        application.add_handler(CommandHandler("restart", self.restart_command))
        application.add_handler(CommandHandler("schedule", self.schedule_command))
        application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.text_message_handler)
        )
        application.add_handler(CallbackQueryHandler(self.callback_handler))
        application.run_polling(allowed_updates=Update.ALL_TYPES)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the Telegram bot runtime."""
    parser = argparse.ArgumentParser(description="Run the armactl Telegram bot.")
    parser.add_argument("--instance", default="default", help="armactl instance name")
    args = parser.parse_args(argv)

    try:
        bot = ArmaCtlTelegramBot(args.instance)
        bot.ensure_runtime_config()
        bot.build_application()
    except Exception as e:
        safe_error = redact_sensitive_text(e)
        LOGGER.error("Telegram bot failed to start: %s", safe_error)
        print(safe_error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
