"""Main TUI application."""

from __future__ import annotations

import os
import sys
from asyncio import Lock
from dataclasses import dataclass
from typing import Literal

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import HorizontalGroup, HorizontalScroll, VerticalGroup, VerticalScroll
from textual.widget import Widget
from textual.widgets import Button, Footer, Header, Label

from armactl import paths
from armactl.bot_manager import ensure_bot_service_runtime
from armactl.discovery import discover
from armactl.i18n import _, get_current_lang_name, toggle_lang, tr
from armactl.state import ServerState
from armactl.tui.display import get_instance_server_name

MainMenuWidgetKind = Literal["button", "status", "warning"]


@dataclass(frozen=True)
class MainMenuEntry:
    """Pure description of a main menu row."""

    kind: MainMenuWidgetKind
    widget_id: str
    variant: str | None = None


def build_main_menu_entries(state: ServerState) -> tuple[MainMenuEntry, ...]:
    """Return the state-dependent main menu entries."""
    entries: list[MainMenuEntry] = [MainMenuEntry("status", "main-menu-status")]

    if state.server_installed:
        entries.append(MainMenuEntry("button", "btn_manage", "primary"))
    else:
        if state.has_install_evidence():
            entries.append(MainMenuEntry("warning", "install-warning"))
        entries.append(MainMenuEntry("button", "btn_install", "success"))

    entries.extend(
        [
            MainMenuEntry("button", "btn_repair", "warning"),
            MainMenuEntry("button", "btn_detect", "default"),
            MainMenuEntry("button", "btn_host_tests", "primary"),
            MainMenuEntry("button", "btn_lang", "default"),
            MainMenuEntry("button", "btn_exit", "error"),
        ]
    )
    return tuple(entries)


def _restore_terminal_state() -> None:
    """Best-effort cleanup for terminals after Textual exits."""
    reset_sequences = (
        "\033[?1l"     # Normal cursor keys
        "\033>"        # Normal keypad mode
        "\033[?25h"    # Show cursor
        "\033[?1000l"  # X10 mouse tracking
        "\033[?1001l"  # VT200 highlight mouse tracking
        "\033[?1002l"  # Button-event mouse tracking
        "\033[?1003l"  # Any-event mouse tracking
        "\033[?1004l"  # Focus in/out events
        "\033[?1005l"  # UTF-8 mouse mode
        "\033[?1006l"  # SGR mouse mode
        "\033[?1007l"  # Alternate scroll mode
        "\033[?1015l"  # urxvt mouse mode
        "\033[?1016l"  # Pixel mouse mode
        "\033[?1049l"  # Alternate screen buffer
        "\033[?2004l"  # Bracketed paste
    )
    sys.stdout.write(reset_sequences)
    sys.stdout.flush()

    if os.name != "posix":
        return

    try:
        import select
        import termios

        fd = sys.stdin.fileno()
        while True:
            readable, _, _ = select.select([fd], [], [], 0)
            if not readable:
                break
            os.read(fd, 1024)
        termios.tcflush(fd, termios.TCIFLUSH)
    except Exception:
        pass


