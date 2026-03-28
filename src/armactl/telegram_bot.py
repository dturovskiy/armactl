"""Runtime for the optional Telegram admin bot."""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass, field
from typing import Any

from armactl.a2s import query_player_status
from armactl.bot_config import BotConfigError, load_bot_config
from armactl.discovery import discover
from armactl.i18n import tr_for_lang, translate_for_lang, using_lang
from armactl.metrics import (
    format_bytes,
    format_cpu_percent,
    query_service_runtime_metrics,
)
from armactl.rcon import query_player_roster
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
TIME_INPUT_RE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")


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
    player_lines: list[str] = field(default_factory=list)
    roster_available: bool = False


def render_bot_status_text(snapshot: BotStatusSnapshot, lang: str) -> str:
    """Render a localized status response for Telegram."""
    running_text = (
        translate_for_lang(lang, "Running")
        if snapshot.server_running
        else translate_for_lang(lang, "Stopped")
    )
    schedule_text = snapshot.schedule.strip() or translate_for_lang(lang, "Unknown")
    next_run_text = snapshot.next_run.strip() or translate_for_lang(lang, "Unknown")
    enabled_text = (
        translate_for_lang(lang, "Yes")
        if snapshot.service_enabled
        else translate_for_lang(lang, "No")
    )
    server_icon = GREEN if snapshot.server_running else RED
    if snapshot.player_count is None:
        players_text = translate_for_lang(lang, "Players: unavailable")
    elif snapshot.max_players is None:
        players_text = tr_for_lang(
            lang,
            "Players: {current}",
            current=snapshot.player_count,
        )
    else:
        players_text = tr_for_lang(
            lang,
            "Players: {current}/{max}",
            current=snapshot.player_count,
            max=snapshot.max_players,
        )
    pid_text = (
        str(snapshot.main_pid)
        if snapshot.main_pid > 0
        else translate_for_lang(lang, "Unknown")
    )
    cpu_text = (
        format_cpu_percent(snapshot.cpu_percent)
        if snapshot.cpu_percent is not None
        else translate_for_lang(lang, "Unknown")
    )
    memory_text = (
        format_bytes(snapshot.memory_rss_bytes)
        if snapshot.memory_rss_bytes is not None
        else translate_for_lang(lang, "Unknown")
    )

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
        _icon_line(CHART, translate_for_lang(lang, "Runtime Metrics")),
        _bullet_line(tr_for_lang(lang, "Main PID: {value}", value=pid_text)),
        _bullet_line(tr_for_lang(lang, "Server CPU: {value}", value=cpu_text)),
        _bullet_line(tr_for_lang(lang, "Server RAM: {value}", value=memory_text)),
        "",
        _icon_line(PEOPLE, players_text),
    ]
    if snapshot.player_lines:
        lines.extend(_bullet_line(player_line) for player_line in snapshot.player_lines)
    elif snapshot.player_count and snapshot.player_count > 0 and not snapshot.roster_available:
        lines.append(
            _bullet_line(
                translate_for_lang(lang, "Player roster unavailable via RCON.")
            )
        )
    return "\n".join(lines)


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


def _build_status_snapshot(instance: str) -> BotStatusSnapshot:
    """Collect service/timer state into a test-friendly snapshot."""
    state = discover(instance, save=False)
    service_status = get_service_status(service_unit_name(instance))
    timer_status = get_timer_status(timer_unit_name(instance))
    player_status = query_player_status(instance, state=state)
    metrics = query_service_runtime_metrics(service_status)
    main_pid = metrics.pid
    roster_lines: list[str] = []
    roster_available = False
    if player_status.player_count and player_status.player_count > 0:
        roster = query_player_roster(instance)
        roster_available = roster.available
        roster_lines = [
            (
                f"{entry.name} (#{entry.player_id})"
                if entry.player_id
                else entry.name
            )
            for entry in roster.entries
        ]
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
        cpu_percent=metrics.cpu_percent,
        memory_rss_bytes=metrics.memory_rss_bytes,
        player_lines=roster_lines,
        roster_available=roster_available,
    )


