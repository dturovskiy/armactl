"""CLI entry point for armactl.

This module defines the command structure. Each subcommand group
delegates to the corresponding backend module — no business logic here.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from armactl import __version__
from armactl import paths as P


@click.group()
@click.version_option(version=__version__, prog_name="armactl")
@click.option(
    "--instance",
    default="default",
    help="Instance name (default: 'default').",
    show_default=True,
)
@click.option(
    "--json-output", "use_json",
    is_flag=True,
    default=False,
    help="Output in JSON format (for TUI integration).",
)
@click.pass_context
def main(ctx: click.Context, instance: str, use_json: bool) -> None:
    """armactl — installer, manager and TUI for Arma Reforger Dedicated Server."""
    ctx.ensure_object(dict)
    ctx.obj["instance"] = instance
    ctx.obj["json"] = use_json


def _get_state(ctx: click.Context):
    """Helper: run discovery and return state for current instance."""
    from armactl.discovery import discover
    return discover(instance=ctx.obj["instance"], save=False)


# ---------------------------------------------------------------------------
# Server commands
# ---------------------------------------------------------------------------


@main.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show server status."""
    from armactl.service_manager import get_service_status

    instance = ctx.obj["instance"]
    state = _get_state(ctx)

    if not state.server_installed:
        if ctx.obj["json"]:
            click.echo(json.dumps({"error": "no_server_found"}))
        else:
            click.echo(f"[{instance}] No server found. Run './armactl detect' or './armactl install'.")
        sys.exit(1)

    svc = get_service_status(state.service_name)

    if ctx.obj["json"]:
        click.echo(json.dumps({**state.to_dict(), **svc}, indent=2))
        return

    icon = "🟢" if state.server_running else "🔴"
    click.echo(f"[{instance}] Server: {icon} {'running' if state.server_running else 'stopped'}")
    click.echo(f"  Install dir: {state.install_dir}")
    click.echo(f"  Config:      {state.config_path}")
    click.echo(f"  Service:     {'✓' if state.service_exists else '✗'} {state.service_name}")
    if svc['enabled']:
        click.echo(f"  Auto-start:  ✓ enabled")
    click.echo(f"  Timer:       {'✓' if state.timer_exists else '✗'} {state.timer_name}")
    if svc['main_pid']:
        click.echo(f"  PID:         {svc['main_pid']}")
    if state.ports.game:
        click.echo(f"  Ports:       game={state.ports.game} a2s={state.ports.a2s} rcon={state.ports.rcon}")


@main.command()
@click.pass_context
def start(ctx: click.Context) -> None:
    """Start the server."""
    from armactl.service_manager import start_service

    instance = ctx.obj["instance"]
    state = _get_state(ctx)

    if not state.server_installed:
        click.echo(f"[{instance}] No server found.", err=True)
        sys.exit(1)

    if state.server_running:
        if ctx.obj["json"]:
            click.echo(json.dumps({"status": "already_running"}))
        else:
            click.echo(f"[{instance}] Server is already running.")
        return

    result = start_service(state.service_name)

    if ctx.obj["json"]:
        click.echo(json.dumps(result.to_dict()))
    else:
        click.echo(f"[{instance}] {result.message}")

    sys.exit(0 if result.success else 1)


@main.command()
@click.pass_context
def stop(ctx: click.Context) -> None:
    """Stop the server."""
    from armactl.service_manager import stop_service

    instance = ctx.obj["instance"]
    state = _get_state(ctx)

    if not state.server_installed:
        click.echo(f"[{instance}] No server found.", err=True)
        sys.exit(1)

    if not state.server_running:
        if ctx.obj["json"]:
            click.echo(json.dumps({"status": "already_stopped"}))
        else:
            click.echo(f"[{instance}] Server is already stopped.")
        return

    result = stop_service(state.service_name)

    if ctx.obj["json"]:
        click.echo(json.dumps(result.to_dict()))
    else:
        click.echo(f"[{instance}] {result.message}")

    sys.exit(0 if result.success else 1)


