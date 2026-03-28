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

    def on_screen_resume(self) -> None:
        """Auto-refresh state when returning from a sub-screen like ConfigEditor."""
        self.action_refresh_state()

    def action_refresh_state(self) -> None:
        state = discover(self.instance, save=False)
        lbl = self.query_one("#server-status", Label)
        btn_toggle = self.query_one("#btn_toggle", Button)
        
        if state.server_running:
            lbl.update("[bold green]🟢 SERVER IS RUNNING[/bold green]")
            btn_toggle.label = "Stop"
            btn_toggle.variant = "error"
        else:
            lbl.update("[bold red]🔴 SERVER IS STOPPED[/bold red]")
            btn_toggle.label = "Start"
            btn_toggle.variant = "success"

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalGroup(id="manage-container"):
            yield Label(f"Manage Server: {self.instance}", id="screen-title")
            yield Label("Loading status...", id="server-status")
            
            from textual.containers import HorizontalGroup
            with HorizontalGroup(id="control-buttons"):
                yield Button("...", id="btn_toggle", variant="primary")
                yield Button("Restart", id="btn_restart", variant="warning")
                
            yield Button("Edit Configuration", id="btn_config", variant="success")
            yield Button("Maintenance / Cleanup", id="btn_cleanup", variant="warning")
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
            
        elif event.button.id == "btn_toggle":
            state = discover(self.instance, save=False)
            if state.server_running:
                def check_stop(confirm: bool):
                    if confirm:
                        res = stop_service(service_name)
                        self.app.notify(res.message, title="Stop")
                        self.action_refresh_state()
                self.app.push_screen(ConfirmScreen("Are you sure you want to STOP the server?"), check_stop)
            else:
                res = start_service(service_name)
                self.app.notify(res.message, title="Start")
                self.action_refresh_state()
            
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
            arr = ports.check_server_ports(state.ports.game, state.ports.a2s, state.ports.rcon)
            text_lines = []
            for name, info in arr.items():
                status = "[green]✓ listening[/green]" if info["listening"] else "[red]✗ closed[/red]"
                text_lines.append(f"{name:<10} | {info['port']:<6} | {status}")
            self.app.push_screen(InfoViewerScreen("Ports Status", "\n".join(text_lines)))
            
        elif event.button.id == "btn_config":
            self.app.push_screen(ConfigEditorScreen(self.instance))
            
        elif event.button.id == "btn_cleanup":
            self.app.push_screen(CleanupScreen(self.instance))


class CleanupScreen(Screen):
    """Screen for analyzing and cleaning up server logs and stale files."""
    
    BINDINGS = [
        ("b", "pop_screen", "Back"),
        ("c", "clean_junk", "Clean Now"),
    ]
    
    def __init__(self, instance: str, **kwargs):
        super().__init__(**kwargs)
        self.instance = instance

    def compose(self) -> ComposeResult:
        from textual.containers import VerticalGroup, HorizontalGroup
        yield Header()
        with VerticalGroup(id="info-container"):
            yield Label(f"Maintenance & Cleanup: {self.instance}", id="screen-title")
            yield RichLog(id="info-log", markup=True)
            with HorizontalGroup(id="control-buttons"):
                yield Button("Clean Junk Files", id="btn_clean_now", variant="warning")
                yield Button("Back", id="btn_back", variant="default")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_stats()

    def refresh_stats(self) -> None:
        from armactl.cleaner import get_junk_stats, format_size
        
        stats = get_junk_stats(self.instance)
        log = self.query_one("#info-log", RichLog)
        btn = self.query_one("#btn_clean_now", Button)
        
        log.clear()
        lines = []
        lines.append("[bold cyan]Server Junk Analysis[/bold cyan]")
        lines.append("─────────────────────────")
        
        sz_logs = format_size(stats['logs']['size'])
        lines.append(f"• Old Logs:       {stats['logs']['count']} files ({sz_logs})")
        
        sz_dumps = format_size(stats['dumps']['size'])
        lines.append(f"• Crash Dumps:    {stats['dumps']['count']} files ({sz_dumps})")
        
        sz_backups = format_size(stats['backups']['size'])
        lines.append(f"• Stale Backups:  {stats['backups']['count']} files ({sz_backups})")
        
        lines.append("─────────────────────────")
        tot = format_size(stats['total_size'])
        
        if stats['total_size'] > 0:
            lines.append(f"[bold red]Total Recoverable Space: {tot}[/bold red]")
            btn.disabled = False
        else:
            lines.append("[bold green]System is clean! Nothing to remove.[/bold green]")
            btn.disabled = True
            
        log.write("\n".join(lines))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_back":
            self.app.pop_screen()
        elif event.button.id == "btn_clean_now":
            def confirm_cleanup(confirm: bool):
                if confirm:
                    from armactl.cleaner import clean_junk, format_size
                    res = clean_junk(self.instance)
                    freed = format_size(res["freed_bytes"])
                    count = res["files_deleted"]
                    self.app.notify(f"Cleaned {count} files, freed {freed}!", title="Cleanup Success")
                    self.refresh_stats()
            self.app.push_screen(ConfirmScreen("Are you sure you want to permanently delete these files?"), confirm_cleanup)


