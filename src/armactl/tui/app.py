"""Main TUI application."""

from __future__ import annotations

import os
import sys

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalGroup
from textual.widgets import Button, Footer, Header, Label

from armactl import paths
from armactl.discovery import discover
from armactl.i18n import _, get_current_lang_name, toggle_lang, tr


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
    #main-menu, #manage-container, #confirm-dialog {
        width: 50;
        height: auto;
        border: solid green;
        padding: 1 2;
        background: $surface;
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
    #raw-config-container {
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
    #server-status {
        content-align: center middle;
        width: 100%;
        margin-bottom: 1;
    }
    #control-buttons {
        height: auto;
        margin-bottom: 1;
    }
    #control-buttons Button {
        width: 1fr;
    }
    HorizontalGroup Button {
        width: 1fr;
        margin: 0 1;
    }
    Button {
        width: 100%;
        margin-bottom: 1;
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
    #modpack-help, #schedule-help {
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
    #modpack-suggestions Button {
        width: 100%;
    }
    """

    BINDINGS = [
        Binding("q", "quit", _("Quit"), show=True),
    ]

    def __init__(self, instance: str = paths.DEFAULT_INSTANCE_NAME, **kwargs):
        super().__init__(**kwargs)
        self.instance = instance

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield Header(show_clock=True)
        with VerticalGroup(id="main-menu"):
            yield Label(
                tr("Arma Reforger Manager [{instance}]", instance=self.instance),
                id="title",
            )

            state = discover(instance=self.instance, save=False)
            if state.server_installed:
                yield Button(_("Manage Existing Server >>"), id="btn_manage", variant="primary")
            else:
                yield Button(_("Install New Server"), id="btn_install", variant="success")

            yield Button(_("Repair Installation"), id="btn_repair", variant="warning")
            yield Button(_("Detect Existing Server"), id="btn_detect", variant="default")
            yield Button(_("Run Host Tests"), id="btn_host_tests", variant="primary")
            lang_label = _("Language:") + f" {get_current_lang_name()}"
            yield Button(lang_label, id="btn_lang", variant="default")
            yield Button(_("Exit"), id="btn_exit", variant="error")

        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Event handler called when a button is pressed."""
        if event.button.id == "btn_exit":
            self.exit(0)
        elif event.button.id == "btn_detect":
            state = discover(instance=self.instance, save=True)
            if state.server_installed:
                self.notify(
                    _("Server files detected! Restart app to see Manage screen."),
                    title=_("Success"),
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