@main.command()
@click.pass_context
def restart(ctx: click.Context) -> None:
    """Restart the server."""
    from armactl.service_manager import restart_service

    instance = ctx.obj["instance"]
    state = _get_state(ctx)

    if not state.server_installed:
        click.echo(f"[{instance}] No server found.", err=True)
        sys.exit(1)

    result = restart_service(state.service_name)

    if ctx.obj["json"]:
        click.echo(json.dumps(result.to_dict()))
    else:
        click.echo(f"[{instance}] {result.message}")

    sys.exit(0 if result.success else 1)


@main.command()
@click.option("-n", "--lines", default=50, help="Number of log lines to show.")
@click.option("-f", "--follow", is_flag=True, default=False, help="Follow logs in real-time.")
@click.pass_context
def logs(ctx: click.Context, lines: int, follow: bool) -> None:
    """Tail server logs."""
    from armactl.logs import get_logs_text, show_logs

    instance = ctx.obj["instance"]
    state = _get_state(ctx)

    if not state.service_exists:
        click.echo(f"[{instance}] Service not found.", err=True)
        sys.exit(1)

    if ctx.obj["json"]:
        text = get_logs_text(state.service_name, lines=lines)
        click.echo(json.dumps({"service": state.service_name, "logs": text}))
        return

    exit_code = show_logs(state.service_name, lines=lines, follow=follow)
    sys.exit(exit_code)


@main.command()
@click.pass_context
def ports(ctx: click.Context) -> None:
    """Show listening ports."""
    from armactl.discovery import discover
    from armactl.ports import format_ports_table

    instance = ctx.obj["instance"]
    state = discover(instance=instance, save=False)

    game = state.ports.game or 2001
    a2s = state.ports.a2s or 17777
    rcon = state.ports.rcon or 19999

    click.echo(f"[{instance}] Port status:")
    click.echo(format_ports_table(game, a2s, rcon))


# ---------------------------------------------------------------------------
# Discovery / Install / Repair
# ---------------------------------------------------------------------------


@main.command()
@click.option(
    "--install-dir",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Manually specify server install directory.",
)
@click.option(
    "--config-path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Manually specify config.json path.",
)
@click.pass_context
def detect(ctx: click.Context, install_dir: Path | None, config_path: Path | None) -> None:
    """Detect existing server installation."""
    from armactl.discovery import discover, discover_manual

    instance = ctx.obj["instance"]

    if install_dir and config_path:
        click.echo(f"[{instance}] Manual detection...")
        state = discover_manual(
            install_dir=install_dir,
            config_path=config_path,
            instance=instance,
        )
    else:
        click.echo(f"[{instance}] Running auto-detection...")
        state = discover(instance=instance)

    if state.server_installed:
        binary_path = Path(state.install_dir) / "ArmaReforgerServer"
        click.echo(f"  ✓ Server found at: {state.install_dir}")
        click.echo(f"  ✓ Binary:  {'found' if state.binary_exists else 'missing'} ({binary_path})")
        click.echo(f"  ✓ Config:  {'found' if state.config_exists else 'missing'} ({state.config_path})")
        click.echo(f"  ✓ Service: {'found' if state.service_exists else 'missing'}")
        click.echo(f"  ✓ Timer:   {'found' if state.timer_exists else 'missing'}")
        icon = "🟢" if state.server_running else "🔴"
        click.echo(f"  ✓ Status:  {icon} {'running' if state.server_running else 'stopped'}")
        if state.ports.game:
            click.echo(f"  ✓ Ports:   game={state.ports.game} a2s={state.ports.a2s} rcon={state.ports.rcon}")
        if state.migrated_from:
            click.echo(f"  ⚠ Detected from legacy paths (migrated_from={state.migrated_from})")
        click.echo(f"  State saved to: {P.state_file(instance)}")
    else:
        click.echo("  ✗ No server found.")
        click.echo("  Use 'armactl install' to install, or")
        click.echo("  Use 'armactl detect --install-dir <path> --config-path <path>' for manual detection.")


@main.command()
@click.pass_context
def install(ctx: click.Context) -> None:
    """Install server from scratch."""
    click.echo(f"[{ctx.obj['instance']}] install — not implemented yet")


