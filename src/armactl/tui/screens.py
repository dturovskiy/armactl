"""Screens for the Textual TUI."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.containers import VerticalGroup
from textual.screen import Screen
from textual.widgets import Header, Footer, RichLog, Button, Label

from armactl.installer import run_install
from armactl.repair import run_repair
from armactl.discovery import discover
from armactl import paths as P

class LogWorkerScreen(Screen):
    """A generic screen that runs a background task and displays logs."""
    
    BINDINGS = [
        ("b", "go_back", "Back to Menu"),
    ]

    def __init__(self, instance: str, title: str, **kwargs):
        super().__init__(**kwargs)
        self.instance = instance
        self.worker_title = title

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalGroup():
            yield Label(self.worker_title, id="screen-title")
            yield RichLog(id="task-log", highlight=True, markup=True)
            yield Button("Close Task (Running...)", id="btn_close", variant="default", disabled=True)
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_close":
            self.app.pop_screen()

    def action_go_back(self) -> None:
        # Prevent going back if task is not finished
        btn = self.query_one("#btn_close", Button)
        if not btn.disabled:
             self.app.pop_screen()


class InstallScreen(LogWorkerScreen):
    """Screen for running the server installation."""
    
    def on_mount(self) -> None:
        self.run_installation_task()

    @work(exclusive=True, thread=True)
    def run_installation_task(self) -> None:
        log_widget = self.query_one("#task-log", RichLog)
        try:
            for message in run_install(self.instance):
                self.app.call_from_thread(log_widget.write, message)
            self.app.call_from_thread(log_widget.write, "[green]Installation completely finished![/green]")
        except Exception as e:
            self.app.call_from_thread(log_widget.write, f"[red]Installation failed: {e}[/red]")
        
        def enable_close():
            btn = self.query_one("#btn_close", Button)
            btn.disabled = False
            btn.label = "Return to Menu"
            btn.variant = "success"
            
        self.app.call_from_thread(enable_close)


class RepairScreen(LogWorkerScreen):
    """Screen for running the server repair task."""
    
    def on_mount(self) -> None:
        self.run_repair_task()

    @work(exclusive=True, thread=True)
    def run_repair_task(self) -> None:
        log_widget = self.query_one("#task-log", RichLog)
        state = discover(self.instance, save=False)
        try:
            # We call run_repair from backend
            for message in run_repair(self.instance, state.install_dir, state.config_path):
                self.app.call_from_thread(log_widget.write, message)
            self.app.call_from_thread(log_widget.write, "[green]Repair completed successfully![/green]")
        except Exception as e:
            self.app.call_from_thread(log_widget.write, f"[red]Repair failed: {e}[/red]")
            
        def enable_close():
            btn = self.query_one("#btn_close", Button)
            btn.disabled = False
            btn.label = "Return to Menu"
            btn.variant = "success"
            
        self.app.call_from_thread(enable_close)


class ManageScreen(Screen):
    """Stump screen for Manage Server (Phase 9 placeholder)."""
    
    BINDINGS = [
        ("b", "pop_screen", "Back to Menu"),
    ]

    def __init__(self, instance: str, **kwargs):
        super().__init__(**kwargs)
        self.instance = instance

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalGroup():
            yield Label(f"Manage Server: {self.instance}", id="screen-title")
            yield Label("\n[Server Control Dashboard goes here specifically in Phase 9]", classes="wip-text")
            yield Button("Back to Main Menu", id="btn_back", variant="primary")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_back":
            self.app.pop_screen()
