"""Screens for the Textual TUI."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.containers import HorizontalGroup, VerticalGroup
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
        ("c", "copy_output", "Copy Output"),
    ]

    def __init__(self, instance: str, title: str, **kwargs):
        super().__init__(**kwargs)
        self.instance = instance
        self.worker_title = title
        self._output_lines: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalGroup():
            yield Label(self.worker_title, id="screen-title")
            yield RichLog(id="task-log", highlight=True, markup=True)
            with HorizontalGroup(id="task-actions"):
                yield Button("Copy Output", id="btn_copy_output", variant="primary")
                yield Button(
                    "Close Task (Running...)",
                    id="btn_close",
                    variant="default",
                    disabled=True,
                )
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_copy_output":
            self.action_copy_output()
        elif event.button.id == "btn_close":
            self.app.pop_screen()

    def action_go_back(self) -> None:
        # Prevent going back if task is not finished
        btn = self.query_one("#btn_close", Button)
        if not btn.disabled:
             self.app.pop_screen()

    def action_copy_output(self) -> None:
        text = "\n".join(line for line in self._output_lines if line)
        if not text:
            self.app.notify("There is no output to copy yet.", severity="warning")
            return

        self.app.copy_to_clipboard(text)
        self.app.notify("Copied full output to clipboard.", title="Clipboard")

    def append_output(self, rendered: str, plain: str | None = None) -> None:
        """Append a line to the visible log and the copy buffer."""
        self._output_lines.append((plain if plain is not None else rendered).rstrip())
        self.query_one("#task-log", RichLog).write(rendered)

    def save_output_to_file(self, output_path: Path, lines: list[str] | None = None) -> None:
        """Persist the current buffered output to a text file."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        source_lines = self._output_lines if lines is None else lines
        text = "\n".join(line for line in source_lines).rstrip()
        if text:
            text += "\n"
        output_path.write_text(text, encoding="utf-8")

    def complete_task(self, *, label: str = "Return to Menu", variant: str = "success") -> None:
        """Enable task closing once the background action is finished."""
        btn = self.query_one("#btn_close", Button)
        btn.disabled = False
        btn.label = label
        btn.variant = variant


class InstallScreen(LogWorkerScreen):
    """Screen for running the server installation."""
    
    def on_mount(self) -> None:
        self.run_installation_task()

    @work(exclusive=True, thread=True)
    def run_installation_task(self) -> None:
        try:
            for message in run_install(self.instance):
                self.app.call_from_thread(self.append_output, message)
            self.app.call_from_thread(
                self.append_output,
                "[green]Installation completely finished![/green]",
                "Installation completely finished!",
            )
        except Exception as e:
            self.app.call_from_thread(
                self.append_output,
                f"[red]Installation failed: {e}[/red]",
                f"Installation failed: {e}",
            )

        self.app.call_from_thread(self.complete_task)


class RepairScreen(LogWorkerScreen):
    """Screen for running the server repair task."""
    
    def on_mount(self) -> None:
        self.run_repair_task()

    @work(exclusive=True, thread=True)
    def run_repair_task(self) -> None:
        state = discover(self.instance, save=False)
        try:
            # We call run_repair from backend
            for message in run_repair(self.instance, state.install_dir, state.config_path):
                self.app.call_from_thread(self.append_output, message)
            self.app.call_from_thread(
                self.append_output,
                "[green]Repair completed successfully![/green]",
                "Repair completed successfully!",
            )
        except Exception as e:
            self.app.call_from_thread(
                self.append_output,
                f"[red]Repair failed: {e}[/red]",
                f"Repair failed: {e}",
            )

        self.app.call_from_thread(self.complete_task)