@main.command()
@click.pass_context
def repair(ctx: click.Context) -> None:
    """Repair broken installation."""
    click.echo(f"[{ctx.obj['instance']}] repair — not implemented yet")


# ---------------------------------------------------------------------------
# Config commands
# ---------------------------------------------------------------------------


@main.group()
def config() -> None:
    """Manage server configuration."""


@config.command("show")
@click.pass_context
def config_show(ctx: click.Context) -> None:
    """Show current configuration."""
    from armactl.config_manager import load_config, ConfigError

    instance = ctx.obj["instance"]
    state = _get_state(ctx)

    if not state.config_exists:
        click.echo(f"[{instance}] Config not found.", err=True)
        sys.exit(1)

    try:
        data = load_config(state.config_path)
        click.echo(json.dumps(data, indent=4))
    except ConfigError as e:
        click.echo(f"[{instance}] {e}", err=True)
        sys.exit(1)


@config.command("set-name")
@click.argument("name")
@click.pass_context
def config_set_name(ctx: click.Context, name: str) -> None:
    """Set server name."""
    from armactl.config_manager import set_value, ConfigError

    instance = ctx.obj["instance"]
    state = _get_state(ctx)

    if not state.config_exists:
        click.echo(f"[{instance}] Config not found.", err=True)
        sys.exit(1)

    try:
        set_value(state.config_path, "game", "name", name)
        click.echo(f"[{instance}] Server name set to '{name}'.")
    except ConfigError as e:
        click.echo(f"[{instance}] {e}", err=True)
        sys.exit(1)


@config.command("set-scenario")
@click.argument("scenario_id")
@click.pass_context
def config_set_scenario(ctx: click.Context, scenario_id: str) -> None:
    """Set scenario ID."""
    from armactl.config_manager import set_value, ConfigError

    instance = ctx.obj["instance"]
    state = _get_state(ctx)

    if not state.config_exists:
        click.echo(f"[{instance}] Config not found.", err=True)
        sys.exit(1)

    try:
        set_value(state.config_path, "game", "scenarioId", scenario_id)
        click.echo(f"[{instance}] Scenario ID set to '{scenario_id}'.")
    except ConfigError as e:
        click.echo(f"[{instance}] {e}", err=True)
        sys.exit(1)


@config.command("set-maxplayers")
@click.argument("count", type=int)
@click.pass_context
def config_set_maxplayers(ctx: click.Context, count: int) -> None:
    """Set max players."""
    from armactl.config_manager import set_value, ConfigError

    instance = ctx.obj["instance"]
    state = _get_state(ctx)

    if not state.config_exists:
        click.echo(f"[{instance}] Config not found.", err=True)
        sys.exit(1)

    try:
        set_value(state.config_path, "game", "maxPlayers", count)
        click.echo(f"[{instance}] Max players set to {count}.")
    except ConfigError as e:
        click.echo(f"[{instance}] {e}", err=True)
        sys.exit(1)


@config.command("set-password-admin")
@click.argument("password")
@click.pass_context
def config_set_password_admin(ctx: click.Context, password: str) -> None:
    """Set admin password."""
    from armactl.config_manager import set_value, ConfigError

    instance = ctx.obj["instance"]
    state = _get_state(ctx)

    if not state.config_exists:
        click.echo(f"[{instance}] Config not found.", err=True)
        sys.exit(1)

    try:
        set_value(state.config_path, "", "adminPassword", password)
        click.echo(f"[{instance}] Admin password updated.")
    except ConfigError as e:
        click.echo(f"[{instance}] {e}", err=True)
        sys.exit(1)


@config.command("set-rcon-password")
@click.argument("password")
@click.pass_context
def config_set_rcon_password(ctx: click.Context, password: str) -> None:
    """Set RCON password."""
    # RCON password uses dedicated server password game properties
    from armactl.config_manager import set_value, ConfigError

    instance = ctx.obj["instance"]
    state = _get_state(ctx)

    if not state.config_exists:
        click.echo(f"[{instance}] Config not found.", err=True)
        sys.exit(1)

    try:
        set_value(state.config_path, "", "password", password)
        click.echo(f"[{instance}] RCON/Server password updated.")
    except ConfigError as e:
        click.echo(f"[{instance}] {e}", err=True)
        sys.exit(1)


