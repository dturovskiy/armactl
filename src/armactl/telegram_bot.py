"""Runtime for the optional Telegram admin bot."""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from typing import Any

from armactl.bot_config import BotConfigError, load_bot_config
from armactl.discovery import discover
from armactl.i18n import tr_for_lang, translate_for_lang, using_lang
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
SEPARATOR = "────────────────────"


def admin_chat_allowed(chat_id: int | str, admin_chat_ids: list[str]) -> bool:
    """Check whether a Telegram chat is in the configured admin allowlist."""
    return str(chat_id) in admin_chat_ids


def _icon_line(icon: str, text: str) -> str:
    """Prefix a Telegram-friendly line with an emoji/icon."""
    return f"{icon} {text}"


def _bullet_line(text: str) -> str:
    """Render a simple bullet line for Telegram text blocks."""
    return f"• {text}"


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
    server_icon = "🟢" if snapshot.server_running else "🔴"

    lines = [
        _icon_line(
            "🤖",
            tr_for_lang(lang, "ArmaCtl Telegram Bot [{instance}]", instance=snapshot.instance),
        ),
        SEPARATOR,
        _icon_line(server_icon, tr_for_lang(lang, "Server: {value}", value=running_text)),
        _icon_line("⚙️", tr_for_lang(lang, "Service: {value}", value=snapshot.service_name)),
        _bullet_line(
            tr_for_lang(
                lang,
                "Service active state: {value}",
                value=snapshot.service_active_state,
            )
        ),
        _bullet_line(tr_for_lang(lang, "Service enabled: {value}", value=enabled_text)),
        "",
        _icon_line("⏰", tr_for_lang(lang, "Timer: {value}", value=snapshot.timer_name)),
        _bullet_line(tr_for_lang(lang, "Current schedule: {value}", value=schedule_text)),
        _bullet_line(tr_for_lang(lang, "Next run: {value}", value=next_run_text)),
        "",
        _icon_line("👥", translate_for_lang(lang, "Players: not implemented yet.")),
    ]
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
    enabled_icon = "🟢" if timer_status.get("enabled") else "🔴"

    lines = [
        _icon_line(
            "⏰",
            tr_for_lang(lang, "Restart Schedule: {instance}", instance=instance),
        ),
        SEPARATOR,
        _icon_line(enabled_icon, tr_for_lang(lang, "Enabled: {value}", value=enabled_text)),
        _bullet_line(tr_for_lang(lang, "Current schedule: {value}", value=schedule_text)),
        _bullet_line(tr_for_lang(lang, "Next run: {value}", value=next_run_text)),
        "",
        _icon_line("🛠️", translate_for_lang(lang, "Schedule controls:")),
        _bullet_line(
            translate_for_lang(lang, "Update the schedule with: /schedule 05:00, 20:00")
        ),
    ]
    return "\n".join(lines)


def _build_status_snapshot(instance: str) -> BotStatusSnapshot:
    """Collect service/timer state into a test-friendly snapshot."""
    state = discover(instance, save=False)
    service_status = get_service_status(service_unit_name(instance))
    timer_status = get_timer_status(timer_unit_name(instance))
    return BotStatusSnapshot(
        instance=instance,
        server_running=state.server_running,
        service_name=service_unit_name(instance),
        service_active_state=service_status.get("active_state", "unknown"),
        service_enabled=bool(service_status.get("enabled")),
        timer_name=timer_unit_name(instance),
        schedule=timer_status.get("schedule", ""),
        next_run=timer_status.get("next_run", ""),
    )