class ArmaCtlTelegramBot:
    """Small Telegram admin bot around the existing armactl backend."""

    def __init__(self, instance: str):
        self.config = load_bot_config(instance)
        self.instance = self.config.instance
        self.lang = self.config.language
        self._pending_schedule_chats: set[str] = set()

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
                    self.button_label(CLOCK, "Schedule"),
                    callback_data="schedule",
                ),
            ],
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
                    self.button_label(ALERT, "Restart Now"),
                    callback_data="schedule:restart-now",
                ),
            ],
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
            ],
            [
                InlineKeyboardButton(
                    self.button_label(REFRESH, "Refresh Status"),
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

    def _status_text(self) -> str:
        return render_bot_status_text(_build_status_snapshot(self.instance), self.lang)

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

    async def _reply_with_menu(self, update, text: str) -> None:
        markup = self._main_keyboard()
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
            )
            return

        results = self._call_backend(
            update_restart_timer_schedule,
            self.instance,
            schedule_entries,
        )
        failures = [result.message for result in results if not result.success]
        if failures:
            await self._reply_with_menu(update, failures[0])
            return

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
        await self._reply_with_menu(update, text)

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
        await self._reply_with_menu(update, self.menu_text())

    async def status_command(self, update, context) -> None:
        if not await self._ensure_allowed(update):
            return
        self._clear_pending_schedule_input(update)
        await self._reply_with_menu(update, self._status_text())

    async def stop_command(self, update, context) -> None:
        if not await self._ensure_allowed(update):
            return
        self._clear_pending_schedule_input(update)
        result = self._call_backend(stop_service, service_unit_name(self.instance))
        await self._reply_with_menu(
            update,
            self.action_result_text(result, self._status_text()),
        )

    async def restart_command(self, update, context) -> None:
        if not await self._ensure_allowed(update):
            return
        self._clear_pending_schedule_input(update)
        result = self._call_backend(restart_service, service_unit_name(self.instance))
        await self._reply_with_menu(
            update,
            self.action_result_text(result, self._status_text()),
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
                await self._reply_with_menu(update, text)
                return

            results = self._call_backend(
                update_restart_timer_schedule,
                self.instance,
                schedule_entries,
            )
            failures = [result.message for result in results if not result.success]
            if failures:
                await self._reply_with_menu(update, failures[0])
                return

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
            await self._reply_with_menu(update, text)
            return

        self._clear_pending_schedule_input(update)
        await self._reply_with_menu(update, self._schedule_text())

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

        if data == "status":
            await self._reply_with_menu(update, self._status_text())
            return

        if data == "schedule":
            await self._reply_with_menu(update, self._schedule_text())
            return

        if data == "start":
            result = self._call_backend(start_service, service_unit_name(self.instance))
            await self._reply_with_menu(
                update,
                self.action_result_text(result, self._status_text()),
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
            await self._reply_with_menu(
                update,
                self.action_result_text(result, self._status_text()),
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
            await self._reply_with_menu(
                update,
                self.action_result_text(result, self._status_text()),
            )
            return

        if data == "schedule:enable":
            result = self._call_backend(enable_service, timer_unit_name(self.instance))
            await self._reply_with_menu(
                update,
                self.action_result_text(result, self._schedule_text()),
            )
            return

        if data == "schedule:disable":
            result = self._call_backend(disable_service, timer_unit_name(self.instance))
            await self._reply_with_menu(
                update,
                self.action_result_text(result, self._schedule_text()),
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
            )
            return

        if data == "schedule:restart-now":
            result = self._call_backend(
                start_service,
                restart_service_unit_name(self.instance),
            )
            await self._reply_with_menu(
                update,
                self.action_result_text(result, self._status_text()),
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
        LOGGER.error("Telegram bot failed to start: %s", e)
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
