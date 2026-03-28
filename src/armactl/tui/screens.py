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


class ConfirmScreen(Screen):
    """A simple modal screen for yes/no confirmation."""
    def __init__(self, prompt: str, **kwargs):
        super().__init__(**kwargs)
        self.prompt = prompt

    def compose(self) -> ComposeResult:
        with VerticalGroup(id="confirm-dialog"):
            yield Label(self.prompt, id="confirm-prompt")
            from textual.containers import HorizontalGroup
            with HorizontalGroup():
                yield Button("Yes", id="btn_yes", variant="error")
                yield Button("No", id="btn_no", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_yes":
            self.dismiss(True)
        else:
            self.dismiss(False)


class TailLogScreen(Screen):
    """Screen for viewing live tailing logs via journalctl."""
    BINDINGS = [("q", "quit_logs", "Close Logs")]
    
    def __init__(self, instance: str, **kwargs):
        super().__init__(**kwargs)
        self.instance = instance
        self._tail_process = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label(f"Live Logs: {self.instance} (Press Q to exit)", id="screen-title")
        yield RichLog(id="tail-log", highlight=True, markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.run_tail_task()

    @work(exclusive=True, thread=True)
    def run_tail_task(self) -> None:
        log_widget = self.query_one("#tail-log", RichLog)
        
        service_name = f"armareforger@{self.instance}.service" if self.instance != "default" else P.SERVICE_NAME
        import subprocess
        
        self._tail_process = subprocess.Popen(
            ["sudo", "journalctl", "-u", service_name, "-f", "-n", "100"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        try:
            for line in iter(self._tail_process.stdout.readline, ""):
                if not line:
                    break
                self.app.call_from_thread(log_widget.write, line.strip())
        except Exception:
            pass

    def action_quit_logs(self) -> None:
        if self._tail_process:
            self._tail_process.terminate()
        self.app.pop_screen()


class InfoViewerScreen(Screen):
    """Generic screen to view static text information (like Ports or Status)."""
    BINDINGS = [("b", "pop_screen", "Back")]

    def __init__(self, title: str, content: str, **kwargs):
        super().__init__(**kwargs)
        self._title = title
        self._content = content

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalGroup(id="info-container"):
            yield Label(self._title, id="screen-title")
            yield RichLog(id="info-log", markup=True)
            yield Button("Back", id="btn_back", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        log = self.query_one("#info-log", RichLog)
        log.write(self._content)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_back":
            self.app.pop_screen()


class ManageScreen(Screen):
    """Dashboard screen for Manage Server."""
    
    BINDINGS = [
        ("b", "pop_screen", "Back to Menu"),
        ("r", "refresh_state", "Refresh Status"),
    ]

    def __init__(self, instance: str, **kwargs):
        super().__init__(**kwargs)
        self.instance = instance

    def on_mount(self) -> None:
        self.action_refresh_state()

    def action_refresh_state(self) -> None:
        state = discover(self.instance, save=False)
        lbl = self.query_one("#server-status", Label)
        if state.server_running:
            lbl.update("[bold green]🟢 SERVER IS RUNNING[/bold green]")
        else:
            lbl.update("[bold red]🔴 SERVER IS STOPPED[/bold red]")

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalGroup(id="manage-container"):
            yield Label(f"Manage Server: {self.instance}", id="screen-title")
            yield Label("Loading status...", id="server-status")
            
            from textual.containers import HorizontalGroup
            with HorizontalGroup(id="control-buttons"):
                yield Button("Start", id="btn_start", variant="success")
                yield Button("Stop", id="btn_stop", variant="error")
                yield Button("Restart", id="btn_restart", variant="warning")
                
            yield Button("View Live Logs", id="btn_logs", variant="primary")
            yield Button("Status Details", id="btn_status", variant="default")
            yield Button("Check Ports", id="btn_ports", variant="default")
            yield Button("Back to Main Menu", id="btn_back", variant="default")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        from armactl.service_manager import start_service, stop_service, restart_service
        service_name = f"armareforger@{self.instance}.service" if self.instance != "default" else P.SERVICE_NAME
        
        if event.button.id == "btn_back":
            self.app.pop_screen()
            
        elif event.button.id == "btn_start":
            res = start_service(service_name)
            self.app.notify(res.message, title="Start")
            self.action_refresh_state()
            
        elif event.button.id == "btn_stop":
            def check_stop(confirm: bool):
                if confirm:
                    res = stop_service(service_name)
                    self.app.notify(res.message, title="Stop")
                    self.action_refresh_state()
            self.app.push_screen(ConfirmScreen("Are you sure you want to STOP the server?"), check_stop)
            
        elif event.button.id == "btn_restart":
            def check_restart(confirm: bool):
                if confirm:
                    res = restart_service(service_name)
                    self.app.notify(res.message, title="Restart")
                    self.action_refresh_state()
            self.app.push_screen(ConfirmScreen("Are you sure you want to RESTART the server?"), check_restart)
            
        elif event.button.id == "btn_logs":
            self.app.push_screen(TailLogScreen(self.instance))

        elif event.button.id == "btn_status":
            state = discover(self.instance, save=False)
            import json
            text = json.dumps(state.to_dict(), indent=2)
            self.app.push_screen(InfoViewerScreen("Detailed Server Status", text))

        elif event.button.id == "btn_ports":
            state = discover(self.instance, save=False)
            if not state.config_exists:
                self.app.notify("Config missing. Cannot read ports.", title="Error", severity="error")
                return
            from armactl import ports
            arr = ports.check_all_ports(state.config_path, state.server_running)
            text_lines = []
            for p in arr:
                status = "[green]✓ listening[/green]" if p["listening"] else "[red]✗ closed[/red]"
                text_lines.append(f"{p['name']:<10} | {p['port']:<6} | {status}")
            self.app.push_screen(InfoViewerScreen("Ports Status", "\n".join(text_lines)))
