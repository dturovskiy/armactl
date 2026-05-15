"""Screens for the Textual TUI."""

from __future__ import annotations

import json
import re
import subprocess
from asyncio import Lock
from datetime import datetime
from pathlib import Path

from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.containers import HorizontalGroup, HorizontalScroll, VerticalGroup, VerticalScroll
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
from armactl.a2s import query_player_status
from armactl.addon_cleanup import cleanup_unconfigured_addons
from armactl.bot_config import (
    BotConfig,
    BotConfigError,
    ensure_bot_config,
    load_bot_config,
    parse_admin_chat_ids,
    save_bot_config,
    validate_bot_config,
)
from armactl.bot_manager import (
    get_bot_service_status,
    install_bot_service,
    restart_bot_service,
    start_bot_service,
    stop_bot_service,
)
from armactl.cleaner import clean_junk, format_size, get_junk_stats
from armactl.config_manager import load_config, save_config, validate_config
from armactl.discovery import discover
from armactl.i18n import _, tr
from armactl.installer import run_install
from armactl.logs import get_logs_text
from armactl.metrics import (
    HostMetrics,
    format_bytes,
    format_cpu_percent,
    format_duration,
    format_load_average,
    query_host_metrics,
    query_service_runtime_metrics,
)
from armactl.mods import add_mod, dedupe_mods, remove_mod_detailed
from armactl.mods_manager import (
    export_mods,
    get_mods,
    import_mods_detailed,
    preview_import_mods,
)
from armactl.rcon import query_player_roster
from armactl.redaction import redact_sensitive_text
from armactl.repair import run_repair
from armactl.service_manager import (
    disable_service,
    enable_service,
    format_schedule_for_input,
    get_service_status,
    get_timer_status,
    normalize_on_calendar_entries,
    restart_service,
    service_unit_name,
    start_service,
    stop_service,
    timer_unit_name,
    update_restart_timer_schedule,
)
from armactl.status_summary import ConfigSummary, ModsSummary, load_status_summaries
from armactl.tui.dashboard import format_player_count, format_usage_bar
from armactl.tui.display import get_instance_display_label, get_instance_server_name


def _build_mod_list_item(index: int, mod: dict[str, object]) -> ListItem:
    """Build a mods list row without a stable DOM id."""
    raw_mod_id = str(mod.get("modId") or "").strip()
    mod_id = raw_mod_id.upper() if raw_mod_id else _("Unknown")
    name = str(mod.get("name") or "")

    display = f"[{index}] {mod_id}"
    if name:
        display += f" - {name}"

    item = ListItem(Label(display))
    item.mod_id = mod_id  # type: ignore[attr-defined]
    return item


