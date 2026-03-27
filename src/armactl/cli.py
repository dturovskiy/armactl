"""CLI entry point for armactl.

This module defines the command structure. Each subcommand group
delegates to the corresponding backend module — no business logic here.
"""

from __future__ import annotations

import click

from armactl import __version__


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
    click.echo(f"[{ctx.obj['instance']}] status — not implemented yet")


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
    click.echo(f"[{ctx.obj['instance']}] ports — not implemented yet")


# ---------------------------------------------------------------------------
# Discovery / Install / Repair
# ---------------------------------------------------------------------------


@main.command()
@click.pass_context
def detect(ctx: click.Context) -> None:
    """Detect existing server installation."""
    click.echo(f"[{ctx.obj['instance']}] detect — not implemented yet")


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