class ConfigEditorScreen(Screen):
    """Screen for editing the config.json file directly from TUI."""
    
    BINDINGS = [
        ("b", "pop_screen", "Back without saving"),
    ]

    def __init__(self, instance: str, **kwargs):
        super().__init__(**kwargs)
        self.instance = instance
        self.config_path = ""
        self.config_data = {}

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label(f"Edit Config: {self.instance}", id="screen-title")
        
        from textual.containers import VerticalScroll, HorizontalGroup
        from textual.widgets import Input
        with VerticalScroll(id="config-editor"):
            yield Label("Server Name:")
            yield Input(id="inp_name")
            yield Label("Scenario ID:")
            yield Input(id="inp_scenario")
            yield Label("Max Players:")
            yield Input(id="inp_players", type="integer")
            
            yield Label("Game Port (UDP):")
            yield Input(id="inp_game_port", type="integer")
            yield Label("A2S Port (UDP):")
            yield Input(id="inp_a2s_port", type="integer")
            yield Label("RCON Port (TCP/UDP):")
            yield Input(id="inp_rcon_port", type="integer")
            
            yield Label("Game Password (for players):")
            yield Input(id="inp_game_pass", placeholder="Leave empty for open public server")
            yield Label("Admin Password:")
            yield Input(id="inp_admin_pass")
            yield Label("RCON Password:")
            yield Input(id="inp_rcon_pass")
            
            with HorizontalGroup(id="control-buttons"):
                yield Button("Save Config", id="btn_save", variant="success")
                yield Button("Save & Restart", id="btn_save_restart", variant="warning")
                yield Button("Cancel", id="btn_cancel", variant="error")
                
        yield Footer()

    def on_mount(self) -> None:
        state = discover(self.instance, save=False)
        self.config_path = state.config_path
        if not state.config_exists:
            self.app.notify("Config file missing!", severity="error")
            self.app.pop_screen()
            return
            
        from armactl.config_manager import load_config
        try:
            self.config_data = load_config(self.config_path)
        except Exception as e:
            self.app.notify(f"Cannot parse config: {e}", severity="error")
            self.app.pop_screen()
            return

        game = self.config_data.get("game", {})
        rcon = self.config_data.get("rcon", {})
        a2s = self.config_data.get("a2s", {})
        
        from textual.widgets import Input
        self.query_one("#inp_name", Input).value = game.get("name", "")
        self.query_one("#inp_scenario", Input).value = game.get("scenarioId", "")
        self.query_one("#inp_players", Input).value = str(game.get("maxPlayers", 64))
        
        self.query_one("#inp_game_port", Input).value = str(self.config_data.get("bindPort", 2001))
        self.query_one("#inp_a2s_port", Input).value = str(a2s.get("port", 17777))
        self.query_one("#inp_rcon_port", Input).value = str(rcon.get("port", 19999))
        
        self.query_one("#inp_game_pass", Input).value = game.get("password", "")
        self.query_one("#inp_admin_pass", Input).value = game.get("passwordAdmin", "")
        self.query_one("#inp_rcon_pass", Input).value = rcon.get("password", "")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id in ("btn_save", "btn_save_restart"):
            self.save_and_exit(restart=(event.button.id == "btn_save_restart"))
        elif event.button.id == "btn_cancel":
            self.app.pop_screen()

    def save_and_exit(self, restart: bool) -> None:
        from textual.widgets import Input
        
        game = self.config_data.get("game", {})
        rcon = self.config_data.get("rcon", {})
        a2s = self.config_data.get("a2s", {})

        game["name"] = self.query_one("#inp_name", Input).value
        game["scenarioId"] = self.query_one("#inp_scenario", Input).value
        try:
            game["maxPlayers"] = int(self.query_one("#inp_players", Input).value)
        except ValueError:
            pass
            
        try:
            self.config_data["bindPort"] = int(self.query_one("#inp_game_port", Input).value)
            self.config_data["publicPort"] = int(self.query_one("#inp_game_port", Input).value)
        except ValueError:
            pass
        try:
            a2s["port"] = int(self.query_one("#inp_a2s_port", Input).value)
            self.config_data["a2s"] = a2s
        except ValueError:
            pass
        try:
            rcon["port"] = int(self.query_one("#inp_rcon_port", Input).value)
            self.config_data["rcon"] = rcon
        except ValueError:
            pass

        game["password"] = self.query_one("#inp_game_pass", Input).value
        game["passwordAdmin"] = self.query_one("#inp_admin_pass", Input).value
        rcon["password"] = self.query_one("#inp_rcon_pass", Input).value
        
        self.config_data["game"] = game
        self.config_data["rcon"] = rcon

        from armactl.config_manager import save_config
        
        try:
            save_config(self.config_path, self.config_data, backup=True)
            self.app.notify("Config saved successfully (backup created automatically)", title="Success")
            
            if restart:
                from armactl.service_manager import restart_service
                service_name = f"armareforger@{self.instance}.service" if self.instance != "default" else P.SERVICE_NAME
                res = restart_service(service_name)
                self.app.notify(res.message, title="Restart")
            
            self.app.pop_screen()
        except Exception as e:
            self.app.notify(f"Error saving config: {e}", severity="error")