class LogWorkerScreen(Screen):
    """A generic screen that runs a background task and displays logs."""

    BINDINGS = [
        ("b", "go_back", _("Back to Menu")),
        ("c", "copy_output", _("Copy Output")),
    ]
    refresh_main_menu_on_return = False

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
            self.run_worker(self._return_to_menu(), group="navigation", exclusive=True)

    def action_go_back(self) -> None:
        # Prevent going back if task is not finished
        btn = self.query_one("#btn_close", Button)
        if not btn.disabled:
            self.run_worker(self._return_to_menu(), group="navigation", exclusive=True)

    async def _return_to_menu(self) -> None:
        if self.refresh_main_menu_on_return:
            refresh = getattr(self.app, "refresh_main_menu", None)
            if refresh is not None:
                await refresh()
        self.app.pop_screen()

    def request_main_menu_refresh(self) -> None:
        refresh = getattr(self.app, "request_main_menu_refresh", None)
        if refresh is not None:
            refresh()

    def action_copy_output(self) -> None:
        text = "\n".join(line for line in self._output_lines if line)
        if not text:
            self.app.notify(_("There is no output to copy yet."), severity="warning")
            return

        self.app.copy_to_clipboard(text)
        self.app.notify(_("Copied full output to clipboard."), title=_("Clipboard"))

    def append_output(self, rendered: str, plain: str | None = None) -> None:
        """Append a line to the visible log and the copy buffer."""
        safe_rendered = redact_sensitive_text(rendered)
        safe_plain = redact_sensitive_text(plain if plain is not None else rendered).rstrip()
        self._output_lines.append(safe_plain)
        self.query_one("#task-log", RichLog).write(safe_rendered)

    def save_output_to_file(self, output_path: Path, lines: list[str] | None = None) -> None:
        """Persist the current buffered output to a text file."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        source_lines = self._output_lines if lines is None else lines
        text = "\n".join(redact_sensitive_text(line) for line in source_lines).rstrip()
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

    refresh_main_menu_on_return = True

    def on_mount(self) -> None:
        self.run_installation_task()

    @work(exclusive=True, thread=True)
    def run_installation_task(self) -> None:
        install_completed = False
        try:
            for message in run_install(self.instance):
                self.app.call_from_thread(self.append_output, message)
            self.app.call_from_thread(
                self.append_output,
                _("[green]Installation completely finished![/green]"),
                _("Installation completely finished!"),
            )
            install_completed = True
        except Exception as e:
            self.app.call_from_thread(
                self.append_output,
                tr("[red]Installation failed: {error}[/red]", error=redact_sensitive_text(e)),
                tr("Installation failed: {error}", error=redact_sensitive_text(e)),
            )

        if install_completed:
            self.app.call_from_thread(self.request_main_menu_refresh)
        self.app.call_from_thread(self.complete_task)


class RepairScreen(LogWorkerScreen):
    """Screen for running the server repair task."""

    refresh_main_menu_on_return = True

    def on_mount(self) -> None:
        self.run_repair_task()

    @work(exclusive=True, thread=True)
    def run_repair_task(self) -> None:
        state = discover(self.instance, save=False)
        repair_completed = False
        try:
            # We call run_repair from backend
            for message in run_repair(self.instance, state.install_dir, state.config_path):
                self.app.call_from_thread(self.append_output, message)
            self.app.call_from_thread(
                self.append_output,
                _("[green]Repair completed successfully![/green]"),
                _("Repair completed successfully!"),
            )
            repair_completed = True
        except Exception as e:
            self.app.call_from_thread(
                self.append_output,
                tr("[red]Repair failed: {error}[/red]", error=redact_sensitive_text(e)),
                tr("Repair failed: {error}", error=redact_sensitive_text(e)),
            )

        if repair_completed:
            self.app.call_from_thread(self.request_main_menu_refresh)
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
                tr(
                    "[red]Failed to start host tests: {error}[/red]",
                    error=redact_sensitive_text(e),
                ),
                tr("Failed to start host tests: {error}", error=redact_sensitive_text(e)),
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
            tr(
                "Live Logs: {instance} (Press Q to exit)",
                instance=get_instance_display_label(self.instance),
            ),
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
        ("q", "quit", _("Quit")),
        ("r", "refresh_state", _("Refresh Status")),
    ]

    def __init__(self, instance: str, **kwargs):
        super().__init__(**kwargs)
        self.instance = instance
        self._active_panel = "overview"
        self._context_action_keys: dict[str, str] = {}

    @staticmethod
    def _yes_no(value: object) -> str:
        if value is True:
            return _("Yes")
        if value is False:
            return _("No")
        return _("Unknown")

    def _service_name(self) -> str:
        return service_unit_name(self.instance)

    def _title_text(self) -> str:
        return tr(
            "Manage Server: {instance}",
            instance=get_instance_display_label(self.instance),
        )

    def _server_display_name(self) -> str:
        return get_instance_server_name(self.instance) or _("Server name not configured")

    def action_quit(self) -> None:
        self.app.exit(0)

    def _nav_items(self) -> list[tuple[str, str, str]]:
        return [
            ("overview", "nav_overview", _("Overview")),
            ("config", "nav_config", _("Config")),
            ("mods", "nav_mods", _("Mods")),
            ("schedule", "nav_schedule", _("Schedule")),
            ("bot", "nav_bot", _("Bot")),
            ("cleanup", "nav_cleanup", _("Cleanup")),
            ("logs", "nav_logs", _("Logs")),
            ("status", "nav_status", _("Status")),
            ("ports", "nav_ports", _("Ports")),
        ]

    def _panel_title(self) -> str:
        titles = {
            "overview": _("Overview"),
            "config": _("Configuration"),
            "mods": _("Mods Manager"),
            "schedule": _("Restart Schedule"),
            "bot": _("Telegram Bot"),
            "cleanup": _("Maintenance / Cleanup"),
            "logs": _("Logs"),
            "status": _("Status Details"),
            "ports": _("Ports"),
        }
        return titles.get(self._active_panel, _("Overview"))

    def on_mount(self) -> None:
        self.action_refresh_state()

    def on_screen_resume(self) -> None:
        """Auto-refresh state when returning from a sub-screen like ConfigEditor."""
        self.action_refresh_state()

    def action_refresh_state(self) -> None:
        state = discover(self.instance, save=False)
        service_status = get_service_status(self._service_name())
        title = self.query_one("#manage-screen-title", Label)
        server_name = self.query_one("#manage-server-name", Label)
        instance_id = self.query_one("#manage-instance-id", Label)
        runtime_badge = self.query_one("#manage-runtime-badge", Label)
        status_label = self.query_one("#server-status", Label)
        btn_toggle = self.query_one("#btn_toggle", Button)

        title.update(self._title_text())
        server_name.update(self._server_display_name())
        instance_id.update(tr("Instance: {instance}", instance=self.instance))

        if state.server_running:
            runtime_badge.update(_("Running"))
            runtime_badge.styles.color = "green"
            status_text = _("[bold green]SERVER IS RUNNING[/bold green]")
            btn_toggle.label = _("Stop")
            btn_toggle.variant = "error"
        else:
            runtime_badge.update(_("Stopped"))
            runtime_badge.styles.color = "red"
            status_text = _("[bold red]SERVER IS STOPPED[/bold red]")
            btn_toggle.label = _("Start")
            btn_toggle.variant = "success"

        btn_toggle.refresh(layout=True)
        enabled_text = self._yes_no(service_status.get("enabled"))
        status_label.update(
            f"{status_text}  |  {tr('Service enabled: {value}', value=enabled_text)}"
        )
        self._render_active_panel()

    def _format_players(self, current: int | None, maximum: int | None) -> str:
        player_text = format_player_count(current, maximum)
        if player_text == "Unknown":
            return _("Unknown")
        return player_text

    def _format_bytes_pair(
        self,
        used: int | None,
        total: int | None,
        *,
        width: int = 12,
    ) -> str:
        if used is None or total is None:
            return _("Unknown")
        usage_bar = format_usage_bar(used, total, width=width)
        return f"{format_bytes(used)} / {format_bytes(total)} {usage_bar}"

    def _query_host_metrics(self) -> HostMetrics:
        try:
            return query_host_metrics()
        except Exception as error:
            return HostMetrics(False, error=str(error))

    def _build_overview_text(self) -> str:
        state = discover(self.instance, save=False)
        service_status = get_service_status(self._service_name())
        player_status = query_player_status(self.instance, state=state)
        metrics = query_service_runtime_metrics(service_status)
        host_metrics = self._query_host_metrics()

        if state.config_exists and state.config_path:
            config_summary, mods_summary = load_status_summaries(state.config_path)
        else:
            config_summary, mods_summary = ConfigSummary(False), ModsSummary(False)

        unknown_text = _("Unknown")
        lines = [
            _("[bold cyan]Service Status[/bold cyan]"),
            tr(
                "Service active state: {value}",
                value=_("Running") if state.server_running else _("Stopped"),
            ),
            tr(
                "Service enabled: {value}",
                value=self._yes_no(service_status.get("enabled")),
            ),
            "",
            _("[bold cyan]Players[/bold cyan]"),
            tr(
                "Players: {value}",
                value=self._format_players(
                    player_status.player_count,
                    player_status.max_players,
                ),
            ),
            "",
            _("[bold cyan]Runtime Metrics[/bold cyan]"),
            tr(
                "Server CPU: {value}",
                value=(
                    format_cpu_percent(metrics.cpu_percent)
                    if metrics.cpu_percent is not None
                    else unknown_text
                ),
            ),
            tr(
                "Server RAM: {value}",
                value=(
                    format_bytes(metrics.memory_rss_bytes)
                    if metrics.memory_rss_bytes is not None
                    else unknown_text
                ),
            ),
            "",
            _("[bold cyan]Host / VM Metrics[/bold cyan]"),
            tr(
                "Host CPU: {value}",
                value=(
                    format_cpu_percent(host_metrics.cpu_percent)
                    if host_metrics.cpu_percent is not None
                    else unknown_text
                ),
            ),
            tr(
                "Host RAM: {value}",
                value=self._format_bytes_pair(
                    host_metrics.memory_used_bytes,
                    host_metrics.memory_total_bytes,
                ),
            ),
            tr(
                "Host Disk: {value}",
                value=self._format_bytes_pair(
                    host_metrics.disk_used_bytes,
                    host_metrics.disk_total_bytes,
                ),
            ),
            "",
            _("[bold cyan]Config Summary[/bold cyan]"),
            tr(
                "Server name: {value}",
                value=config_summary.server_name or unknown_text,
            ),
            tr("Config path: {value}", value=state.config_path or unknown_text),
            tr("Server directory: {value}", value=state.install_dir or unknown_text),
            "",
            _("[bold cyan]Mods Summary[/bold cyan]"),
            tr(
                "Installed mods: {count}",
                count=mods_summary.count if mods_summary.available else 0,
            ),
        ]

        warnings: list[str] = []
        if player_status.player_count is None:
            warnings.append(_("Players: unavailable"))
        if not metrics.available:
            warnings.append(
                tr(
                    "Server metrics unavailable: {value}",
                    value=metrics.error or unknown_text,
                )
            )
        if not host_metrics.available:
            warnings.append(
                tr(
                    "Host metrics unavailable: {value}",
                    value=host_metrics.error or unknown_text,
                )
            )
        if not config_summary.available:
            warnings.append(_("Config summary unavailable."))
        if not mods_summary.available:
            warnings.append(_("Mods summary unavailable."))

        if warnings:
            lines.extend(["", _("[bold yellow]Warnings[/bold yellow]")])
            lines.extend(f"- {warning}" for warning in warnings)

        return "\n".join(lines)

    def _build_status_details_text(self) -> str:
        state = discover(self.instance, save=False)
        service_name = self._service_name()
        timer_name = timer_unit_name(self.instance)
        service_status = get_service_status(service_name)
        timer_status = get_timer_status(timer_name)
        player_status = query_player_status(self.instance, state=state)
        metrics = query_service_runtime_metrics(service_status)
        host_metrics = self._query_host_metrics()
        main_pid = metrics.pid
        if state.config_exists and state.config_path:
            config_summary, mods_summary = load_status_summaries(state.config_path)
        else:
            config_summary, mods_summary = ConfigSummary(False), ModsSummary(False)
        unknown_text = _("Unknown")

        def bool_text(value: bool | None) -> str:
            if value is True:
                return _("Yes")
            if value is False:
                return _("No")
            return unknown_text

        lines = [
            _("[bold cyan]Service Status[/bold cyan]"),
            tr("Service: {value}", value=service_name),
            tr(
                "Service active state: {value}",
                value=service_status.get("active_state", unknown_text),
            ),
            tr(
                "Service enabled: {value}",
                value=_("Yes") if service_status.get("enabled") else _("No"),
            ),
            tr("Main PID: {value}", value=main_pid or unknown_text),
            "",
            _("[bold cyan]Timer Status[/bold cyan]"),
            tr("Timer: {value}", value=timer_name),
            tr(
                "Current schedule: {value}",
                value=timer_status.get("schedule", "").strip() or unknown_text,
            ),
            tr(
                "Next run: {value}",
                value=timer_status.get("next_run", "").strip() or unknown_text,
            ),
            "",
            _("[bold cyan]Runtime Metrics[/bold cyan]"),
            tr(
                "Server CPU: {value}",
                value=(
                    format_cpu_percent(metrics.cpu_percent)
                    if metrics.cpu_percent is not None
                    else unknown_text
                ),
            ),
            tr(
                "Server RAM: {value}",
                value=(
                    format_bytes(metrics.memory_rss_bytes)
                    if metrics.memory_rss_bytes is not None
                    else unknown_text
                ),
            ),
            "",
            _("[bold cyan]Host / VM Metrics[/bold cyan]"),
            tr(
                "Host CPU: {value}",
                value=(
                    format_cpu_percent(host_metrics.cpu_percent)
                    if host_metrics.cpu_percent is not None
                    else unknown_text
                ),
            ),
            tr(
                "Host RAM: {value}",
                value=(
                    f"{format_bytes(host_metrics.memory_used_bytes)} / "
                    f"{format_bytes(host_metrics.memory_total_bytes)}"
                    if (
                        host_metrics.memory_used_bytes is not None
                        and host_metrics.memory_total_bytes is not None
                    )
                    else unknown_text
                ),
            ),
            tr(
                "Host Disk: {value}",
                value=(
                    f"{format_bytes(host_metrics.disk_used_bytes)} / "
                    f"{format_bytes(host_metrics.disk_total_bytes)}"
                    if (
                        host_metrics.disk_used_bytes is not None
                        and host_metrics.disk_total_bytes is not None
                    )
                    else unknown_text
                ),
            ),
            tr(
                "Host Load Avg: {value}",
                value=format_load_average(
                    host_metrics.load_average_1m,
                    host_metrics.load_average_5m,
                    host_metrics.load_average_15m,
                ),
            ),
            tr(
                "Host uptime: {value}",
                value=format_duration(host_metrics.uptime_seconds),
            ),
            "",
            _("[bold cyan]Config Summary[/bold cyan]"),
        ]

        if config_summary.available:
            lines.extend(
                [
                    tr(
                        "Server name: {value}",
                        value=config_summary.server_name or unknown_text,
                    ),
                    tr(
                        "Scenario: {value}",
                        value=config_summary.scenario_id or unknown_text,
                    ),
                    tr(
                        "Max players: {value}",
                        value=(
                            config_summary.max_players
                            if config_summary.max_players is not None
                            else unknown_text
                        ),
                    ),
                    tr(
                        "Ports: game {game} / A2S {a2s} / RCON {rcon}",
                        game=(
                            config_summary.bind_port
                            if config_summary.bind_port is not None
                            else unknown_text
                        ),
                        a2s=(
                            config_summary.a2s_port
                            if config_summary.a2s_port is not None
                            else unknown_text
                        ),
                        rcon=(
                            config_summary.rcon_port
                            if config_summary.rcon_port is not None
                            else unknown_text
                        ),
                    ),
                    tr(
                        "Visible: {value}",
                        value=bool_text(config_summary.visible),
                    ),
                    tr(
                        "BattlEye: {value}",
                        value=bool_text(config_summary.battleye),
                    ),
                ]
            )
        else:
            lines.append(_("Config summary unavailable."))

        lines.extend(
            [
                "",
                _("[bold cyan]Mods Summary[/bold cyan]"),
            ]
        )

        if mods_summary.available:
            lines.append(
                tr(
                    "Installed mods: {count}",
                    count=mods_summary.count if mods_summary.count is not None else 0,
                )
            )
            if mods_summary.preview:
                lines.extend(f"- {entry.label}" for entry in mods_summary.preview)
            elif mods_summary.count == 0:
                lines.append(_("No mods configured."))
            if mods_summary.remaining_count > 0:
                lines.append(
                    tr("+ {count} more mod(s)", count=mods_summary.remaining_count)
                )
        else:
            lines.append(_("Mods summary unavailable."))

        lines.extend(
            [
                "",
            _("[bold cyan]Players[/bold cyan]"),
            ]
        )

        if player_status.player_count is None:
            lines.append(_("Players: unavailable"))
        elif player_status.max_players is None:
            lines.append(
                tr(
                    "Players: {current}",
                    current=player_status.player_count,
                )
            )
        else:
            lines.append(
                tr(
                    "Players: {current}/{max}",
                    current=player_status.player_count,
                    max=player_status.max_players,
                )
            )

        if player_status.player_count and player_status.player_count > 0:
            roster = query_player_roster(self.instance)
            if roster.available and roster.entries:
                for entry in roster.entries:
                    if entry.player_id:
                        lines.append(
                            tr(
                                "- {name} (#{player_id})",
                                name=entry.name,
                                player_id=entry.player_id,
                            )
                        )
                    else:
                        lines.append(tr("- {name}", name=entry.name))
            elif roster.configured:
                lines.append(
                    tr(
                        "Player roster unavailable: {value}",
                        value=roster.error or _("Unknown"),
                    )
                )
            else:
                lines.append(_("RCON player roster is not configured."))
        elif player_status.player_count == 0:
            lines.append(_("No players online."))

        return "\n".join(lines)

    def _build_ports_text(self) -> str:
        state = discover(self.instance, save=False)
        if not state.config_exists:
            return _("Config missing. Cannot read ports.")

        port_rows = ports.check_server_ports(
            state.ports.game,
            state.ports.a2s,
            state.ports.rcon,
        )
        lines = [_("Ports Status"), ""]
        for name, info in port_rows.items():
            status = (
                _("[green]OPEN listening[/green]")
                if info["listening"]
                else _("[red]CLOSED[/red]")
            )
            lines.append(f"{name:<10} | {info['port']:<6} | {status}")
        return "\n".join(lines)

    def _bool_text(self, value: bool | None) -> str:
        if value is True:
            return _("Yes")
        if value is False:
            return _("No")
        return _("Unknown")

    def _build_config_text(self) -> str:
        state = discover(self.instance, save=False)
        unknown_text = _("Unknown")
        lines = [_("[bold cyan]Configuration[/bold cyan]")]

        if not state.config_exists or not state.config_path:
            lines.extend(
                [
                    _("Config missing."),
                    tr("Expected config path: {value}", value=state.config_path or unknown_text),
                    tr("Server directory: {value}", value=state.install_dir or unknown_text),
                ]
            )
            return "\n".join(lines)

        config_summary, _mods_summary = load_status_summaries(state.config_path)
        lines.extend(
            [
                tr("Config path: {value}", value=state.config_path),
                tr("Server directory: {value}", value=state.install_dir or unknown_text),
                tr(
                    "Server name: {value}",
                    value=config_summary.server_name or unknown_text,
                ),
                tr("Scenario: {value}", value=config_summary.scenario_id or unknown_text),
                tr(
                    "Max players: {value}",
                    value=(
                        config_summary.max_players
                        if config_summary.max_players is not None
                        else unknown_text
                    ),
                ),
                tr(
                    "Ports: game {game} / A2S {a2s} / RCON {rcon}",
                    game=(
                        config_summary.bind_port
                        if config_summary.bind_port is not None
                        else unknown_text
                    ),
                    a2s=(
                        config_summary.a2s_port
                        if config_summary.a2s_port is not None
                        else unknown_text
                    ),
                    rcon=(
                        config_summary.rcon_port
                        if config_summary.rcon_port is not None
                        else unknown_text
                    ),
                ),
                tr("Visible: {value}", value=self._bool_text(config_summary.visible)),
                tr("BattlEye: {value}", value=self._bool_text(config_summary.battleye)),
            ]
        )

        if not config_summary.available:
            lines.extend(["", _("Config summary unavailable.")])

        return "\n".join(lines)

    def _build_mods_text(self) -> str:
        state = discover(self.instance, save=False)
        if not state.config_exists or not state.config_path:
            return "\n".join(
                [
                    _("[bold cyan]Mods Summary[/bold cyan]"),
                    _("Config missing. Cannot read mods."),
                ]
            )

        _config_summary, mods_summary = load_status_summaries(state.config_path)
        lines = [_("[bold cyan]Mods Summary[/bold cyan]")]
        if not mods_summary.available:
            lines.append(_("Mods summary unavailable."))
            return "\n".join(lines)

        lines.append(
            tr(
                "Installed mods: {count}",
                count=mods_summary.count if mods_summary.count is not None else 0,
            )
        )
        if mods_summary.preview:
            lines.extend(f"- {entry.label}" for entry in mods_summary.preview)
        elif mods_summary.count == 0:
            lines.append(_("No mods configured."))
        if mods_summary.remaining_count > 0:
            lines.append(tr("+ {count} more mod(s)", count=mods_summary.remaining_count))
        return "\n".join(lines)

    def _display_timer_value(self, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or cleaned.lower() == "n/a":
            return _("Unknown")
        return cleaned

    def _build_schedule_text(self) -> str:
        state = discover(self.instance, save=False)
        timer_name = timer_unit_name(self.instance)
        timer_status = get_timer_status(timer_name)

        installed_value = _("Yes") if state.timer_exists else _("No")
        enabled_value = _("Yes") if timer_status.get("enabled") else _("No")
        lines = [
            _("[bold cyan]Timer Status[/bold cyan]"),
            tr("Timer unit: {timer_name}", timer_name=timer_name),
            tr("Installed: {value}", value=installed_value),
            tr("Enabled: {value}", value=enabled_value),
            tr(
                "Active state: {value}",
                value=self._display_timer_value(timer_status.get("active_state", "")),
            ),
            tr(
                "Current schedule: {value}",
                value=self._display_timer_value(timer_status.get("schedule", "")),
            ),
            tr(
                "Next run: {value}",
                value=self._display_timer_value(timer_status.get("next_run", "")),
            ),
            tr(
                "Last trigger: {value}",
                value=self._display_timer_value(timer_status.get("last_trigger", "")),
            ),
        ]

        description = str(timer_status.get("description", "")).strip()
        if description:
            lines.append(tr("Description: {value}", value=description))
        return "\n".join(lines)

    def _build_bot_text(self) -> str:
        lines = [_("[bold cyan]Telegram Bot Status[/bold cyan]")]
        validation_errors: list[str] = []

        try:
            config = load_bot_config(self.instance)
            validation_errors = validate_bot_config(config)
            enabled_value = _("Yes") if config.enabled else _("No")
            admins_value = config.admin_chat_ids_text() or _("Missing")
            lines.extend(
                [
                    tr("Bot config file: {path}", path=config.env_path),
                    tr("Bot enabled: {value}", value=enabled_value),
                    tr("Bot token status: {value}", value=config.masked_token()),
                    tr("Admin Chat IDs: {value}", value=admins_value),
                    tr("Bot language: {value}", value=config.language),
                ]
            )
        except Exception as error:
            lines.extend(
                [
                    tr("Bot config file: {path}", path=paths.bot_env_file(self.instance)),
                    tr(
                        "Failed to load Telegram bot settings: {error}",
                        error=redact_sensitive_text(error),
                    ),
                ]
            )

        bot_service = get_bot_service_status()
        runtime_status = bot_service.get("runtime", {})
        lines.extend(
            [
                tr(
                    "Bot service unit: {value}",
                    value=bot_service.get("service_name", paths.BOT_SERVICE_NAME),
                ),
                tr(
                    "Bot service installed: {value}",
                    value=_("Yes") if bot_service.get("installed") else _("No"),
                ),
                tr(
                    "Bot service enabled on boot: {value}",
                    value=_("Yes") if bot_service.get("enabled") else _("No"),
                ),
                tr(
                    "Bot service active state: {value}",
                    value=bot_service.get("active_state", _("Unknown")),
                ),
                tr(
                    "Bot runtime ready: {value}",
                    value=_("Yes") if runtime_status.get("success") else _("No"),
                ),
            ]
        )

        if validation_errors:
            lines.append(_("[bold yellow]Validation warnings[/bold yellow]"))
            lines.extend(tr("- {error}", error=error) for error in validation_errors)
        return "\n".join(lines)

    def _build_cleanup_text(self) -> str:
        stats = get_junk_stats(self.instance)
        sz_logs = format_size(stats["logs"]["size"])
        sz_dumps = format_size(stats["dumps"]["size"])
        sz_backups = format_size(stats["backups"]["size"])
        total_size = format_size(stats["total_size"])

        lines = [
            _("[bold cyan]Server Junk Analysis[/bold cyan]"),
            tr("- Old Logs: {count} files ({size})", count=stats["logs"]["count"], size=sz_logs),
            tr(
                "- Crash Dumps: {count} files ({size})",
                count=stats["dumps"]["count"],
                size=sz_dumps,
            ),
            tr(
                "- Stale Backups: {count} files ({size})",
                count=stats["backups"]["count"],
                size=sz_backups,
            ),
        ]

        if stats["total_size"] > 0:
            lines.append(
                tr("[bold red]Total Recoverable Space: {size}[/bold red]", size=total_size)
            )
        else:
            lines.append(_("[bold green]System is clean! Nothing to remove.[/bold green]"))
        return "\n".join(lines)

    def _build_logs_text(self) -> str:
        text = get_logs_text(self._service_name(), lines=30).strip()
        lines = [_("[bold cyan]Recent Logs[/bold cyan]")]
        if not text:
            lines.append(_("No recent log output available."))
            return "\n".join(lines)

        lines.extend(escape(line) for line in text.splitlines()[-30:])
        return "\n".join(lines)

    def _context_actions(self) -> list[tuple[str, str, str]]:
        actions = {
            "overview": [
                ("open_live_logs", _("Live Logs"), "primary"),
                ("open_config", _("Config"), "default"),
            ],
            "config": [
                ("open_config", _("Edit Configuration"), "primary"),
                ("open_raw_config", _("Raw Config JSON"), "default"),
            ],
            "mods": [
                ("open_mods", _("Mods Manager"), "primary"),
            ],
            "schedule": [
                ("open_schedule", _("Restart Schedule"), "primary"),
            ],
            "bot": [
                ("open_bot", _("Telegram Bot"), "primary"),
            ],
            "cleanup": [
                ("open_cleanup", _("Maintenance / Cleanup"), "warning"),
            ],
            "logs": [
                ("open_live_logs", _("Live Logs"), "primary"),
            ],
            "status": [
                ("open_schedule", _("Restart Schedule"), "primary"),
            ],
            "ports": [
                ("open_config", _("Config"), "primary"),
            ],
        }
        return actions.get(self._active_panel, actions["overview"])

    def _update_context_actions(self) -> None:
        action_buttons = ("btn_context_primary", "btn_context_secondary")
        self._context_action_keys.clear()
        actions = self._context_actions()

        for index, button_id in enumerate(action_buttons):
            button = self.query_one(f"#{button_id}", Button)
            try:
                action_key, label, variant = actions[index]
            except IndexError:
                button.display = False
                button.label = ""
                button.variant = "default"
                button.disabled = True
                button.refresh(layout=True)
                continue

            self._context_action_keys[button_id] = action_key
            button.display = True
            button.label = label
            button.variant = variant
            button.disabled = False
            button.refresh(layout=True)

    def _select_panel(self, panel: str) -> None:
        self._active_panel = panel
        self.action_refresh_state()

    def _handle_context_action(self, action_key: str) -> None:
        if action_key == "open_config":
            self.app.push_screen(ConfigEditorScreen(self.instance))
        elif action_key == "open_raw_config":
            self.app.push_screen(RawConfigScreen(self.instance))
        elif action_key == "open_schedule":
            self.app.push_screen(ScheduleScreen(self.instance))
        elif action_key == "open_bot":
            self.app.push_screen(BotConfigScreen(self.instance))
        elif action_key == "open_mods":
            self.app.push_screen(ModManagerScreen(self.instance))
        elif action_key == "open_cleanup":
            self.app.push_screen(CleanupScreen(self.instance))
        elif action_key == "open_live_logs":
            self.app.push_screen(TailLogScreen(self.instance))

    def _render_active_panel(self) -> None:
        self.query_one("#manage-panel-title", Label).update(self._panel_title())
        log = self.query_one("#manage-panel-log", RichLog)
        log.clear()

        if self._active_panel == "status":
            log.write(self._build_status_details_text())
        elif self._active_panel == "ports":
            log.write(self._build_ports_text())
        elif self._active_panel == "config":
            log.write(self._build_config_text())
        elif self._active_panel == "mods":
            log.write(self._build_mods_text())
        elif self._active_panel == "schedule":
            log.write(self._build_schedule_text())
        elif self._active_panel == "bot":
            log.write(self._build_bot_text())
        elif self._active_panel == "cleanup":
            log.write(self._build_cleanup_text())
        elif self._active_panel == "logs":
            log.write(self._build_logs_text())
        else:
            log.write(self._build_overview_text())

        for panel, button_id, _label in self._nav_items():
            button = self.query_one(f"#{button_id}", Button)
            button.variant = "primary" if panel == self._active_panel else "default"
        self._update_context_actions()

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalGroup(id="manage-container"):
            with VerticalGroup(id="manage-header"):
                with HorizontalGroup(id="manage-title-row"):
                    with VerticalGroup(id="manage-title-block"):
                        yield Label(self._title_text(), id="manage-screen-title")
                        yield Label(self._server_display_name(), id="manage-server-name")
                        yield Label(
                            tr("Instance: {instance}", instance=self.instance),
                            id="manage-instance-id",
                        )
                    yield Label(_("Loading status..."), id="manage-runtime-badge")

                yield Label(_("Loading status..."), id="server-status")

            with HorizontalScroll(id="manage-nav"):
                for panel, button_id, label in self._nav_items():
                    variant = "primary" if panel == self._active_panel else "default"
                    yield Button(label, id=button_id, variant=variant)

            with HorizontalScroll(id="manage-action-row"):
                yield Button("...", id="btn_toggle", variant="primary")
                yield Button(_("Restart"), id="btn_restart", variant="warning")
                yield Button(_("Refresh Status"), id="btn_refresh_manage", variant="default")
                yield Button(_("Live Logs"), id="btn_context_primary", variant="primary")
                yield Button(_("Config"), id="btn_context_secondary", variant="default")
                yield Button(_("Back to Main Menu"), id="btn_back", variant="default")

            with VerticalScroll(id="manage-content"):
                yield Label(_("Overview"), id="manage-panel-title")
                yield RichLog(id="manage-panel-log", markup=True, highlight=False)
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        service_name = self._service_name()

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

        elif event.button.id == "btn_refresh_manage":
            self.action_refresh_state()

        elif event.button.id in self._context_action_keys:
            self._handle_context_action(self._context_action_keys[event.button.id])

        else:
            nav_targets = {button_id: panel for panel, button_id, _label in self._nav_items()}
            if event.button.id in nav_targets:
                self._select_panel(nav_targets[event.button.id])


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
                tr(
                    "Restart Schedule: {instance}",
                    instance=get_instance_display_label(self.instance),
                ),
                id="screen-title",
            )
            yield Label(
                _(
                    "Enter one or more exact restart times separated by commas "
                    "or spaces. Example: 05:00, 13:30, 22:00."
                ),
                id="schedule-help",
            )
            yield Label(_("Restart Time(s):"))
            yield Input(
                id="inp_restart_schedule",
                placeholder=_("05:00, 13:30, 22:00"),
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
                _("At least one restart time is required."),
                title=_("Restart Schedule"),
                severity="error",
            )
            return

        schedule_entries = normalize_on_calendar_entries(schedule_value)
        if not schedule_entries:
            self.app.notify(
                _("At least one restart time is required."),
                title=_("Restart Schedule"),
                severity="error",
            )
            return

        results = update_restart_timer_schedule(self.instance, schedule_entries)
        failures = [result.message for result in results if not result.success]
        if failures:
            self.app.notify(
                failures[0],
                title=_("Restart Schedule"),
                severity="error",
            )
            return

        pretty_schedule = format_schedule_for_input(schedule_entries)
        self.query_one("#inp_restart_schedule", Input).value = pretty_schedule
        self._loaded_schedule = pretty_schedule
        self.app.notify(
            tr("Restart schedule updated to {schedule}.", schedule=pretty_schedule),
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


class BotConfigScreen(Screen):
    """Screen for editing the optional Telegram bot `.env` settings."""

    BINDINGS = [
        ("b", "pop_screen", _("Back")),
        ("ctrl+s", "save_bot_settings", _("Save Bot Settings")),
        ("ctrl+r", "reload_bot_settings", _("Reload From Disk")),
        ("c", "copy_bot_env_path", _("Copy .env Path")),
    ]

    def __init__(self, instance: str, **kwargs):
        super().__init__(**kwargs)
        self.instance = instance
        self._enabled = False
        self._env_path = paths.bot_env_file(instance)

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalGroup(id="bot-config-container"):
            yield Label(
                tr(
                    "Telegram Bot: {instance}",
                    instance=get_instance_display_label(self.instance),
                ),
                id="screen-title",
            )
            yield Label(
                tr(
                    "Optional Telegram bot settings. armactl reads and writes "
                    "{path} as the single source of truth.",
                    path=self._env_path,
                ),
                id="bot-config-help",
            )
            yield RichLog(id="bot-status-log", markup=True)
            yield Label(_("Bot Token:"))
            yield Input(
                id="inp_bot_token",
                placeholder="123456789:ABCDEF...",
                password=True,
            )
            yield Label(_("Admin Chat IDs:"))
            yield Input(
                id="inp_bot_chat_ids",
                placeholder="123456789, -1001234567890",
            )
            yield Label(_("Bot Language:"))
            yield Input(id="inp_bot_language", placeholder="uk")
            with HorizontalGroup(id="bot-enable-buttons"):
                yield Button(_("Enable Bot"), id="btn_bot_enable", variant="success")
                yield Button(_("Disable Bot"), id="btn_bot_disable", variant="warning")
            with HorizontalGroup(id="bot-config-buttons"):
                yield Button(_("Save Bot Settings"), id="btn_save_bot", variant="success")
                yield Button(_("Reload From Disk"), id="btn_reload_bot", variant="default")
                yield Button(_("Copy .env Path"), id="btn_copy_bot_path", variant="primary")
                yield Button(_("Back"), id="btn_back_bot", variant="error")
            yield Label(
                _(
                    "Apply Bot Service installs/updates the secure helper and "
                    "systemd unit, then starts or restarts the bot automatically."
                ),
                id="bot-service-help",
            )
            with HorizontalGroup(id="bot-service-buttons"):
                yield Button(
                    _("Apply Bot Service"),
                    id="btn_install_bot_service",
                    variant="primary",
                )
                yield Button(_("Restart Bot"), id="btn_restart_bot_service", variant="success")
                yield Button(_("Stop Bot"), id="btn_stop_bot_service", variant="warning")
                yield Button(
                    _("Refresh Status"),
                    id="btn_refresh_bot_service",
                    variant="default",
                )
        yield Footer()

    def on_mount(self) -> None:
        self.reload_bot_settings(notify=False)
        self.query_one("#inp_bot_token", Input).focus()

    def _build_draft_config(self) -> tuple[BotConfig, list[str]]:
        token = self.query_one("#inp_bot_token", Input).value.strip()
        raw_chat_ids = self.query_one("#inp_bot_chat_ids", Input).value.strip()
        language = self.query_one("#inp_bot_language", Input).value.strip() or "uk"

        validation_errors: list[str] = []
        try:
            admin_chat_ids = parse_admin_chat_ids(raw_chat_ids)
        except BotConfigError as e:
            admin_chat_ids = []
            validation_errors.append(str(e))

        config = BotConfig(
            instance=self.instance,
            enabled=self._enabled,
            token=token,
            admin_chat_ids=admin_chat_ids,
            language=language,
            env_path=self._env_path,
        )
        validation_errors.extend(validate_bot_config(config))
        return config, validation_errors

    def _update_enable_buttons(self) -> None:
        enable_button = self.query_one("#btn_bot_enable", Button)
        disable_button = self.query_one("#btn_bot_disable", Button)
        enable_button.disabled = self._enabled
        disable_button.disabled = not self._enabled

    def _refresh_status_log(
        self,
        validation_errors: list[str] | None = None,
        *,
        include_service_status: bool = True,
    ) -> None:
        config, draft_errors = self._build_draft_config()
        if validation_errors is None:
            validation_errors = draft_errors

        log = self.query_one("#bot-status-log", RichLog)
        enabled_value = _("Yes") if config.enabled else _("No")
        admins_value = config.admin_chat_ids_text() or _("Missing")

        lines = [
            _("[bold cyan]Telegram Bot Status[/bold cyan]"),
            tr("Bot config file: {path}", path=self._env_path),
            tr("Bot enabled: {value}", value=enabled_value),
            tr("Bot token status: {value}", value=config.masked_token()),
            tr("Admin Chat IDs: {value}", value=admins_value),
            tr("Bot language: {value}", value=config.language),
        ]

        if include_service_status:
            bot_service = get_bot_service_status()
            service_installed = _("Yes") if bot_service.get("installed") else _("No")
            service_enabled = _("Yes") if bot_service.get("enabled") else _("No")
            runtime_status = bot_service.get("runtime", {})
            runtime_ready = _("Yes") if runtime_status.get("success") else _("No")
            privileged_channel = (
                _("Yes") if bot_service.get("privileged_channel_installed") else _("No")
            )
            helper_user = bot_service.get("privileged_channel_user") or _("Unknown")
            service_user = bot_service.get("service_user") or _("Unknown")
            current_linux_user = bot_service.get("current_linux_user") or _("Unknown")
            helper_match_value = bot_service.get("privileged_channel_matches_service_user")
            if helper_match_value is True:
                helper_matches = _("Yes")
            elif helper_match_value is False:
                helper_matches = _("No")
            else:
                helper_matches = _("Unknown")

            lines.extend(
                [
                    tr(
                        "Bot service unit: {value}",
                        value=bot_service.get("service_name", paths.BOT_SERVICE_NAME),
                    ),
                    tr(
                        "Bot service file: {path}",
                        path=bot_service.get("service_file", paths.bot_service_file()),
                    ),
                    tr("Bot service installed: {value}", value=service_installed),
                    tr("Bot service enabled on boot: {value}", value=service_enabled),
                    tr("Bot service user: {value}", value=service_user),
                    tr("Current Linux user: {value}", value=current_linux_user),
                    tr(
                        "Secure control channel installed: {value}",
                        value=privileged_channel,
                    ),
                    tr("Secure control channel user: {value}", value=helper_user),
                    tr(
                        "Secure control channel matches bot user: {value}",
                        value=helper_matches,
                    ),
                    tr(
                        "Bot service active state: {value}",
                        value=bot_service.get("active_state", _("Unknown")),
                    ),
                    tr("Bot runtime ready: {value}", value=runtime_ready),
                    tr(
                        "Bot runtime check: {value}",
                        value=runtime_status.get("message", _("Unknown")),
                    ),
                ]
            )

            description = str(bot_service.get("description", "")).strip()
            if description:
                lines.append(tr("Bot service description: {value}", value=description))
            if bot_service.get("privileged_channel_matches_service_user") is False:
                lines.append(
                    _(
                        "[bold yellow]Secure control channel is currently granted "
                        "to a different Linux user.[/bold yellow]"
                    )
                )

        if validation_errors:
            lines.append(_("[bold yellow]Validation warnings[/bold yellow]"))
            for error in validation_errors:
                lines.append(tr("- {error}", error=error))

        log.clear()
        log.write("\n".join(lines))

    def reload_bot_settings(self, notify: bool = True) -> None:
        try:
            ensure_bot_config(self.instance)
            config = load_bot_config(self.instance)
        except Exception as e:
            self.app.notify(
                tr(
                    "Failed to load Telegram bot settings: {error}",
                    error=redact_sensitive_text(e),
                ),
                title=_("Telegram Bot"),
                severity="error",
            )
            return

        self._enabled = config.enabled
        self.query_one("#inp_bot_token", Input).value = config.token
        self.query_one("#inp_bot_chat_ids", Input).value = config.admin_chat_ids_text()
        self.query_one("#inp_bot_language", Input).value = config.language
        self._update_enable_buttons()
        self._refresh_status_log(validation_errors=[], include_service_status=True)

        if notify:
            self.app.notify(
                _("Reloaded Telegram bot settings from disk."),
                title=_("Telegram Bot"),
            )

    def action_reload_bot_settings(self) -> None:
        self.reload_bot_settings(notify=True)

    def action_copy_bot_env_path(self) -> None:
        self.app.copy_to_clipboard(str(self._env_path))
        self.app.notify(
            tr("Copied Telegram bot config path: {path}", path=self._env_path),
            title=_("Clipboard"),
        )

    def action_save_bot_settings(self, notify: bool = True) -> bool:
        config, validation_errors = self._build_draft_config()
        if validation_errors:
            if notify:
                self.app.notify(
                    validation_errors[0],
                    title=_("Telegram Bot"),
                    severity="error",
                )
            self._refresh_status_log(validation_errors)
            return False

        try:
            saved_path = save_bot_config(config)
        except Exception as e:
            if notify:
                self.app.notify(
                    tr(
                        "Failed to save Telegram bot settings: {error}",
                        error=redact_sensitive_text(e),
                    ),
                    title=_("Telegram Bot"),
                    severity="error",
                )
            return False

        self._env_path = saved_path
        self._refresh_status_log(validation_errors=[], include_service_status=True)
        if notify:
            self.app.notify(
                tr("Saved Telegram bot settings to {path}.", path=saved_path),
                title=_("Telegram Bot"),
            )
        return True

    def action_install_bot_service(self) -> None:
        if not self.action_save_bot_settings(notify=False):
            self.app.notify(
                _("Save a valid bot config before installing the bot service."),
                title=_("Telegram Bot"),
                severity="error",
            )
            return

        was_running = bool(get_bot_service_status().get("active"))
        results = install_bot_service(self.instance)
        failures = [result.message for result in results if not result.success]
        self._refresh_status_log(validation_errors=[])
        if failures:
            self.app.notify(failures[0], title=_("Telegram Bot"), severity="error")
            return

        runtime_result = restart_bot_service() if was_running else start_bot_service()
        if not runtime_result.success:
            self._refresh_status_log(validation_errors=[])
            self.app.notify(
                runtime_result.message,
                title=_("Telegram Bot"),
                severity="error",
            )
            return

        self._refresh_status_log(validation_errors=[])
        self.app.notify(
            _("Bot service applied and bot runtime is ready."),
            title=_("Telegram Bot"),
        )

    def _handle_bot_service_action(self, action_label: str, result) -> None:
        self._refresh_status_log(validation_errors=[])
        self.app.notify(
            result.message,
            title=action_label,
            severity="error" if not result.success else "information",
        )

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id in {"inp_bot_token", "inp_bot_chat_ids", "inp_bot_language"}:
            self._refresh_status_log(include_service_status=False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_back_bot":
            self.app.pop_screen()
        elif event.button.id == "btn_bot_enable":
            self._enabled = True
            self._update_enable_buttons()
            self._refresh_status_log(include_service_status=False)
            self.app.notify(
                _("Telegram bot marked as enabled. Save settings to persist."),
                title=_("Telegram Bot"),
            )
        elif event.button.id == "btn_bot_disable":
            self._enabled = False
            self._update_enable_buttons()
            self._refresh_status_log(include_service_status=False)
            self.app.notify(
                _("Telegram bot marked as disabled. Save settings to persist."),
                title=_("Telegram Bot"),
            )
        elif event.button.id == "btn_save_bot":
            self.action_save_bot_settings()
        elif event.button.id == "btn_reload_bot":
            self.action_reload_bot_settings()
        elif event.button.id == "btn_copy_bot_path":
            self.action_copy_bot_env_path()
        elif event.button.id == "btn_install_bot_service":
            self.action_install_bot_service()
        elif event.button.id == "btn_restart_bot_service":
            self._handle_bot_service_action(_("Restart Bot"), restart_bot_service())
        elif event.button.id == "btn_stop_bot_service":
            self._handle_bot_service_action(_("Stop Bot"), stop_bot_service())
        elif event.button.id == "btn_refresh_bot_service":
            self._refresh_status_log(validation_errors=[])
            self.app.notify(_("Refreshed bot service status."), title=_("Telegram Bot"))


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
            yield Label(
                f"{_('Maintenance / Cleanup')}: {get_instance_display_label(self.instance)}",
                id="screen-title",
            )
            yield RichLog(id="info-log", markup=True)
            with HorizontalGroup(id="control-buttons"):
                yield Button(_("Clean Junk Files"), id="btn_clean_now", variant="warning")
                yield Button(
                    _("Clean Unused Workshop Addons"),
                    id="btn_clean_addons",
                    variant="error",
                )
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

        elif event.button.id == "btn_clean_addons":
            cfg = paths.config_file(self.instance)
            # Dry-run first to show what would be deleted.
            preview = cleanup_unconfigured_addons(cfg, dry_run=True)
            if preview.errors:
                self.app.notify(
                    tr(
                        "Workshop addon cleanup failed: {error}",
                        error="; ".join(preview.errors),
                    ),
                    title=_("Workshop Cleanup"),
                    severity="warning",
                )
                return
            if not preview.deleted:
                self.app.notify(
                    _("No unused workshop addon directories found."),
                    title=_("Workshop Cleanup"),
                )
                return

            dir_count = len(preview.deleted)
            freed = format_size(preview.bytes_deleted)
            prompt = tr(
                "Delete {count} unused workshop addon directory/directories "
                "and free {size}?",
                count=dir_count,
                size=freed,
            )

            def confirm_addon_cleanup(confirm: bool) -> None:
                if confirm:
                    result = cleanup_unconfigured_addons(cfg)
                    freed_real = format_size(result.bytes_deleted)
                    if result.deleted:
                        self.app.notify(
                            tr(
                                "Deleted {count} addon directories, freed {freed}.",
                                count=len(result.deleted),
                                freed=freed_real,
                            ),
                            title=_("Workshop Cleanup"),
                        )
                    if result.errors:
                        for err in result.errors:
                            self.app.notify(str(err), severity="warning")
                    self.refresh_stats()

            self.app.push_screen(
                ConfirmScreen(prompt),
                confirm_addon_cleanup,
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
        yield Label(
            tr(
                "Configuration: {instance}",
                instance=get_instance_display_label(self.instance),
            ),
            id="screen-title",
        )

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
            yield Input(
                id="inp_game_pass",
                placeholder=_("Leave empty for open public server"),
                password=True,
            )
            yield Label(_("Admin Password:"))
            yield Input(id="inp_admin_pass", password=True)
            yield Label(_("RCON Password:"))
            yield Input(id="inp_rcon_pass", password=True)

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
            self.app.notify(
                tr("Cannot parse config: {error}", error=redact_sensitive_text(e)),
                severity="error",
            )
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
            self.app.notify(
                tr("Error saving config: {error}", error=redact_sensitive_text(e)),
                severity="error",
            )


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
            yield Label(
                f"{_('Raw Config JSON')}: {get_instance_display_label(self.instance)}",
                id="screen-title",
            )
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
        self._mods_refresh_lock = Lock()

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalGroup(id="info-container"):
            yield Label(
                f"{_('Mods Manager')}: {get_instance_display_label(self.instance)}",
                id="screen-title",
            )

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

    async def on_mount(self) -> None:
        await self.action_refresh_mods()

    async def on_screen_resume(self) -> None:
        await self.action_refresh_mods()

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

    async def _handle_mod_pack_result(self, result: tuple[str, str] | None) -> None:
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

            added, skipped, update_result = import_mods_detailed(
                cfg,
                file_path,
                append=(action == "append"),
            )
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
            if update_result.enospc_retry_performed:
                self.app.notify(
                    _(
                        "Disk is full. Removed local files for deleted mod(s) "
                        "and retried saving config."
                    ),
                    severity="warning",
                )
            cleanup = update_result.cleanup_result
            if cleanup and cleanup.deleted:
                self.app.notify(
                    tr(
                        "Deleted {count} addon directories, freed {freed}.",
                        count=len(cleanup.deleted),
                        freed=format_size(cleanup.bytes_deleted),
                    ),
                    title=_("Workshop Cleanup"),
                )
            if cleanup and cleanup.errors:
                self.app.notify(
                    tr(
                        "Mod pack imported, but local addon cleanup failed: {error}",
                        error="; ".join(cleanup.errors),
                    ),
                    severity="warning",
                )
            await self.action_refresh_mods()
        except Exception as e:
            self.app.notify(tr("Mod pack operation failed: {error}", error=e), severity="error")

    async def action_refresh_mods(self) -> None:
        async with self._mods_refresh_lock:
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
            await list_view.clear()

            if not mods:
                await list_view.append(ListItem(Label(_("No mods installed."))))
                return

            for idx, mod in enumerate(mods, 1):
                await list_view.append(_build_mod_list_item(idx, mod))

    async def on_button_pressed(self, event: Button.Pressed) -> None:
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
            await self.action_refresh_mods()

        elif event.button.id == "btn_remove_mod":
            list_view = self.query_one("#mods-list", ListView)
            if list_view.highlighted_child is None:
                self.app.notify(_("Select a mod to remove first."), severity="warning")
                return

            mod_id = getattr(list_view.highlighted_child, "mod_id", None)
            if mod_id:

                async def confirm_remove(confirm: bool):
                    if confirm:
                        result = remove_mod_detailed(cfg, mod_id)
                        if result.config_changed:
                            if result.enospc_retry_performed:
                                self.app.notify(
                                    _(
                                        "Disk is full. Removed local files for "
                                        "deleted mod(s) and retried saving config."
                                    ),
                                    severity="warning",
                                )

                            cleanup = result.cleanup_result
                            if cleanup and cleanup.errors:
                                self.app.notify(
                                    tr(
                                        "Mod removed from config, but local addon "
                                        "cleanup failed: {error}",
                                        error="; ".join(cleanup.errors),
                                    ),
                                    severity="warning",
                                )

                            if cleanup and cleanup.deleted:
                                freed = format_size(cleanup.bytes_deleted)
                                self.app.notify(
                                    tr(
                                        "Removed mod {mod_id} and deleted {count} "
                                        "local addon directory/directories: "
                                        "{size} freed.",
                                        mod_id=mod_id,
                                        count=len(cleanup.deleted),
                                        size=freed,
                                    )
                                )
                            elif not (cleanup and cleanup.errors):
                                self.app.notify(
                                    tr(
                                        "Removed mod {mod_id}. "
                                        "No local addon files found.",
                                        mod_id=mod_id,
                                    )
                                )
                            await self.action_refresh_mods()
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
                await self.action_refresh_mods()