class HostTestsScreen(LogWorkerScreen):
    """Screen for running repo-local host checks."""

    def on_mount(self) -> None:
        self.run_host_tests_task()

    @work(exclusive=True, thread=True)
    def run_host_tests_task(self) -> None:
        import subprocess

        saved_output: list[str] = []
        project_root = Path(__file__).resolve().parents[3]
        script_path = project_root / "scripts" / "run-host-tests"
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        log_path = P.logs_dir(self.instance) / f"host-tests-{timestamp}.log"

        if not script_path.exists():
            self.app.call_from_thread(
                self.append_output,
                f"[red]Host test script not found: {script_path}[/red]",
                f"Host test script not found: {script_path}",
            )
            self.app.call_from_thread(self.complete_task, label="Close", variant="error")
            return

        cmd = ["/bin/sh", str(script_path)]
        saved_output.append(f"Running: {' '.join(cmd)}")
        self.app.call_from_thread(
            self.append_output,
            f"[cyan]Running:[/cyan] {' '.join(cmd)}",
            f"Running: {' '.join(cmd)}",
        )
        saved_output.append(f"Working directory: {project_root}")
        self.app.call_from_thread(
            self.append_output,
            f"[cyan]Working directory:[/cyan] {project_root}",
            f"Working directory: {project_root}",
        )
        saved_output.append(f"Log file: {log_path}")
        self.app.call_from_thread(
            self.append_output,
            f"[cyan]Log file:[/cyan] {log_path}",
            f"Log file: {log_path}",
        )

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=project_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except Exception as e:
            self.app.call_from_thread(
                self.append_output,
                f"[red]Failed to start host tests: {e}[/red]",
                f"Failed to start host tests: {e}",
            )
            self.app.call_from_thread(self.complete_task, label="Close", variant="error")
            return

        assert proc.stdout is not None
        for line in proc.stdout:
            text = line.rstrip()
            saved_output.append(text)
            self.app.call_from_thread(self.append_output, text)

        return_code = proc.wait()
        if return_code == 0:
            saved_output.append("Host tests finished successfully.")
            self.app.call_from_thread(
                self.append_output,
                "[green]Host tests finished successfully.[/green]",
                "Host tests finished successfully.",
            )
            self.app.call_from_thread(
                self.app.notify,
                f"Saved host test log to {log_path}",
                title="Host Tests Log",
            )
            self.app.call_from_thread(self.complete_task)
        else:
            saved_output.append(f"Host tests failed with exit code {return_code}.")
            self.app.call_from_thread(
                self.append_output,
                f"[red]Host tests failed with exit code {return_code}.[/red]",
                f"Host tests failed with exit code {return_code}.",
            )
            self.app.call_from_thread(
                self.app.notify,
                f"Saved failing host test log to {log_path}",
                title="Host Tests Log",
                severity="warning",
            )
            self.app.call_from_thread(self.complete_task, label="Close", variant="error")
        self.save_output_to_file(log_path, saved_output)


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
        from armactl.i18n import _
        state = discover(self.instance, save=False)
        lbl = self.query_one("#server-status", Label)
        btn_toggle = self.query_one("#btn_toggle", Button)
        
        if state.server_running:
            lbl.update("[bold green]🟢 SERVER IS RUNNING[/bold green]")
            btn_toggle.label = _("Stop")
            btn_toggle.variant = "error"
        else:
            lbl.update("[bold red]🔴 SERVER IS STOPPED[/bold red]")
            btn_toggle.label = _("Start")
            btn_toggle.variant = "success"

    def compose(self) -> ComposeResult:
        from armactl.i18n import _
        yield Header()
        with VerticalGroup(id="manage-container"):
            yield Label(f"Manage Server: {self.instance}", id="screen-title")
            yield Label(_("Loading status..."), id="server-status")
            
            from textual.containers import HorizontalGroup
            with HorizontalGroup(id="control-buttons"):
                yield Button("...", id="btn_toggle", variant="primary")
                yield Button(_("Restart"), id="btn_restart", variant="warning")
                
            yield Button(_("Edit Configuration"), id="btn_config", variant="success")
            yield Button(_("Mods Manager"), id="btn_mods", variant="primary")
            yield Button(_("Maintenance / Cleanup"), id="btn_cleanup", variant="warning")
            yield Button(_("View Live Logs"), id="btn_logs", variant="primary")
            yield Button(_("Status Details"), id="btn_status", variant="default")
            yield Button(_("Check Ports"), id="btn_ports", variant="default")
            yield Button(_("Back to Main Menu"), id="btn_back", variant="default")
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

        elif event.button.id == "btn_mods":
            self.app.push_screen(ModManagerScreen(self.instance))
            
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
        from armactl.i18n import _
        yield Header()
        with VerticalGroup(id="info-container"):
            yield Label(_("Maintenance & Cleanup: ") + f"{self.instance}", id="screen-title")
            yield RichLog(id="info-log", markup=True)
            with HorizontalGroup(id="control-buttons"):
                yield Button(_("Clean Junk Files"), id="btn_clean_now", variant="warning")
                yield Button(_("Back"), id="btn_back", variant="default")
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
        from armactl.i18n import _
        yield Header()
        yield Label(f"Edit Config: {self.instance}", id="screen-title")
        
        from textual.containers import VerticalScroll, HorizontalGroup
        from textual.widgets import Input
        with VerticalScroll(id="config-editor"):
            yield Label(_("Server Name:"))
            yield Input(id="inp_name")
            yield Label(_("Scenario ID:"))
            yield Input(id="inp_scenario")
            yield Label(_("Max Players:"))
            yield Input(id="inp_players", type="integer")
            
            yield Label(_("Game Port (UDP):"))
            yield Input(id="inp_game_port", type="integer")
            yield Label(_("A2S Port (UDP):"))
            yield Input(id="inp_a2s_port", type="integer")
            yield Label(_("RCON Port (TCP/UDP):"))
            yield Input(id="inp_rcon_port", type="integer")
            
            yield Label(_("Game Password (for players):"))
            yield Input(id="inp_game_pass", placeholder=_("Leave empty for open public server"))
            yield Label(_("Admin Password:"))
            yield Input(id="inp_admin_pass")
            yield Label(_("RCON Password:"))
            yield Input(id="inp_rcon_pass")
            
            with HorizontalGroup(id="control-buttons"):
                yield Button(_("Save Config"), id="btn_save", variant="success")
                yield Button(_("Save & Restart"), id="btn_save_restart", variant="warning")
                yield Button(_("Cancel"), id="btn_cancel", variant="error")
                
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