class ArmaCtlTelegramBot:
    """Small Telegram admin bot around the existing armactl backend."""

    def __init__(self, instance: str):
        self.config = load_bot_config(instance)
        self.instance = self.config.instance
        self.lang = self.config.language

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
        """Render the main bot menu text."""
        return "\n".join(
            [
                _icon_line(
                    "🤖",
                    self.tr("ArmaCtl Telegram Bot [{instance}]", instance=self.instance),
                ),
                SEPARATOR,
                self.t("Choose an action:"),
            ]
        )

    def action_result_text(self, result, details: str) -> str:
        """Render a backend action result followed by a refreshed detail block."""
        icon = "✅" if getattr(result, "success", False) else "❌"
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
                InlineKeyboardButton(self.button_label("📊", "Status"), callback_data="status"),
                InlineKeyboardButton(self.button_label("⏰", "Schedule"), callback_data="schedule"),
            ],
            [
                InlineKeyboardButton(
                    self.button_label("▶️", "Start Server"),
                    callback_data="start",
                ),
                InlineKeyboardButton(
                    self.button_label("⏹️", "Stop Server"),
                    callback_data="stop:confirm",
                ),
            ],
            [
                InlineKeyboardButton(
                    self.button_label("🔄", "Restart Server"),
                    callback_data="restart:confirm",
                ),
                InlineKeyboardButton(
                    self.button_label("♻️", "Refresh Menu"),
                    callback_data="menu",
                ),
            ],
        ]
        return InlineKeyboardMarkup(keyboard)

    def _schedule_keyboard(self):
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        keyboard = [
            [
                InlineKeyboardButton(
                    self.button_label("✅", "Enable Timer"),
                    callback_data="schedule:enable",
                ),
                InlineKeyboardButton(
                    self.button_label("⏸️", "Disable Timer"),
                    callback_data="schedule:disable",
                ),
            ],
            [
                InlineKeyboardButton(
                    self.button_label("🚨", "Restart Now"),
                    callback_data="schedule:restart-now",
                ),
                InlineKeyboardButton(self.button_label("↩️", "Back"), callback_data="menu"),
            ],
        ]
        return InlineKeyboardMarkup(keyboard)

    def _confirm_keyboard(self, action: str):
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        keyboard = [
            [
                InlineKeyboardButton(
                    self.button_label("✅", "Yes"),
                    callback_data=f"{action}:run",
                ),
                InlineKeyboardButton(self.button_label("❌", "Cancel"), callback_data="menu"),
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

    async def _deny_access(self, update) -> None:
        chat_id = getattr(update.effective_chat, "id", "unknown")
        message = "\n".join(
            [
                _icon_line(
                    "⛔",
                    self.tr("Bot access denied for chat ID {chat_id}.", chat_id=chat_id),
                ),
                _icon_line(
                    "🆔",
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

    async def _reply_with_menu(self, update, text: str, *, schedule: bool = False) -> None:
        markup = self._schedule_keyboard() if schedule else self._main_keyboard()
        if getattr(update, "callback_query", None) is not None:
            await update.callback_query.edit_message_text(text=text, reply_markup=markup)
        else:
            await update.effective_message.reply_text(text, reply_markup=markup)

    async def _edit_message(self, query, text: str, markup) -> None:
        """Edit an existing callback message with an explicit keyboard."""
        await query.edit_message_text(text=text, reply_markup=markup)

    def _call_backend(self, fn, *args):
        with using_lang(self.lang):
            return fn(*args)

    async def start_command(self, update, context) -> None:
        if not await self._ensure_allowed(update):
            return
        await self._reply_with_menu(update, self.menu_text())

    async def status_command(self, update, context) -> None:
        if not await self._ensure_allowed(update):
            return
        await self._reply_with_menu(update, self._status_text())

    async def stop_command(self, update, context) -> None:
        if not await self._ensure_allowed(update):
            return
        result = self._call_backend(stop_service, service_unit_name(self.instance))
        await self._reply_with_menu(
            update,
            self.action_result_text(result, self._status_text()),
        )

    async def restart_command(self, update, context) -> None:
        if not await self._ensure_allowed(update):
            return
        result = self._call_backend(restart_service, service_unit_name(self.instance))
        await self._reply_with_menu(
            update,
            self.action_result_text(result, self._status_text()),
        )

    async def schedule_command(self, update, context) -> None:
        if not await self._ensure_allowed(update):
            return

        if getattr(context, "args", None):
            raw_schedule = " ".join(context.args).strip()
            schedule_entries = normalize_on_calendar_entries(raw_schedule)
            if not schedule_entries:
                text = self.t("At least one restart time is required.")
                await self._reply_with_menu(update, text, schedule=True)
                return

            results = self._call_backend(
                update_restart_timer_schedule,
                self.instance,
                schedule_entries,
            )
            failures = [result.message for result in results if not result.success]
            if failures:
                await self._reply_with_menu(update, failures[0], schedule=True)
                return

            pretty_schedule = format_schedule_for_input(schedule_entries)
            text = "\n\n".join(
                [
                    _icon_line(
                        "✅",
                        self.tr(
                            "Restart schedule updated to {schedule}.",
                            schedule=pretty_schedule,
                        ),
                    ),
                    self._schedule_text(),
                ]
            )
            await self._reply_with_menu(update, text, schedule=True)
            return

        await self._reply_with_menu(update, self._schedule_text(), schedule=True)

    async def callback_handler(self, update, context) -> None:
        if not await self._ensure_allowed(update):
            return

        query = update.callback_query
        await query.answer()
        data = query.data or ""

        if data == "menu":
            await self._reply_with_menu(update, self.menu_text())
            return

        if data == "status":
            await self._reply_with_menu(update, self._status_text())
            return

        if data == "schedule":
            await self._reply_with_menu(update, self._schedule_text(), schedule=True)
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
                schedule=True,
            )
            return

        if data == "schedule:disable":
            result = self._call_backend(disable_service, timer_unit_name(self.instance))
            await self._reply_with_menu(
                update,
                self.action_result_text(result, self._schedule_text()),
                schedule=True,
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
                schedule=True,
            )

    def build_application(self):
        """Create and configure the PTB Application."""
        try:
            from telegram import Update
            from telegram.ext import Application, CallbackQueryHandler, CommandHandler
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
