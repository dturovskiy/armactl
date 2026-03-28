"""Screens for the Textual TUI."""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.containers import HorizontalGroup, VerticalGroup, VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    TextArea,
)

from armactl import paths, ports
from armactl.cleaner import clean_junk, format_size, get_junk_stats
from armactl.config_manager import load_config, save_config, validate_config
from armactl.discovery import discover
from armactl.i18n import _, tr
from armactl.installer import run_install
from armactl.mods import add_mod, dedupe_mods, remove_mod
from armactl.mods_manager import (
    export_mods,
    get_mods,
    import_mods,
    preview_import_mods,
)
from armactl.repair import run_repair
from armactl.service_manager import (
    disable_service,
    enable_service,
    generate_services,
    get_timer_status,
    normalize_on_calendar,
    restart_service,
    service_unit_name,
    start_service,
    stop_service,
    timer_unit_name,
)


class LogWorkerScreen(Screen):
    """A generic screen that runs a background task and displays logs."""

    BINDINGS = [
        ("b", "go_back", _("Back to Menu")),
        ("c", "copy_output", _("Copy Output")),
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
                yield Button(_("Copy Output"), id="btn_copy_output", variant="primary")
                yield Button(
                    _("Close Task (Running...)"),
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
            self.app.notify(_("There is no output to copy yet."), severity="warning")
            return

        self.app.copy_to_clipboard(text)
        self.app.notify(_("Copied full output to clipboard."), title=_("Clipboard"))

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

    def complete_task(
        self,
        *,
        label: str | None = None,
        variant: str = "success",
    ) -> None:
        """Enable task closing once the background action is finished."""
        btn = self.query_one("#btn_close", Button)
        btn.disabled = False
        btn.label = label or _("Return to Menu")
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
                _("[green]Installation completely finished![/green]"),
                _("Installation completely finished!"),
            )
        except Exception as e:
            self.app.call_from_thread(
                self.append_output,
                tr("[red]Installation failed: {error}[/red]", error=e),
                tr("Installation failed: {error}", error=e),
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
                _("[green]Repair completed successfully![/green]"),
                _("Repair completed successfully!"),
            )
        except Exception as e:
            self.app.call_from_thread(
                self.append_output,
                tr("[red]Repair failed: {error}[/red]", error=e),
                tr("Repair failed: {error}", error=e),
            )

        self.app.call_from_thread(self.complete_task)


class HostTestsScreen(LogWorkerScreen):
    """Screen for running repo-local host checks."""

    def on_mount(self) -> None:
        self.run_host_tests_task()

    @work(exclusive=True, thread=True)
    def run_host_tests_task(self) -> None:
        saved_output: list[str] = []
        project_root = Path(__file__).resolve().parents[3]
        script_path = project_root / "scripts" / "run-host-tests"
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        log_path = paths.logs_dir(self.instance) / f"host-tests-{timestamp}.log"

        if not script_path.exists():
            self.app.call_from_thread(
                self.append_output,
                tr("[red]Host test script not found: {path}[/red]", path=script_path),
                tr("Host test script not found: {path}", path=script_path),
            )
            self.app.call_from_thread(self.complete_task, label=_("Close"), variant="error")
            return

        cmd = ["/bin/sh", str(script_path)]
        saved_output.append(tr("Running: {command}", command=" ".join(cmd)))
        self.app.call_from_thread(
            self.append_output,
            f"[cyan]{_('Running:')}[/cyan] {' '.join(cmd)}",
            tr("Running: {command}", command=" ".join(cmd)),
        )
        saved_output.append(tr("Working directory: {path}", path=project_root))
        self.app.call_from_thread(
            self.append_output,
            f"[cyan]{_('Working directory:')}[/cyan] {project_root}",
            tr("Working directory: {path}", path=project_root),
        )
        saved_output.append(tr("Log file: {path}", path=log_path))
        self.app.call_from_thread(
            self.append_output,
            f"[cyan]{_('Log file:')}[/cyan] {log_path}",
            tr("Log file: {path}", path=log_path),
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
                tr("[red]Failed to start host tests: {error}[/red]", error=e),
                tr("Failed to start host tests: {error}", error=e),
            )
            self.app.call_from_thread(self.complete_task, label=_("Close"), variant="error")
            return

        assert proc.stdout is not None
        for line in proc.stdout:
            text = line.rstrip()
            saved_output.append(text)
            self.app.call_from_thread(self.append_output, text)

        return_code = proc.wait()
        if return_code == 0:
            saved_output.append(_("Host tests finished successfully."))
            self.app.call_from_thread(
                self.append_output,
                _("[green]Host tests finished successfully.[/green]"),
                _("Host tests finished successfully."),
            )
            self.app.call_from_thread(
                self.app.notify,
                tr("Saved host test log to {path}", path=log_path),
                title=_("Host Tests Log"),
            )
            self.app.call_from_thread(self.complete_task)
        else:
            saved_output.append(
                tr("Host tests failed with exit code {code}.", code=return_code)
            )
            self.app.call_from_thread(
                self.append_output,
                tr("[red]Host tests failed with exit code {code}.[/red]", code=return_code),
                tr("Host tests failed with exit code {code}.", code=return_code),
            )
            self.app.call_from_thread(
                self.app.notify,
                tr("Saved failing host test log to {path}", path=log_path),
                title=_("Host Tests Log"),
                severity="warning",
            )
            self.app.call_from_thread(self.complete_task, label=_("Close"), variant="error")
        self.save_output_to_file(log_path, saved_output)


class ConfirmScreen(Screen):
    """A simple modal screen for yes/no confirmation."""
    def __init__(self, prompt: str, **kwargs):
        super().__init__(**kwargs)
        self.prompt = prompt

    def compose(self) -> ComposeResult:
        with VerticalGroup(id="confirm-dialog"):
            yield Label(self.prompt, id="confirm-prompt")
            with HorizontalGroup():
                yield Button(_("Yes"), id="btn_yes", variant="error")
                yield Button(_("No"), id="btn_no", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_yes":
            self.dismiss(True)
        else:
            self.dismiss(False)


class TailLogScreen(Screen):
    """Screen for viewing live tailing logs via journalctl."""
    BINDINGS = [("q", "quit_logs", _("Close Logs"))]

    def __init__(self, instance: str, **kwargs):
        super().__init__(**kwargs)
        self.instance = instance
        self._tail_process = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label(
            tr("Live Logs: {instance} (Press Q to exit)", instance=self.instance),
            id="screen-title",
        )
        yield RichLog(id="tail-log", highlight=True, markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.run_tail_task()

    @work(exclusive=True, thread=True)
    def run_tail_task(self) -> None:
        log_widget = self.query_one("#tail-log", RichLog)
        service_name = (
            f"armareforger@{self.instance}.service"
            if self.instance != "default"
            else paths.SERVICE_NAME
        )

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
    BINDINGS = [("b", "pop_screen", _("Back"))]

    def __init__(self, title: str, content: str, **kwargs):
        super().__init__(**kwargs)
        self._title = title
        self._content = content

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalGroup(id="info-container"):
            yield Label(self._title, id="screen-title")
            yield RichLog(id="info-log", markup=True)
            yield Button(_("Back"), id="btn_back", variant="primary")
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
        ("b", "pop_screen", _("Back to Menu")),
        ("r", "refresh_state", _("Refresh Status")),
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
            lbl.update(_("[bold green]SERVER IS RUNNING[/bold green]"))
            btn_toggle.label = _("Stop")
            btn_toggle.variant = "error"
        else:
            lbl.update(_("[bold red]SERVER IS STOPPED[/bold red]"))
            btn_toggle.label = _("Start")
            btn_toggle.variant = "success"

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalGroup(id="manage-container"):
            yield Label(tr("Manage Server: {instance}", instance=self.instance), id="screen-title")
            yield Label(_("Loading status..."), id="server-status")

            with HorizontalGroup(id="control-buttons"):
                yield Button("...", id="btn_toggle", variant="primary")
                yield Button(_("Restart"), id="btn_restart", variant="warning")

            yield Button(_("Edit Configuration"), id="btn_config", variant="success")
            yield Button(_("Raw Config JSON"), id="btn_config_raw", variant="default")
            yield Button(_("Restart Schedule"), id="btn_schedule", variant="primary")
            yield Button(_("Mods Manager"), id="btn_mods", variant="primary")
            yield Button(_("Maintenance / Cleanup"), id="btn_cleanup", variant="warning")
            yield Button(_("View Live Logs"), id="btn_logs", variant="primary")
            yield Button(_("Status Details"), id="btn_status", variant="default")
            yield Button(_("Check Ports"), id="btn_ports", variant="default")
            yield Button(_("Back to Main Menu"), id="btn_back", variant="default")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        service_name = (
            f"armareforger@{self.instance}.service"
            if self.instance != "default"
            else paths.SERVICE_NAME
        )

        if event.button.id == "btn_back":
            self.app.pop_screen()

        elif event.button.id == "btn_toggle":
            state = discover(self.instance, save=False)
            if state.server_running:

                def check_stop(confirm: bool):
                    if confirm:
                        res = stop_service(service_name)
                        self.app.notify(_(res.message), title=_("Stop"))
                        self.action_refresh_state()

                self.app.push_screen(
                    ConfirmScreen(_("Are you sure you want to STOP the server?")),
                    check_stop,
                )
            else:
                res = start_service(service_name)
                self.app.notify(_(res.message), title=_("Start"))
                self.action_refresh_state()

        elif event.button.id == "btn_restart":

            def check_restart(confirm: bool):
                if confirm:
                    res = restart_service(service_name)
                    self.app.notify(_(res.message), title=_("Restart"))
                    self.action_refresh_state()

            self.app.push_screen(
                ConfirmScreen(_("Are you sure you want to RESTART the server?")),
                check_restart,
            )

        elif event.button.id == "btn_logs":
            self.app.push_screen(TailLogScreen(self.instance))

        elif event.button.id == "btn_status":
            state = discover(self.instance, save=False)
            text = json.dumps(state.to_dict(), indent=2)
            self.app.push_screen(InfoViewerScreen(_("Detailed Server Status"), text))

        elif event.button.id == "btn_ports":
            state = discover(self.instance, save=False)
            if not state.config_exists:
                self.app.notify(
                    _("Config missing. Cannot read ports."),
                    title=_("Error"),
                    severity="error",
                )
                return
            arr = ports.check_server_ports(
                state.ports.game,
                state.ports.a2s,
                state.ports.rcon,
            )
            text_lines = []
            for name, info in arr.items():
                status = (
                    _("[green]OPEN listening[/green]")
                    if info["listening"]
                    else _("[red]CLOSED[/red]")
                )
                text_lines.append(f"{name:<10} | {info['port']:<6} | {status}")
            self.app.push_screen(InfoViewerScreen(_("Ports Status"), "\n".join(text_lines)))

        elif event.button.id == "btn_config":
            self.app.push_screen(ConfigEditorScreen(self.instance))

        elif event.button.id == "btn_config_raw":
            self.app.push_screen(RawConfigScreen(self.instance))

        elif event.button.id == "btn_schedule":
            self.app.push_screen(ScheduleScreen(self.instance))

        elif event.button.id == "btn_mods":
            self.app.push_screen(ModManagerScreen(self.instance))

        elif event.button.id == "btn_cleanup":
            self.app.push_screen(CleanupScreen(self.instance))


class ScheduleScreen(Screen):
    """Screen for managing the systemd restart timer."""

    BINDINGS = [
        ("b", "pop_screen", _("Back")),
        ("r", "refresh_schedule", _("Refresh Status")),
        ("ctrl+s", "apply_schedule", _("Apply Schedule")),
    ]

    def __init__(self, instance: str, **kwargs):
        super().__init__(**kwargs)
        self.instance = instance
        self._loaded_schedule = ""

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalGroup(id="info-container"):
            yield Label(
                tr("Restart Schedule: {instance}", instance=self.instance),
                id="screen-title",
            )
            yield Label(
                _("Manage the systemd timer used for scheduled server restarts."),
                id="schedule-help",
            )
            yield Label(_("Restart Schedule (OnCalendar or HH:MM[:SS]):"))
            yield Input(
                id="inp_restart_schedule",
                placeholder=_("*-*-* 06:00:00 or 05:30"),
            )
            yield RichLog(id="timer-status-log", markup=True)
            with HorizontalGroup(id="schedule-buttons-primary"):
                yield Button(_("Apply Schedule"), id="btn_apply_schedule", variant="success")
                yield Button(_("Enable Timer"), id="btn_enable_timer", variant="primary")
                yield Button(_("Disable Timer"), id="btn_disable_timer", variant="warning")
            with HorizontalGroup(id="schedule-buttons-secondary"):
                yield Button(_("Restart Now"), id="btn_restart_now", variant="error")
                yield Button(_("Refresh Status"), id="btn_refresh_schedule", variant="default")
                yield Button(_("Back"), id="btn_back", variant="default")
        yield Footer()

    def on_mount(self) -> None:
        self.action_refresh_schedule()
        self.query_one("#inp_restart_schedule", Input).focus()

    def _server_service_name(self) -> str:
        return service_unit_name(self.instance)

    def _timer_name(self) -> str:
        return timer_unit_name(self.instance)

    def _display_timer_value(self, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or cleaned.lower() == "n/a":
            return _("Unknown")
        return cleaned

    def _update_schedule_input(self, schedule: str) -> None:
        if not schedule:
            return

        input_widget = self.query_one("#inp_restart_schedule", Input)
        current_value = input_widget.value.strip()
        if not current_value or current_value == self._loaded_schedule:
            input_widget.value = schedule
        self._loaded_schedule = schedule

    def action_refresh_schedule(self) -> None:
        state = discover(self.instance, save=False)
        timer_status = get_timer_status(self._timer_name())
        timer_log = self.query_one("#timer-status-log", RichLog)

        schedule_value = self._display_timer_value(timer_status.get("schedule", ""))
        next_run = self._display_timer_value(timer_status.get("next_run", ""))
        last_trigger = self._display_timer_value(timer_status.get("last_trigger", ""))
        active_state = self._display_timer_value(timer_status.get("active_state", ""))

        self._update_schedule_input(timer_status.get("schedule", ""))

        installed_value = _("Yes") if state.timer_exists else _("No")
        enabled_value = _("Yes") if timer_status.get("enabled") else _("No")

        lines = [
            _("[bold cyan]Timer Status[/bold cyan]"),
            tr("Timer unit: {timer_name}", timer_name=self._timer_name()),
            tr("Installed: {value}", value=installed_value),
            tr("Enabled: {value}", value=enabled_value),
            tr("Active state: {value}", value=active_state),
            tr("Current schedule: {value}", value=schedule_value),
            tr("Next run: {value}", value=next_run),
            tr("Last trigger: {value}", value=last_trigger),
        ]

        description = timer_status.get("description", "").strip()
        if description:
            lines.append(tr("Description: {value}", value=description))

        timer_log.clear()
        timer_log.write("\n".join(lines))

    def action_apply_schedule(self) -> None:
        schedule_value = self.query_one("#inp_restart_schedule", Input).value.strip()
        if not schedule_value:
            self.app.notify(
                _("Restart schedule is required."),
                title=_("Restart Schedule"),
                severity="error",
            )
            return

        normalized_schedule = normalize_on_calendar(schedule_value)
        results = generate_services(self.instance, on_calendar=normalized_schedule)
        failures = [result.message for result in results if not result.success]
        if failures:
            self.app.notify(
                failures[0],
                title=_("Restart Schedule"),
                severity="error",
            )
            return

        self.query_one("#inp_restart_schedule", Input).value = normalized_schedule
        self._loaded_schedule = normalized_schedule
        self.app.notify(
            tr("Restart schedule updated to {schedule}.", schedule=normalized_schedule),
            title=_("Restart Schedule"),
        )
        self.action_refresh_schedule()

    def _toggle_timer(self, enable: bool) -> None:
        action = enable_service if enable else disable_service
        title = _("Enable Timer") if enable else _("Disable Timer")
        result = action(self._timer_name())
        self.app.notify(
            result.message,
            title=title,
            severity="error" if not result.success else "information",
        )
        self.action_refresh_schedule()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_back":
            self.app.pop_screen()
        elif event.button.id == "btn_refresh_schedule":
            self.action_refresh_schedule()
        elif event.button.id == "btn_apply_schedule":
            self.action_apply_schedule()
        elif event.button.id == "btn_enable_timer":
            self._toggle_timer(enable=True)
        elif event.button.id == "btn_disable_timer":
            self._toggle_timer(enable=False)
        elif event.button.id == "btn_restart_now":

            def check_restart(confirm: bool) -> None:
                if confirm:
                    result = restart_service(self._server_service_name())
                    self.app.notify(result.message, title=_("Restart Now"))

            self.app.push_screen(
                ConfirmScreen(_("Are you sure you want to RESTART the server now?")),
                check_restart,
            )


class CleanupScreen(Screen):
    """Screen for analyzing and cleaning up server logs and stale files."""

    BINDINGS = [
        ("b", "pop_screen", _("Back")),
        ("c", "clean_junk", _("Clean Now")),
    ]

    def __init__(self, instance: str, **kwargs):
        super().__init__(**kwargs)
        self.instance = instance

    def compose(self) -> ComposeResult:
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
        stats = get_junk_stats(self.instance)
        log = self.query_one("#info-log", RichLog)
        btn = self.query_one("#btn_clean_now", Button)

        log.clear()
        lines = []
        lines.append(_("[bold cyan]Server Junk Analysis[/bold cyan]"))
        lines.append("-------------------------")

        sz_logs = format_size(stats['logs']['size'])
        lines.append(
            tr("- Old Logs: {count} files ({size})", count=stats["logs"]["count"], size=sz_logs)
        )

        sz_dumps = format_size(stats['dumps']['size'])
        lines.append(
            tr(
                "- Crash Dumps: {count} files ({size})",
                count=stats["dumps"]["count"],
                size=sz_dumps,
            )
        )

        sz_backups = format_size(stats['backups']['size'])
        lines.append(
            tr(
                "- Stale Backups: {count} files ({size})",
                count=stats["backups"]["count"],
                size=sz_backups,
            )
        )

        lines.append("-------------------------")
        tot = format_size(stats['total_size'])

        if stats['total_size'] > 0:
            lines.append(
                tr("[bold red]Total Recoverable Space: {size}[/bold red]", size=tot)
            )
            btn.disabled = False
        else:
            lines.append(_("[bold green]System is clean! Nothing to remove.[/bold green]"))
            btn.disabled = True

        log.write("\n".join(lines))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_back":
            self.app.pop_screen()
        elif event.button.id == "btn_clean_now":

            def confirm_cleanup(confirm: bool):
                if confirm:
                    res = clean_junk(self.instance)
                    freed = format_size(res["freed_bytes"])
                    count = res["files_deleted"]
                    self.app.notify(
                        tr("Cleaned {count} files, freed {freed}!", count=count, freed=freed),
                        title=_("Cleanup Success"),
                    )
                    self.refresh_stats()

            self.app.push_screen(
                ConfirmScreen(
                    _("Are you sure you want to permanently delete these files?")
                ),
                confirm_cleanup,
            )


class ConfigEditorScreen(Screen):
    """Screen for editing the config.json file directly from TUI."""

    BINDINGS = [
        ("b", "pop_screen", _("Back without saving")),
    ]

    def __init__(self, instance: str, **kwargs):
        super().__init__(**kwargs)
        self.instance = instance
        self.config_path = ""
        self.config_data = {}

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label(tr("Edit Config: {instance}", instance=self.instance), id="screen-title")

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
            self.app.notify(_("Config file missing!"), severity="error")
            self.app.pop_screen()
            return

        try:
            self.config_data = load_config(self.config_path)
        except Exception as e:
            self.app.notify(tr("Cannot parse config: {error}", error=e), severity="error")
            self.app.pop_screen()
            return

        game = self.config_data.get("game", {})
        rcon = self.config_data.get("rcon", {})
        a2s = self.config_data.get("a2s", {})

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

        try:
            save_config(self.config_path, self.config_data, backup=True)
            self.app.notify(
                _("Config saved successfully (backup created automatically)"),
                title=_("Success"),
            )

            if restart:
                service_name = (
                    f"armareforger@{self.instance}.service"
                    if self.instance != "default"
                    else paths.SERVICE_NAME
                )
                res = restart_service(service_name)
                self.app.notify(_(res.message), title=_("Restart"))

            self.app.pop_screen()
        except Exception as e:
            self.app.notify(tr("Error saving config: {error}", error=e), severity="error")


class RawConfigScreen(Screen):
    """Screen for viewing and editing the raw config.json text."""

    BINDINGS = [
        ("b", "pop_screen", _("Back")),
        ("ctrl+s", "save_raw_config", _("Save JSON")),
        ("ctrl+r", "reload_raw_config", _("Reload")),
        ("c", "copy_raw_config", _("Copy JSON")),
    ]

    def __init__(self, instance: str, **kwargs):
        super().__init__(**kwargs)
        self.instance = instance
        self.config_path = ""

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalGroup(id="raw-config-container"):
            yield Label(f"{_('Raw Config JSON')}: {self.instance}", id="screen-title")
            yield Label(
                _(
                    "Manual JSON editor. Save keeps an automatic backup before writing."
                ),
                id="raw-config-help",
            )
            yield TextArea("", id="raw-config-editor")
            with HorizontalGroup(id="control-buttons"):
                yield Button(_("Save Config"), id="btn_raw_save", variant="success")
                yield Button(
                    _("Save & Restart"),
                    id="btn_raw_save_restart",
                    variant="warning",
                )
                yield Button(_("Reload From Disk"), id="btn_raw_reload", variant="default")
                yield Button(_("Copy JSON"), id="btn_raw_copy", variant="primary")
                yield Button(_("Back"), id="btn_raw_back", variant="error")
        yield Footer()

    def on_mount(self) -> None:
        state = discover(self.instance, save=False)
        self.config_path = state.config_path
        if not state.config_exists:
            self.app.notify(_("Config file missing!"), severity="error")
            self.app.pop_screen()
            return

        try:
            self.reload_from_disk()
            self.query_one("#raw-config-editor", TextArea).focus()
        except Exception as e:
            self.app.notify(tr("Cannot load config: {error}", error=e), severity="error")
            self.app.pop_screen()

    def _get_editor(self) -> TextArea:
        return self.query_one("#raw-config-editor", TextArea)

    def _set_editor_text(self, text: str) -> None:
        editor = self._get_editor()
        if hasattr(editor, "load_text"):
            editor.load_text(text)
        else:
            editor.text = text

    def _get_editor_text(self) -> str:
        return self._get_editor().text

    def reload_from_disk(self) -> None:
        config_text = Path(self.config_path).read_text(encoding="utf-8")
        self._set_editor_text(config_text)

    def action_reload_raw_config(self) -> None:
        try:
            self.reload_from_disk()
            self.app.notify(_("Reloaded config.json from disk."), title=_("Config Reload"))
        except Exception as e:
            self.app.notify(tr("Failed to reload config: {error}", error=e), severity="error")

    def action_copy_raw_config(self) -> None:
        text = self._get_editor_text().strip()
        if not text:
            self.app.notify(_("There is no config text to copy."), severity="warning")
            return

        self.app.copy_to_clipboard(text)
        self.app.notify(_("Copied config.json to clipboard."), title=_("Clipboard"))

    def action_save_raw_config(self) -> None:
        self.save_raw_config(restart=False)

    def save_raw_config(self, restart: bool) -> None:
        raw_text = self._get_editor_text()

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as e:
            self.app.notify(tr("Invalid JSON: {error}", error=e), severity="error")
            return

        errors = validate_config(data=data)
        if errors:
            self.app.notify(_(errors[0]), title=_("Config Validation"), severity="error")
            return

        try:
            save_config(self.config_path, data, backup=True)
            pretty_text = json.dumps(data, indent=4)
            self._set_editor_text(pretty_text)
            self.app.notify(
                _("Raw config saved successfully (backup created automatically)"),
                title=_("Success"),
            )
        except Exception as e:
            self.app.notify(tr("Error saving config: {error}", error=e), severity="error")
            return

        if restart:
            service_name = (
                f"armareforger@{self.instance}.service"
                if self.instance != "default"
                else paths.SERVICE_NAME
            )
            res = restart_service(service_name)
            self.app.notify(_(res.message), title=_("Restart"))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_raw_back":
            self.app.pop_screen()
        elif event.button.id == "btn_raw_reload":
            self.action_reload_raw_config()
        elif event.button.id == "btn_raw_copy":
            self.action_copy_raw_config()
        elif event.button.id == "btn_raw_save":
            self.save_raw_config(restart=False)
        elif event.button.id == "btn_raw_save_restart":
            self.save_raw_config(restart=True)


class ModPackFileScreen(Screen):
    """Prompt screen for mod pack import/export file paths."""

    BINDINGS = [
        ("b", "cancel", _("Back")),
        ("escape", "cancel", _("Cancel")),
    ]

    def __init__(
        self,
        title: str,
        mode: str,
        default_path: str = "",
        suggested_files: list[tuple[str, str]] | None = None,
        directory_note: str = "",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._title = title
        self._mode = mode
        self._default_path = default_path
        self._suggested_files = suggested_files or []
        self._directory_note = directory_note

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalGroup(id="modpack-dialog"):
            yield Label(self._title, id="screen-title")

            if self._mode == "import":
                help_text = _(
                    "Import accepts either an exported mod pack JSON "
                    "or a full config.json with game.mods."
                )
                placeholder = _("Path to mod pack JSON or config.json")
            else:
                help_text = _("Export writes the current mod list as a standalone JSON mod pack.")
                placeholder = _("Path to save mod pack JSON")

            yield Label(help_text, id="modpack-help")
            if self._mode == "import" and self._suggested_files:
                yield Label(
                    _(
                        "Suggested files below come from templates and this "
                        "instance's modpacks folder. Selecting one fills the "
                        "path field."
                    ),
                    id="modpack-source-note",
                )
                with VerticalScroll(id="modpack-suggestions"):
                    for index, (label, path_value) in enumerate(self._suggested_files):
                        yield Button(
                            label,
                            id=f"btn_modpack_suggestion_{index}",
                            variant="default",
                        )
            elif self._directory_note:
                yield Label(self._directory_note, id="modpack-source-note")

            yield Input(value=self._default_path, placeholder=placeholder, id="inp_modpack_path")

            with HorizontalGroup():
                if self._mode == "import":
                    yield Button(_("Import (Append)"), id="btn_import_append", variant="success")
                    yield Button(_("Import (Replace)"), id="btn_import_replace", variant="warning")
                else:
                    yield Button(_("Export"), id="btn_export_modpack", variant="success")
                yield Button(_("Cancel"), id="btn_cancel_modpack", variant="error")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#inp_modpack_path", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_cancel_modpack":
            self.dismiss(None)
            return

        if event.button.id.startswith("btn_modpack_suggestion_"):
            index = int(event.button.id.rsplit("_", maxsplit=1)[1])
            selected_path = self._suggested_files[index][1]
            input_widget = self.query_one("#inp_modpack_path", Input)
            input_widget.value = selected_path
            input_widget.focus()
            return

        file_path = self.query_one("#inp_modpack_path", Input).value.strip()
        if not file_path:
            self.app.notify(_("File path is required."), severity="error")
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
        ("b", "pop_screen", _("Back to Menu")),
        ("ctrl+r", "action_refresh_mods", _("Refresh List")),
    ]

    def __init__(self, instance: str, **kwargs):
        super().__init__(**kwargs)
        self.instance = instance

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalGroup(id="info-container"):
            yield Label(_("Mods Manager: ") + f"{self.instance}", id="screen-title")

            yield Input(id="inp_mod_id", placeholder=_("Paste Mod ID or Workshop String here..."))
            yield Input(id="inp_mod_name", placeholder=_("Name (Optional)"))
            yield Button(_("Add/Update Mod"), id="btn_add_mod", variant="success")

            yield Label(_("Installed Mods:"), id="mods-list-title")
            yield Label(_("Installed Mods: 0"), id="mods-summary")
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

    @staticmethod
    def _repo_root() -> Path:
        return Path(__file__).resolve().parents[3]

    def _import_directory_note(self) -> str:
        modpacks_dir = paths.modpacks_dir(self.instance)
        templates_dir = self._repo_root() / "templates"
        return tr(
            "armactl checks {modpacks_dir} for saved mod packs and "
            "{templates_dir} for example JSON configs.",
            modpacks_dir=modpacks_dir,
            templates_dir=templates_dir,
        )

    def _format_import_suggestion(self, path: Path) -> tuple[str, str]:
        modpacks_dir = paths.modpacks_dir(self.instance)
        templates_dir = self._repo_root() / "templates"
        legacy_export = paths.instance_root(self.instance) / "mods-export.json"

        if path.parent == modpacks_dir:
            source = _("Saved mod pack")
        elif path.parent == templates_dir:
            source = _("Template example")
        elif path == legacy_export:
            source = _("Legacy export")
        else:
            source = _("JSON file")

        try:
            count_text = tr(" ({count} mods)", count=preview_import_mods(path))
        except Exception:
            count_text = ""

        return f"{source}: {path.name}{count_text}", str(path)

    def _import_pack_suggestions(self) -> list[tuple[str, str]]:
        suggestions: list[tuple[str, str]] = []
        seen: set[str] = set()
        modpacks_dir = paths.modpacks_dir(self.instance)
        templates_dir = self._repo_root() / "templates"
        legacy_export = paths.instance_root(self.instance) / "mods-export.json"

        def add_candidate(path: Path) -> None:
            expanded = path.expanduser()
            if not expanded.exists():
                return

            resolved = str(expanded.resolve())
            if resolved in seen:
                return

            seen.add(resolved)
            suggestions.append(self._format_import_suggestion(expanded))

        if modpacks_dir.exists():
            saved_files = sorted(
                modpacks_dir.glob("*.json"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
            for candidate in saved_files[:8]:
                add_candidate(candidate)

        add_candidate(legacy_export)

        if templates_dir.exists():
            for candidate in sorted(templates_dir.glob("*.json")):
                add_candidate(candidate)

        return suggestions

    def _default_export_path(self) -> str:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"mods-export-{timestamp}.json"
        return str(paths.modpacks_dir(self.instance) / filename)

    def _handle_mod_pack_result(self, result: tuple[str, str] | None) -> None:
        if result is None:
            return

        action, raw_path = result
        cfg = paths.config_file(self.instance)
        file_path = Path(raw_path).expanduser()

        try:
            if action == "export":
                count = export_mods(cfg, file_path)
                self.app.notify(
                    tr("Exported {count} mods to {path}.", count=count, path=file_path),
                    title=_("Mod Pack Export"),
                )
                return

            added, skipped = import_mods(cfg, file_path, append=(action == "append"))
            mode_label = _("appended") if action == "append" else _("replaced")
            self.app.notify(
                tr(
                    "Mod pack {mode}: added {added}, skipped {skipped} duplicate(s).",
                    mode=mode_label,
                    added=added,
                    skipped=skipped,
                ),
                title=_("Mod Pack Import"),
            )
            self.action_refresh_mods()
        except Exception as e:
            self.app.notify(tr("Mod pack operation failed: {error}", error=e), severity="error")

    def action_refresh_mods(self) -> None:
        cfg = paths.config_file(self.instance)
        try:
            mods = get_mods(cfg)
        except Exception as e:
            self.app.notify(tr("Error loading mods: {error}", error=e), severity="error")
            return

        self.query_one("#mods-summary", Label).update(
            tr("Installed Mods: {count}", count=len(mods))
        )

        list_view = self.query_one("#mods-list", ListView)
        list_view.clear()

        if not mods:
            list_view.append(ListItem(Label(_("No mods installed."))))
            return

        for idx, mod in enumerate(mods, 1):
            mod_id = mod.get("modId", _("Unknown"))
            name = mod.get("name", "")
            display = f"[{idx}] {mod_id}"
            if name:
                display += f" - {name}"
            item = ListItem(Label(display), id=f"mod_item_{mod_id}")
            item.mod_id = mod_id  # type: ignore[attr-defined]
            list_view.append(item)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        cfg = paths.config_file(self.instance)

        if event.button.id == "btn_back":
            self.app.pop_screen()

        elif event.button.id == "btn_import_pack":
            suggestions = self._import_pack_suggestions()
            default_path = suggestions[0][1] if suggestions else ""
            self.app.push_screen(
                ModPackFileScreen(
                    _("Import Mod Pack"),
                    "import",
                    default_path=default_path,
                    suggested_files=suggestions,
                    directory_note=self._import_directory_note(),
                ),
                self._handle_mod_pack_result,
            )

        elif event.button.id == "btn_export_pack":
            self.app.push_screen(
                ModPackFileScreen(
                    _("Export Mod Pack"),
                    "export",
                    default_path=self._default_export_path(),
                    directory_note=(
                        tr(
                            "By default exports are saved in {path}.",
                            path=paths.modpacks_dir(self.instance),
                        )
                    ),
                ),
                self._handle_mod_pack_result,
            )

        elif event.button.id == "btn_add_mod":
            inp_id = self.query_one("#inp_mod_id", Input)
            inp_name = self.query_one("#inp_mod_name", Input)
            raw_id = inp_id.value.strip()
            name = inp_name.value.strip()

            if not raw_id:
                self.app.notify(_("Mod string is required!"), severity="error")
                return

            match = re.search(r"([0-9A-Fa-f]{10,24})", raw_id)
            if not match:
                self.app.notify(_("Could not find a valid Mod ID in the input!"), severity="error")
                return

            mod_id = match.group(1).upper()

            is_new = add_mod(cfg, mod_id, name)
            if is_new:
                self.app.notify(tr("Mod {mod_id} added successfully.", mod_id=mod_id))
            else:
                self.app.notify(tr("Mod {mod_id} updated successfully.", mod_id=mod_id))

            inp_id.value = ""
            inp_name.value = ""
            self.action_refresh_mods()

        elif event.button.id == "btn_remove_mod":
            list_view = self.query_one("#mods-list", ListView)
            if list_view.highlighted_child is None:
                self.app.notify(_("Select a mod to remove first."), severity="warning")
                return

            mod_id = getattr(list_view.highlighted_child, "mod_id", None)
            if mod_id:

                def confirm_remove(confirm: bool):
                    if confirm:
                        success = remove_mod(cfg, mod_id)
                        if success:
                            self.app.notify(tr("Removed mod {mod_id}.", mod_id=mod_id))
                            self.action_refresh_mods()
                        else:
                            self.app.notify(
                                tr("Mod {mod_id} not found.", mod_id=mod_id),
                                severity="error",
                            )

                self.app.push_screen(
                    ConfirmScreen(
                        tr("Are you sure you want to remove Mod '{mod_id}'?", mod_id=mod_id)
                    ),
                    confirm_remove,
                )

        elif event.button.id == "btn_dedupe_mods":
            count = dedupe_mods(cfg)
            self.app.notify(
                tr("Deduped mods. Reclaimed {count} duplicates.", count=count)
            )
            if count > 0:
                self.action_refresh_mods()


