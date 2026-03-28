"""Main TUI application."""

from __future__ import annotations

import sys
from textual.app import App, ComposeResult
from textual.containers import VerticalGroup
from textual.widgets import Header, Footer, Button, Label
from textual.binding import Binding

from armactl.discovery import discover
from armactl import paths as P

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
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
    ]

    def __init__(self, instance: str = P.DEFAULT_INSTANCE_NAME, **kwargs):
        super().__init__(**kwargs)
        self.instance = instance

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        from armactl.i18n import _, toggle_lang, _current_lang
        yield Header(show_clock=True)
        with VerticalGroup(id="main-menu"):
            yield Label(f"Arma Reforger Manager [{self.instance}]", id="title")
            
            # Conditionally show buttons based on install state
            state = discover(instance=self.instance, save=False)
            
            if state.server_installed:
                yield Button(_("Manage Existing Server >>"), id="btn_manage", variant="primary")
            else:
                yield Button(_("Install New Server"), id="btn_install", variant="success")
                
            yield Button(_("Repair Installation"), id="btn_repair", variant="warning")
            lang_label = "Switch Language (UA)" if _current_lang == "en" else "Змінити мову (EN)"
            yield Button(lang_label, id="btn_lang", variant="default")
            yield Button(_("Exit"), id="btn_exit", variant="error")
            
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Event handler called when a button is pressed."""
        if event.button.id == "btn_exit":
            self.exit(0)
        elif event.button.id == "btn_lang":
            from armactl.i18n import toggle_lang, _
            toggle_lang()
            lbl = self.query_one("#btn_lang", Button)
            self.notify("Language changed! Please exit and run armactl again to apply changes.", title="Language Swapped", timeout=5)
        elif event.button.id == "btn_manage":
            from armactl.tui.screens import ManageScreen
            self.push_screen(ManageScreen(instance=self.instance))
        elif event.button.id == "btn_install":
            from armactl.tui.screens import InstallScreen
            self.push_screen(InstallScreen(instance=self.instance, title=f"Installing Server -> {self.instance}"))
        elif event.button.id == "btn_repair":
            from armactl.tui.screens import RepairScreen
            self.push_screen(RepairScreen(instance=self.instance, title=f"Repairing Server -> {self.instance}"))

def run_tui(instance: str) -> None:
    """Entry point for the TUI."""
    app = ArmaCtlApp(instance=instance)
    reply = app.run()
    sys.exit(reply or 0)
