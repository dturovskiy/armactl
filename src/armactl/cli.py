"""CLI entry point for armactl.

This module defines the command structure. Each subcommand group
delegates to the corresponding backend module — no business logic here.
"""

from __future__ import annotations

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
@click.pass_context
def main(ctx: click.Context, instance: str) -> None:
    """armactl — installer, manager and TUI for Arma Reforger Dedicated Server."""
    ctx.ensure_object(dict)
    ctx.obj["instance"] = instance


# ---------------------------------------------------------------------------
# Server commands
# ---------------------------------------------------------------------------


@main.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show server status."""
    from armactl.discovery import discover

    instance = ctx.obj["instance"]
    state = discover(instance=instance, save=False)

    if not state.server_installed:
        click.echo(f"[{instance}] No server found. Run 'armactl detect' or 'armactl install'.")
        return

    icon = "🟢" if state.server_running else "🔴"
    click.echo(f"[{instance}] Server: {icon} {'running' if state.server_running else 'stopped'}")
    click.echo(f"  Install dir: {state.install_dir}")
    click.echo(f"  Config:      {state.config_path}")
    click.echo(f"  Service:     {'✓' if state.service_exists else '✗'} {state.service_name}")
    click.echo(f"  Timer:       {'✓' if state.timer_exists else '✗'} {state.timer_name}")
    if state.ports.game:
        click.echo(f"  Ports:       game={state.ports.game} a2s={state.ports.a2s} rcon={state.ports.rcon}")


@main.command()
@click.pass_context
def start(ctx: click.Context) -> None:
    """Start the server."""
    click.echo(f"[{ctx.obj['instance']}] start — not implemented yet")


@main.command()
@click.pass_context
def stop(ctx: click.Context) -> None:
    """Stop the server."""
    click.echo(f"[{ctx.obj['instance']}] stop — not implemented yet")


@main.command()
@click.pass_context
def restart(ctx: click.Context) -> None:
    """Restart the server."""
    click.echo(f"[{ctx.obj['instance']}] restart — not implemented yet")


@main.command()
@click.pass_context
def logs(ctx: click.Context) -> None:
    """Tail server logs."""
    click.echo(f"[{ctx.obj['instance']}] logs — not implemented yet")


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
        click.echo(f"  ✓ Server found at: {state.install_dir}")
        click.echo(f"  ✓ Binary:  {'found' if state.binary_exists else 'missing'}")
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
    click.echo(f"[{ctx.obj['instance']}] config show — not implemented yet")


@config.command("set-name")
@click.argument("name")
@click.pass_context
def config_set_name(ctx: click.Context, name: str) -> None:
    """Set server name."""
    click.echo(f"[{ctx.obj['instance']}] config set-name '{name}' — not implemented yet")


@config.command("set-scenario")
@click.argument("scenario_id")
@click.pass_context
def config_set_scenario(ctx: click.Context, scenario_id: str) -> None:
    """Set scenario ID."""
    click.echo(f"[{ctx.obj['instance']}] config set-scenario '{scenario_id}' — not implemented yet")


@config.command("set-maxplayers")
@click.argument("count", type=int)
@click.pass_context
def config_set_maxplayers(ctx: click.Context, count: int) -> None:
    """Set max players."""
    click.echo(f"[{ctx.obj['instance']}] config set-maxplayers {count} — not implemented yet")


@config.command("validate")
@click.pass_context
def config_validate(ctx: click.Context) -> None:
    """Validate configuration."""
    click.echo(f"[{ctx.obj['instance']}] config validate — not implemented yet")


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
    click.echo(f"[{ctx.obj['instance']}] schedule show — not implemented yet")


@schedule.command("set")
@click.argument("cron_expr")
@click.pass_context
def schedule_set(ctx: click.Context, cron_expr: str) -> None:
    """Set restart schedule (OnCalendar expression)."""
    click.echo(f"[{ctx.obj['instance']}] schedule set '{cron_expr}' — not implemented yet")


@schedule.command("enable")
@click.pass_context
def schedule_enable(ctx: click.Context) -> None:
    """Enable scheduled restarts."""
    click.echo(f"[{ctx.obj['instance']}] schedule enable — not implemented yet")


@schedule.command("disable")
@click.pass_context
def schedule_disable(ctx: click.Context) -> None:
    """Disable scheduled restarts."""
    click.echo(f"[{ctx.obj['instance']}] schedule disable — not implemented yet")


@schedule.command("restart-now")
@click.pass_context
def schedule_restart_now(ctx: click.Context) -> None:
    """Trigger immediate restart."""
    click.echo(f"[{ctx.obj['instance']}] schedule restart-now — not implemented yet")