@config.command("validate")
@click.pass_context
def config_validate(ctx: click.Context) -> None:
    """Validate configuration."""
    from armactl.config_manager import validate_config, ConfigError

    instance = ctx.obj["instance"]
    state = _get_state(ctx)

    if not state.config_exists:
        click.echo(f"[{instance}] Config not found.", err=True)
        sys.exit(1)

    try:
        errors = validate_config(config_path=state.config_path)
        if errors:
            click.echo(f"[{instance}] Validation failed:")
            for err in errors:
                click.echo(f"  - {err}")
            sys.exit(1)
        else:
            click.echo(f"[{instance}] Config JSON format is valid.")
            sys.exit(0)
    except ConfigError as e:
        click.echo(f"[{instance}] {e}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Service commands
# ---------------------------------------------------------------------------

@main.group()
def service() -> None:
    """Manage systemd service."""

@service.command("install")
@click.pass_context
def service_install(ctx: click.Context) -> None:
    """Generate and install systemd service."""
    from armactl.service_manager import generate_services
    instance = ctx.obj["instance"]
    click.echo(f"[{instance}] Installing services...")
    results = generate_services(instance=instance)
    for r in results:
        click.echo(f"  {'✓' if r.success else '✗'} {r.message}")

@service.command("enable")
@click.pass_context
def service_enable_cmd(ctx: click.Context) -> None:
    """Enable systemd service."""
    from armactl.service_manager import enable_service
    from armactl import paths as P
    instance = ctx.obj["instance"]
    service_name = f"armareforger@{instance}.service" if instance != "default" else P.SERVICE_NAME
    result = enable_service(service_name)
    click.echo(f"[{instance}] {result.message}")

@service.command("disable")
@click.pass_context
def service_disable_cmd(ctx: click.Context) -> None:
    """Disable systemd service."""
    from armactl.service_manager import disable_service
    from armactl import paths as P
    instance = ctx.obj["instance"]
    service_name = f"armareforger@{instance}.service" if instance != "default" else P.SERVICE_NAME
    result = disable_service(service_name)
    click.echo(f"[{instance}] {result.message}")

@service.command("status")
@click.pass_context
def service_status_cmd(ctx: click.Context) -> None:
    """Show detailed service status."""
    ctx.invoke(status)

# ---------------------------------------------------------------------------
# Timer commands
# ---------------------------------------------------------------------------

@main.group()
def timer() -> None:
    """Manage systemd timer."""

@timer.command("install")
@click.pass_context
def timer_install(ctx: click.Context) -> None:
    """Generate and install systemd timer."""
    # Already done in generate_services, but we expose it or just invoke the same
    from armactl.service_manager import generate_services
    instance = ctx.obj["instance"]
    click.echo(f"[{instance}] Installing timer (and service files)...")
    results = generate_services(instance=instance)
    for r in results:
        click.echo(f"  {'✓' if r.success else '✗'} {r.message}")

@timer.command("enable")
@click.pass_context
def timer_enable_cmd(ctx: click.Context) -> None:
    """Enable systemd timer."""
    from armactl.service_manager import enable_service
    from armactl import paths as P
    instance = ctx.obj["instance"]
    timer_name = f"armareforger-restart@{instance}.timer" if instance != "default" else P.TIMER_NAME
    result = enable_service(timer_name)
    click.echo(f"[{instance}] {result.message}")

@timer.command("disable")
@click.pass_context
def timer_disable_cmd(ctx: click.Context) -> None:
    """Disable systemd timer."""
    from armactl.service_manager import disable_service
    from armactl import paths as P
    instance = ctx.obj["instance"]
    timer_name = f"armareforger-restart@{instance}.timer" if instance != "default" else P.TIMER_NAME
    result = disable_service(timer_name)
    click.echo(f"[{instance}] {result.message}")

# ---------------------------------------------------------------------------
# Mods commands
# ---------------------------------------------------------------------------


@main.group()
def mods() -> None:
    """Manage server mods."""


@mods.command("list")
@click.pass_context
def mods_list(ctx: click.Context) -> None:
    """List installed mods."""
    click.echo(f"[{ctx.obj['instance']}] mods list — not implemented yet")


@mods.command("add")
@click.argument("mod_id")
@click.argument("name")
@click.pass_context
def mods_add(ctx: click.Context, mod_id: str, name: str) -> None:
    """Add a mod."""
    click.echo(f"[{ctx.obj['instance']}] mods add {mod_id} '{name}' — not implemented yet")


@mods.command("remove")
@click.argument("mod_id")
@click.pass_context
def mods_remove(ctx: click.Context, mod_id: str) -> None:
    """Remove a mod."""
    click.echo(f"[{ctx.obj['instance']}] mods remove {mod_id} — not implemented yet")


@mods.command("dedupe")
@click.pass_context
def mods_dedupe(ctx: click.Context) -> None:
    """Remove duplicate mods."""
    click.echo(f"[{ctx.obj['instance']}] mods dedupe — not implemented yet")


@mods.command("count")
@click.pass_context
def mods_count(ctx: click.Context) -> None:
    """Show mod count."""
    click.echo(f"[{ctx.obj['instance']}] mods count — not implemented yet")


# ---------------------------------------------------------------------------
# Schedule commands
# ---------------------------------------------------------------------------


@main.group()
def schedule() -> None:
    """Manage restart schedule."""


@schedule.command("show")
@click.pass_context
def schedule_show(ctx: click.Context) -> None:
    """Show current restart schedule."""
    import subprocess
    from armactl import paths as P
    instance = ctx.obj["instance"]
    timer_name = f"armareforger-restart@{instance}.timer" if instance != "default" else P.TIMER_NAME
    
    try:
        ans = subprocess.run(["systemctl", "show", timer_name, "--property=TimersCalendar"], capture_output=True, text=True)
        click.echo(f"[{instance}] Schedule: {ans.stdout.strip()}")
    except OSError:
        click.echo(f"[{instance}] Failed to read timer status.")

@schedule.command("set")
@click.argument("cron_expr")
@click.pass_context
def schedule_set(ctx: click.Context, cron_expr: str) -> None:
    """Set restart schedule (OnCalendar expression)."""
    import re

    from armactl.service_manager import generate_services
    instance = ctx.obj["instance"]

    # If the user just provides '05:00' or '05:00:00', format to systemd OnCalendar '*-*-* HH:MM:SS'
    if re.match(r"^\d{1,2}:\d{2}(:\d{2})?$", cron_expr):
        if cron_expr.count(":") == 1:
            cron_expr += ":00"
        cron_expr = f"*-*-* {cron_expr}"

    click.echo(f"[{instance}] Updating schedule to '{cron_expr}'...")
    results = generate_services(instance=instance, on_calendar=cron_expr)
    for r in results:
        if "timer" in r.message.lower() or "daemon" in r.message.lower():
            click.echo(f"  {'✓' if r.success else '✗'} {r.message}")

@schedule.command("enable")
@click.pass_context
def schedule_enable(ctx: click.Context) -> None:
    """Enable scheduled restarts."""
    ctx.invoke(timer_enable_cmd)

@schedule.command("disable")
@click.pass_context
def schedule_disable(ctx: click.Context) -> None:
    """Disable scheduled restarts."""
    ctx.invoke(timer_disable_cmd)

@schedule.command("restart-now")
@click.pass_context
def schedule_restart_now(ctx: click.Context) -> None:
    """Trigger immediate restart via timer service."""
    from armactl.service_manager import start_service
    from armactl import paths as P
    instance = ctx.obj["instance"]
    restart_service_name = f"armareforger-restart@{instance}.service" if instance != "default" else P.RESTART_SERVICE_NAME
    
    click.echo(f"[{instance}] Triggering restart...")
    res = start_service(restart_service_name)
    click.echo(f"[{instance}] {res.message}")