class ArmaCtlApp(App):
    """The main TUI Application for armactl."""

    CSS = """
    Screen {
        layout: vertical;
        align: center middle;
    }
    #confirm-dialog {
        width: 64;
        max-width: 90%;
        height: auto;
        border: solid green;
        padding: 1 2;
        background: $surface;
    }
    #main-menu, #manage-container {
        width: 100%;
        height: 100%;
        padding: 1 2;
        background: $surface;
    }
    #main-shell-header, #manage-header {
        width: 100%;
        height: auto;
        border: solid green;
        padding: 1 2;
        margin-bottom: 1;
    }
    #main-title-row, #manage-title-row {
        width: 100%;
        height: auto;
    }
    #main-title-block, #manage-title-block {
        width: 1fr;
        height: auto;
    }
    #main-shell-title, #manage-screen-title {
        width: 100%;
        text-style: bold;
        color: white;
    }
    #main-server-name, #main-instance-id, #manage-server-name, #manage-instance-id {
        width: 100%;
    }
    #main-instance-id, #manage-instance-id {
        color: $text-muted;
    }
    #main-runtime-badge, #manage-runtime-badge {
        width: 18;
        content-align: center middle;
        text-style: bold;
    }
    #main-status-summary, #server-status {
        width: 100%;
        margin-top: 1;
        color: $text-muted;
    }
    #main-action-bar, #manage-nav, #manage-action-row {
        width: 100%;
        height: auto;
        margin-bottom: 1;
        overflow-x: auto;
        overflow-y: hidden;
    }
    #main-action-bar Button, #manage-nav Button, #manage-action-row Button {
        width: auto;
        min-width: 10;
        height: auto;
        margin: 0 1 0 0;
    }
    #manage-nav Button {
        min-width: 8;
    }
    #main-content, #manage-content {
        width: 100%;
        height: 1fr;
        border: solid green;
        padding: 1 2;
    }
    #main-content-title, #manage-panel-title {
        width: 100%;
        margin-bottom: 1;
        text-style: bold;
        color: white;
    }
    #main-menu-status, #install-warning {
        width: 100%;
        margin-bottom: 1;
    }
    #install-warning {
        color: yellow;
    }
    #manage-panel-log {
        width: 100%;
        height: 1fr;
    }
    #main-shell-note {
        width: 100%;
        color: $text-muted;
    }
    #modpack-dialog {
        width: 80;
        height: auto;
        border: solid green;
        padding: 1 2;
        background: $surface;
    }
    #info-container {
        width: 80%;
        height: 80%;
        border: solid green;
        padding: 1 2;
        background: $surface;
    }
    #config-editor {
        width: 100%;
        height: 1fr;
        padding: 1 2;
    }
    #raw-config-container, #bot-config-container {
        width: 90%;
        height: 90%;
        border: solid green;
        padding: 1 2;
        background: $surface;
    }
    #screen-title {
        content-align: center middle;
        width: 100%;
        margin-bottom: 1;
        text-style: bold;
        color: white;
    }
    #confirm-prompt {
        content-align: center middle;
        width: 100%;
        margin-bottom: 2;
        text-style: bold;
        color: yellow;
    }
    #control-buttons, #schedule-buttons-primary, #schedule-buttons-secondary,
    #bot-enable-buttons, #bot-config-buttons, #bot-service-buttons {
        height: auto;
        margin-bottom: 1;
    }
    #control-buttons Button, #schedule-buttons-primary Button,
    #schedule-buttons-secondary Button, #bot-enable-buttons Button,
    #bot-config-buttons Button, #bot-service-buttons Button {
        width: 1fr;
    }
    HorizontalGroup Button {
        width: 1fr;
        margin: 0 1;
    }
    Button {
        margin-bottom: 1;
    }
    #info-container > Button, #modpack-suggestions Button, #btn_add_mod {
        width: 100%;
    }
    #info-container RichLog {
        height: 1fr;
        border: solid green;
        margin-bottom: 1;
    }
    #task-log {
        height: 15;
        border: solid green;
        margin-bottom: 1;
    }
    #tail-log {
        height: 1fr;
        width: 100%;
    }
    #mods-summary {
        margin-bottom: 1;
        color: $text-muted;
    }
    #mods-list {
        height: 1fr;
        border: solid green;
        margin-bottom: 1;
    }
    #modpack-help, #schedule-help, #bot-config-help, #bot-service-help {
        width: 100%;
        margin-bottom: 1;
        color: $text-muted;
    }
    #raw-config-help {
        width: 100%;
        margin-bottom: 1;
        color: $text-muted;
    }
    #raw-config-editor {
        width: 100%;
        height: 1fr;
        border: solid green;
        margin-bottom: 1;
    }
    #bot-status-log {
        height: 10;
        border: solid green;
        margin-bottom: 1;
    }
    #modpack-source-note {
        width: 100%;
        margin-bottom: 1;
        color: $text-muted;
    }
    #modpack-suggestions {
        height: 10;
        border: solid green;
        margin-bottom: 1;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit", _("Quit"), show=True),
        Binding("r", "refresh_root_menu", _("Refresh"), show=True),
    ]

    def __init__(self, instance: str = paths.DEFAULT_INSTANCE_NAME, **kwargs):
        super().__init__(**kwargs)
        self.instance = instance
        self._main_menu: VerticalGroup | None = None
        self._main_menu_actions: HorizontalScroll | None = None
        self._main_menu_content: VerticalScroll | None = None
        self._main_menu_refresh_lock = Lock()

    def on_mount(self) -> None:
        """Kick off small background health checks once the main menu is shown."""
        self._update_main_menu_header(discover(instance=self.instance, save=False))
        self.ensure_bot_runtime_task()

    @work(exclusive=True, thread=True)
    def ensure_bot_runtime_task(self) -> None:
        """Auto-heal the optional Telegram bot when it was already installed earlier."""
        results = ensure_bot_service_runtime(self.instance)
        if not results:
            return

        failures = [result.message for result in results if not result.success]
        if failures:
            self.call_from_thread(
                self.notify,
                tr("Telegram bot auto-start check failed: {error}", error=failures[0]),
                title=_("Telegram Bot"),
                severity="warning",
            )
            return

        self.call_from_thread(
            self.notify,
            _("Recovered Telegram bot service automatically after host boot."),
            title=_("Telegram Bot"),
        )

    def _main_menu_buttons(self, state: ServerState) -> list[Button]:
        """Build the current root action bar buttons."""
        buttons: list[Button] = []
        for entry in build_main_menu_entries(state):
            if entry.kind != "button":
                continue

            buttons.append(
                Button(
                    self._main_menu_button_label(entry.widget_id),
                    id=entry.widget_id,
                    variant=entry.variant or "default",
                )
            )
        return buttons

    def _main_menu_badge_text(self, state: ServerState) -> str:
        if state.server_running:
            return _("Running")
        if state.server_installed:
            return _("Stopped")
        if state.has_install_evidence():
            return _("Repair")
        return _("Setup")

    def _main_menu_status_summary(self, state: ServerState) -> str:
        installed_text = _("Yes") if state.server_installed else _("No")
        runtime_text = self._main_menu_badge_text(state)
        summary = [
            tr("Installed: {value}", value=installed_text),
            tr("Status: {value}", value=runtime_text),
        ]

        if state.config_exists:
            summary.append(
                tr(
                    "Ports: game {game} / A2S {a2s} / RCON {rcon}",
                    game=state.ports.game or _("Unknown"),
                    a2s=state.ports.a2s or _("Unknown"),
                    rcon=state.ports.rcon or _("Unknown"),
                )
            )
        return "  |  ".join(summary)

    def _main_menu_content_widgets(self, state: ServerState) -> list[Widget]:
        """Build the current root content panel."""
        content_title = _("Dashboard") if state.server_installed else _("Setup Actions")
        widgets: list[Widget] = [
            Label(content_title, id="main-content-title"),
            Label(self._main_menu_status_summary(state), id="main-menu-status"),
        ]

        if state.has_install_evidence() and not state.server_installed:
            widgets.append(
                Label(
                    _(
                        "Incomplete server installation detected. "
                        "Use Repair Installation to finish validation."
                    ),
                    id="install-warning",
                )
            )

        if state.server_installed:
            widgets.extend(
                [
                    Label(tr("Service: {value}", value=state.service_name)),
                    Label(tr("Config path: {value}", value=state.config_path or _("Unknown"))),
                    Label(
                        tr("Server directory: {value}", value=state.install_dir or _("Unknown"))
                    ),
                ]
            )
        else:
            widgets.append(Label(_("Server is not installed."), id="main-shell-note"))

        return widgets

    def _update_main_menu_header(self, state: ServerState) -> None:
        server_name = get_instance_server_name(self.instance)
        self.query_one("#main-server-name", Label).update(
            server_name or _("Server name not configured")
        )
        self.query_one("#main-instance-id", Label).update(
            tr("Instance: {instance}", instance=self.instance)
        )
        self.query_one("#main-status-summary", Label).update(
            self._main_menu_status_summary(state)
        )

        badge = self.query_one("#main-runtime-badge", Label)
        badge_text = self._main_menu_badge_text(state)
        badge.update(badge_text)
        if state.server_running:
            badge.styles.color = "green"
        elif state.server_installed:
            badge.styles.color = "red"
        else:
            badge.styles.color = "yellow"

    def _main_menu_button_label(self, widget_id: str) -> str:
        """Return the current label for a main menu button."""
        if widget_id == "btn_manage":
            return _("Manage Existing Server >>")
        if widget_id == "btn_install":
            return _("Install New Server")
        if widget_id == "btn_repair":
            return _("Repair Installation")
        if widget_id == "btn_detect":
            return _("Detect Existing Server")
        if widget_id == "btn_host_tests":
            return _("Run Host Tests")
        if widget_id == "btn_lang":
            return _("Language:") + f" {get_current_lang_name()}"
        if widget_id == "btn_exit":
            return _("Exit")
        return widget_id

    async def refresh_main_menu(self, *, save: bool = False) -> ServerState:
        """Re-run discovery and rebuild the root menu without duplicate widget IDs."""
        async with self._main_menu_refresh_lock:
            state = discover(instance=self.instance, save=save)
            if self._main_menu_actions is None or self._main_menu_content is None:
                return state

            self._update_main_menu_header(state)
            async with self._main_menu_actions.batch():
                await self._main_menu_actions.remove_children()
                await self._main_menu_actions.mount_all(self._main_menu_buttons(state))
            async with self._main_menu_content.batch():
                await self._main_menu_content.remove_children()
                await self._main_menu_content.mount_all(
                    self._main_menu_content_widgets(state)
                )
            return state

    def request_main_menu_refresh(self, *, save: bool = False) -> None:
        """Schedule a root menu refresh from sync or worker callbacks."""
        self.run_worker(
            self.refresh_main_menu(save=save),
            name="refresh-main-menu",
            group="main-menu",
            exclusive=True,
            exit_on_error=False,
        )

    def action_refresh_root_menu(self) -> None:
        self.request_main_menu_refresh(save=False)

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield Header(show_clock=True)
        state = discover(instance=self.instance, save=False)
        with VerticalGroup(id="main-menu") as menu:
            self._main_menu = menu
            with VerticalGroup(id="main-shell-header"):
                with HorizontalGroup(id="main-title-row"):
                    with VerticalGroup(id="main-title-block"):
                        yield Label("armactl", id="main-shell-title")
                        yield Label(
                            get_instance_server_name(self.instance)
                            or _("Server name not configured"),
                            id="main-server-name",
                        )
                        yield Label(
                            tr("Instance: {instance}", instance=self.instance),
                            id="main-instance-id",
                        )
                    yield Label(
                        self._main_menu_badge_text(state),
                        id="main-runtime-badge",
                    )
                yield Label(
                    self._main_menu_status_summary(state),
                    id="main-status-summary",
                )

            with HorizontalScroll(id="main-action-bar") as action_bar:
                self._main_menu_actions = action_bar
                yield from self._main_menu_buttons(state)

            with VerticalScroll(id="main-content") as content:
                self._main_menu_content = content
                yield from self._main_menu_content_widgets(state)

        yield Footer()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Event handler called when a button is pressed."""
        if event.button.id == "btn_exit":
            self.exit(0)
        elif event.button.id == "btn_detect":
            state = await self.refresh_main_menu(save=True)
            if state.server_installed:
                self.notify(
                    _("Server files detected. Main menu updated."),
                    title=_("Success"),
                )
            elif state.has_install_evidence():
                self.notify(
                    _(
                        "Incomplete server installation detected. "
                        "Use Repair Installation to finish validation."
                    ),
                    severity="warning",
                )
            else:
                self.notify(
                    _("No server installation found at default paths."),
                    severity="error",
                )
        elif event.button.id == "btn_lang":
            toggle_lang()
            self.notify(
                _("Language changed! Please exit and run armactl again to apply changes."),
                title=_("Language Swapped"),
                timeout=5,
            )
        elif event.button.id == "btn_manage":
            from armactl.tui.screens import ManageScreen

            self.push_screen(ManageScreen(instance=self.instance))
        elif event.button.id == "btn_install":
            from armactl.tui.screens import InstallScreen

            self.push_screen(
                InstallScreen(
                    instance=self.instance,
                    title=tr("Installing Server -> {instance}", instance=self.instance),
                )
            )
        elif event.button.id == "btn_repair":
            from armactl.tui.screens import RepairScreen

            self.push_screen(
                RepairScreen(
                    instance=self.instance,
                    title=tr("Repairing Server -> {instance}", instance=self.instance),
                )
            )
        elif event.button.id == "btn_host_tests":
            from armactl.tui.screens import HostTestsScreen

            self.push_screen(
                HostTestsScreen(
                    instance=self.instance,
                    title=_("Running Host Tests"),
                )
            )


def run_tui(instance: str) -> None:
    """Entry point for the TUI."""
    app = ArmaCtlApp(instance=instance)
    reply = app.run()
    _restore_terminal_state()

    sys.exit(reply or 0)