class ModPackFileScreen(Screen):
    """Prompt screen for mod pack import/export file paths."""

    BINDINGS = [
        ("b", "cancel", "Back"),
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(self, title: str, mode: str, default_path: str = "", **kwargs):
        super().__init__(**kwargs)
        self._title = title
        self._mode = mode
        self._default_path = default_path

    def compose(self) -> ComposeResult:
        from textual.containers import HorizontalGroup
        from textual.widgets import Input

        yield Header()
        with VerticalGroup(id="modpack-dialog"):
            yield Label(self._title, id="screen-title")

            if self._mode == "import":
                help_text = "Import accepts either an exported mod pack JSON or a full config.json with game.mods."
                placeholder = "Path to mod pack JSON or config.json"
            else:
                help_text = "Export writes the current mod list as a standalone JSON mod pack."
                placeholder = "Path to save mod pack JSON"

            yield Label(help_text, id="modpack-help")
            yield Input(value=self._default_path, placeholder=placeholder, id="inp_modpack_path")

            with HorizontalGroup():
                if self._mode == "import":
                    yield Button("Import (Append)", id="btn_import_append", variant="success")
                    yield Button("Import (Replace)", id="btn_import_replace", variant="warning")
                else:
                    yield Button("Export", id="btn_export_modpack", variant="success")
                yield Button("Cancel", id="btn_cancel_modpack", variant="error")
        yield Footer()

    def on_mount(self) -> None:
        from textual.widgets import Input

        self.query_one("#inp_modpack_path", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        from textual.widgets import Input

        if event.button.id == "btn_cancel_modpack":
            self.dismiss(None)
            return

        file_path = self.query_one("#inp_modpack_path", Input).value.strip()
        if not file_path:
            self.app.notify("File path is required.", severity="error")
            return

        if event.button.id == "btn_export_modpack":
            self.dismiss(("export", file_path))
        elif event.button.id == "btn_import_append":
            self.dismiss(("append", file_path))
        elif event.button.id == "btn_import_replace":
            self.dismiss(("replace", file_path))


class ModManagerScreen(Screen):
    """Screen for managing Arma Reforger mods in config.json."""

    BINDINGS = [
        ("b", "pop_screen", "Back to Menu"),
        ("ctrl+r", "action_refresh_mods", "Refresh List"),
    ]

    def __init__(self, instance: str, **kwargs):
        super().__init__(**kwargs)
        self.instance = instance

    def compose(self) -> ComposeResult:
        from armactl.i18n import _
        from textual.containers import HorizontalGroup
        from textual.widgets import Input, ListView

        yield Header()
        with VerticalGroup(id="info-container"):
            yield Label(_("Mods Manager: ") + f"{self.instance}", id="screen-title")

            yield Input(id="inp_mod_id", placeholder=_("Paste Mod ID or Workshop String here..."))
            yield Input(id="inp_mod_name", placeholder=_("Name (Optional)"))
            yield Button(_("Add/Update Mod"), id="btn_add_mod", variant="success")

            yield Label(_("Installed Mods:"), id="mods-list-title")
            yield Label("Installed Mods: 0", id="mods-summary")
            yield ListView(id="mods-list")

            with HorizontalGroup():
                yield Button(_("Remove Selected"), id="btn_remove_mod", variant="error")
                yield Button(_("Deduplicate"), id="btn_dedupe_mods", variant="warning")

            with HorizontalGroup():
                yield Button(_("Import Mod Pack"), id="btn_import_pack", variant="primary")
                yield Button(_("Export Mod Pack"), id="btn_export_pack", variant="success")
                yield Button(_("Back"), id="btn_back", variant="default")
        yield Footer()

    def on_mount(self) -> None:
        self.action_refresh_mods()

    def _default_export_path(self) -> str:
        from armactl import paths as P

        return str(P.instance_root(self.instance) / "mods-export.json")

    def _handle_mod_pack_result(self, result: tuple[str, str] | None) -> None:
        from pathlib import Path

        from armactl import paths as P
        from armactl.mods_manager import export_mods, import_mods

        if result is None:
            return

        action, raw_path = result
        cfg = P.config_file(self.instance)
        file_path = Path(raw_path).expanduser()

        try:
            if action == "export":
                count = export_mods(cfg, file_path)
                self.app.notify(f"Exported {count} mods to {file_path}.", title="Mod Pack Export")
                return

            added, skipped = import_mods(cfg, file_path, append=(action == "append"))
            mode_label = "appended" if action == "append" else "replaced"
            self.app.notify(
                f"Mod pack {mode_label}: added {added}, skipped {skipped} duplicate(s).",
                title="Mod Pack Import",
            )
            self.action_refresh_mods()
        except Exception as e:
            self.app.notify(f"Mod pack operation failed: {e}", severity="error")

    def action_refresh_mods(self) -> None:
        from armactl import paths as P
        from armactl.mods_manager import get_mods
        from textual.widgets import ListItem, ListView

        cfg = P.config_file(self.instance)
        try:
            mods = get_mods(cfg)
        except Exception as e:
            self.app.notify(f"Error loading mods: {e}", severity="error")
            return

        self.query_one("#mods-summary", Label).update(f"Installed Mods: {len(mods)}")

        list_view = self.query_one("#mods-list", ListView)
        list_view.clear()

        if not mods:
            list_view.append(ListItem(Label("No mods installed.")))
            return

        for idx, mod in enumerate(mods, 1):
            mod_id = mod.get("modId", "Unknown")
            name = mod.get("name", "")
            display = f"[{idx}] {mod_id}"
            if name:
                display += f" - {name}"
            item = ListItem(Label(display), id=f"mod_item_{mod_id}")
            item.mod_id = mod_id  # type: ignore[attr-defined]
            list_view.append(item)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        import re

        from armactl import paths as P
        from armactl.mods import add_mod, dedupe_mods, remove_mod

        cfg = P.config_file(self.instance)

        if event.button.id == "btn_back":
            self.app.pop_screen()

        elif event.button.id == "btn_import_pack":
            self.app.push_screen(
                ModPackFileScreen("Import Mod Pack", "import"),
                self._handle_mod_pack_result,
            )

        elif event.button.id == "btn_export_pack":
            self.app.push_screen(
                ModPackFileScreen(
                    "Export Mod Pack",
                    "export",
                    default_path=self._default_export_path(),
                ),
                self._handle_mod_pack_result,
            )

        elif event.button.id == "btn_add_mod":
            from textual.widgets import Input

            inp_id = self.query_one("#inp_mod_id", Input)
            inp_name = self.query_one("#inp_mod_name", Input)
            raw_id = inp_id.value.strip()
            name = inp_name.value.strip()

            if not raw_id:
                self.app.notify("Mod string is required!", severity="error")
                return

            match = re.search(r"([0-9A-Fa-f]{10,24})", raw_id)
            if not match:
                self.app.notify("Could not find a valid Mod ID in the input!", severity="error")
                return

            mod_id = match.group(1).upper()

            is_new = add_mod(cfg, mod_id, name)
            if is_new:
                self.app.notify(f"Mod {mod_id} added successfully.")
            else:
                self.app.notify(f"Mod {mod_id} updated successfully.")

            inp_id.value = ""
            inp_name.value = ""
            self.action_refresh_mods()

        elif event.button.id == "btn_remove_mod":
            from textual.widgets import ListView

            list_view = self.query_one("#mods-list", ListView)
            if list_view.highlighted_child is None:
                self.app.notify("Select a mod to remove first.", severity="warning")
                return

            mod_id = getattr(list_view.highlighted_child, "mod_id", None)
            if mod_id:

                def confirm_remove(confirm: bool):
                    if confirm:
                        success = remove_mod(cfg, mod_id)
                        if success:
                            self.app.notify(f"Removed mod {mod_id}.")
                            self.action_refresh_mods()
                        else:
                            self.app.notify(f"Mod {mod_id} not found.", severity="error")

                self.app.push_screen(
                    ConfirmScreen(f"Are you sure you want to remove Mod '{mod_id}'?"),
                    confirm_remove,
                )

        elif event.button.id == "btn_dedupe_mods":
            count = dedupe_mods(cfg)
            self.app.notify(f"Deduped mods. Reclaimed {count} duplicates.")
            if count > 0:
                self.action_refresh_mods()
