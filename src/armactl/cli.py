"""CLI entry point for armactl.

This module defines the command structure. Each subcommand group
delegates to the corresponding backend module — no business logic here.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import click

from armactl import __version__, paths
from armactl.ports import WEB_PANEL_DEFAULT_PORT
from armactl.web.launcher import DEFAULT_WEB_HOST

if TYPE_CHECKING:
    from armactl.web.auth.models import UserRecord
    from armactl.web.runtime import WebRuntimeConfig


@click.group(invoke_without_command=True)
@click.version_option(version=__version__, prog_name="armactl")
@click.option(
    "--instance",
    default="default",
    help="Instance name (default: 'default').",
    show_default=True,
)
@click.option(
    "--json-output",
    "use_json",
    is_flag=True,
    default=False,
    help="Output in JSON format (for TUI integration).",
)
@click.pass_context
def main(ctx: click.Context, instance: str, use_json: bool) -> None:
    """armactl — installer, manager and TUI for Arma Reforger Dedicated Server."""
    try:
        instance = paths.validate_instance_name(instance)
    except paths.InvalidInstanceNameError as e:
        raise click.BadParameter(str(e), param_hint="--instance") from e

    # Add common args to context
    ctx.ensure_object(dict)
    ctx.obj["instance"] = instance
    ctx.obj["json"] = use_json

    if ctx.invoked_subcommand is None:
        import subprocess
        import threading
        import time

        def keep_sudo_alive() -> None:
            while True:
                time.sleep(300)  # Refresh sudo timestamp every 5 mins
                try:
                    subprocess.run(["sudo", "-n", "-v"], capture_output=True)
                except Exception:
                    pass

        # Pre-authenticate sudo immediately so TUI doesn't get messed up later
        click.echo("Authorizing sudo privileges for server management...")
        try:
            subprocess.run(["sudo", "-v"], check=True)
            # Start daemon thread to keep sudo alive while TUI is open
            t = threading.Thread(target=keep_sudo_alive, daemon=True)
            t.start()
        except subprocess.CalledProcessError:
            click.echo(
                "Failed to acquire sudo privileges! Background commands might fail.",
                err=True,
            )

        from armactl.tui.app import run_tui

        # If no strict command given, launch the visual TUI
        run_tui(instance)


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
    from armactl import metrics, paths
    from armactl.redaction import redact_sensitive_text
    from armactl.sat_admin_guard import SatAdminGuardError, inspect_sat_admin_config
    from armactl.service_manager import get_service_status

    instance = ctx.obj["instance"]
    state = _get_state(ctx)

    if not state.server_installed:
        if ctx.obj["json"]:
            click.echo(json.dumps({"error": "no_server_found"}))
        elif state.has_install_evidence():
            click.echo(
                f"[{instance}] Server installation is incomplete or unverified. "
                "Run './armactl repair'."
            )
            click.echo(f"  Install dir: {state.install_dir}")
            click.echo(f"  Package integrity: {state.package_integrity or 'unknown'}")
            click.echo(
                f"  Config:      {'found' if state.config_exists else 'missing'} "
                f"({state.config_path})"
            )
        else:
            click.echo(
                f"[{instance}] No server found. Run './armactl detect' or './armactl install'."
            )
        sys.exit(1)

    svc = get_service_status(state.service_name)

    sat_status = None
    sat_error = ""
    try:
        sat_status = inspect_sat_admin_config(state.config_path)
    except SatAdminGuardError as error:
        sat_error = redact_sensitive_text(error)

    if ctx.obj["json"]:
        payload = {**state.to_dict(), **svc}
        if sat_status is not None:
            payload["sat_admin_guard"] = sat_status.to_dict()
        elif sat_error:
            payload["sat_admin_guard"] = {"available": False, "warning": sat_error}
        click.echo(json.dumps(payload, indent=2))
        return

    icon = "🟢" if state.server_running else "🔴"
    click.echo(f"[{instance}] Server: {icon} {'running' if state.server_running else 'stopped'}")
    click.echo(f"  Install dir: {state.install_dir}")
    click.echo(f"  Config:      {state.config_path}")
    click.echo(f"  Service:     {'✓' if state.service_exists else '✗'} {state.service_name}")
    if svc["enabled"]:
        click.echo("  Auto-start:  enabled")
    click.echo(f"  Timer:       {'✓' if state.timer_exists else '✗'} {state.timer_name}")
    if svc["main_pid"]:
        click.echo(f"  PID:         {svc['main_pid']}")
    fps_metrics = metrics.query_server_fps_metrics(paths.config_dir(instance))
    if fps_metrics.available:
        click.echo(f"  Server FPS:  {metrics.format_fps(fps_metrics.fps)}")
        click.echo(
            "  Frame time:  "
            f"{metrics.format_frame_time_ms(fps_metrics.frame_avg_ms)} avg / "
            f"{metrics.format_frame_time_ms(fps_metrics.frame_max_ms)} max"
        )
        click.echo(f"  Telemetry:   {metrics.format_duration(fps_metrics.age_seconds)} old")
    elif fps_metrics.stale:
        age_text = metrics.format_duration(fps_metrics.age_seconds)
        click.echo("  Server FPS:  stale")
        click.echo(f"  Telemetry:   {age_text} old")
    else:
        click.echo("  Server FPS:  unavailable")
    if state.ports.game:
        click.echo(
            f"  Ports:       game={state.ports.game} a2s={state.ports.a2s} rcon={state.ports.rcon}"
        )
    if sat_status is not None and sat_status.warning:
        click.echo(f"  SAT:         ! {sat_status.warning}")
    elif sat_error:
        click.echo(f"  SAT:         ! {sat_error}")


@main.command("sync-generated")
@click.pass_context
def sync_generated(ctx: click.Context) -> None:
    """Refresh generated runtime files without stopping the server."""
    from armactl.service_manager import sync_generated_start_script

    instance = ctx.obj["instance"]
    state = _get_state(ctx)

    if not state.server_installed:
        if ctx.obj["json"]:
            click.echo(json.dumps({"error": "no_server_found"}))
        else:
            click.echo(f"[{instance}] No server found.", err=True)
        sys.exit(1)

    result = sync_generated_start_script(instance)

    if ctx.obj["json"]:
        click.echo(json.dumps(result.to_dict(), indent=2))
    else:
        prefix = "✓" if result.success else "✗"
        click.echo(f"[{instance}] {prefix} {result.message}")
        if result.success and state.server_running:
            click.echo(
                f"[{instance}] Restart the server when convenient to apply launch script changes."
            )

    sys.exit(0 if result.success else 1)


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

    if not state.config_exists:
        click.echo(f"[{instance}] Config missing. Run './armactl repair' first.", err=True)
        sys.exit(1)

    if state.server_running:
        if ctx.obj["json"]:
            click.echo(json.dumps({"status": "already_running"}))
        else:
            click.echo(f"[{instance}] Server is already running.")
        return

    if not ctx.obj["json"]:
        click.echo(f"[{instance}] Starting server...")

    result = start_service(state.service_name)

    if ctx.obj["json"]:
        click.echo(json.dumps(result.to_dict()))
    else:
        if result.success:
            click.echo(f"[{instance}] ✓ Server started successfully.\n")
            ctx.invoke(status)
        else:
            click.echo(f"[{instance}] ✗ Failed to start server: {result.message}", err=True)

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

    if not ctx.obj["json"]:
        click.echo(f"[{instance}] Stopping server...")

    result = stop_service(state.service_name)

    if ctx.obj["json"]:
        click.echo(json.dumps(result.to_dict()))
    else:
        if result.success:
            click.echo(f"[{instance}] ✓ Server stopped successfully.")
        else:
            click.echo(f"[{instance}] ✗ Failed to stop server: {result.message}", err=True)

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

    if not state.config_exists:
        click.echo(f"[{instance}] Config missing. Run './armactl repair' first.", err=True)
        sys.exit(1)

    if not ctx.obj["json"]:
        click.echo(f"[{instance}] Restarting server...")

    result = restart_service(state.service_name)

    if ctx.obj["json"]:
        click.echo(json.dumps(result.to_dict()))
    else:
        if result.success:
            click.echo(f"[{instance}] ✓ Server restarted successfully.\n")
            ctx.invoke(status)
        else:
            click.echo(f"[{instance}] ✗ Failed to restart server: {result.message}", err=True)

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
@click.option("-n", "--lines", default=120, help="Journal lines to include per unit.")
@click.option("--no-journal", is_flag=True, default=False, help="Skip journalctl sections.")
@click.pass_context
def report(ctx: click.Context, lines: int, no_journal: bool) -> None:
    """Print a redacted support/debug diagnostic report."""
    from armactl.report import build_report

    instance = ctx.obj["instance"]
    text = build_report(instance=instance, lines=lines, include_journal=not no_journal)
    if ctx.obj["json"]:
        click.echo(json.dumps({"instance": instance, "report": text}))
        return
    click.echo(text, nl=False)


@main.group(invoke_without_command=True)
@click.pass_context
def ports(ctx: click.Context) -> None:
    """Manage and show firewall ports."""
    # If a subcommand was invoked (open, close), do not show status automatically
    if ctx.invoked_subcommand is None:
        ctx.invoke(ports_show)


@ports.command("show")
@click.pass_context
def ports_show(ctx: click.Context) -> None:
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


@ports.command("open")
@click.pass_context
def ports_open(ctx: click.Context) -> None:
    """Open server ports in UFW."""
    from armactl.discovery import discover
    from armactl.ports import manage_ports

    instance = ctx.obj["instance"]
    state = discover(instance=instance, save=False)

    game = state.ports.game or 2001
    a2s = state.ports.a2s or 17777
    rcon = state.ports.rcon or 19999

    click.echo(f"[{instance}] Opening ports using UFW...")
    for msg in manage_ports("open", game, a2s, rcon):
        click.echo(msg)


@ports.command("close")
@click.pass_context
def ports_close(ctx: click.Context) -> None:
    """Close server ports in UFW."""
    from armactl.discovery import discover
    from armactl.ports import manage_ports

    instance = ctx.obj["instance"]
    state = discover(instance=instance, save=False)

    game = state.ports.game or 2001
    a2s = state.ports.a2s or 17777
    rcon = state.ports.rcon or 19999

    click.echo(f"[{instance}] Closing ports using UFW...")
    for msg in manage_ports("close", game, a2s, rcon):
        click.echo(msg)


# ---------------------------------------------------------------------------
# Web commands
# ---------------------------------------------------------------------------


@main.group(invoke_without_command=True)
@click.option(
    "--data-root",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Optional web runtime data root.",
)
@click.option(
    "--access",
    type=click.Choice(["local", "lan"]),
    default=None,
    help="First-run access mode: local machine only or local network.",
)
@click.option(
    "--port",
    type=click.IntRange(1, 65535),
    default=WEB_PANEL_DEFAULT_PORT,
    show_default=True,
    help="TCP port for first-run web setup.",
)
@click.option(
    "--https-required/--no-https-required",
    default=None,
    help="Require HTTPS-aware deployment settings for web sessions.",
)
@click.option(
    "--owner",
    "owner_username",
    default=None,
    metavar="USERNAME",
    help="Initial owner username when the web panel has no owner yet.",
)
@click.pass_context
def web(
    ctx: click.Context,
    data_root: Path | None,
    access: str | None,
    port: int,
    https_required: bool | None,
    owner_username: str | None,
) -> None:
    """Set up or manage the browser web panel."""
    if ctx.invoked_subcommand is not None:
        return

    _run_web_quickstart_cli(
        data_root=data_root,
        access=access,
        port=port,
        https_required=https_required,
        owner_username=owner_username,
    )


def _web_quickstart_access_label(access: str) -> str:
    if access == "lan":
        return "local network"
    return "local machine"


def _web_quickstart_url(bind_host: str, bind_port: int) -> str:
    if bind_host == "0.0.0.0":
        return f"http://<server-ip>:{bind_port}"
    return f"http://{bind_host}:{bind_port}"


def _web_quickstart_success(result) -> bool:
    install_ok = all(service_result.success for service_result in result.install_result.results)
    start_ok = result.start_result is not None and result.start_result.success
    return install_ok and start_ok


def _web_quickstart_exit_code(result) -> int:
    for service_result in result.install_result.results:
        if not service_result.success:
            return service_result.exit_code or 1
    if result.start_result is not None and not result.start_result.success:
        return result.start_result.exit_code or 1
    return 0


def _format_web_quickstart_summary(result, access: str) -> str:
    config = result.config
    https_required = "yes" if config.https_required else "no"
    setup_status = "complete" if _web_quickstart_success(result) else "incomplete"
    owner_status = "already configured"
    if result.owner_created and result.owner_user is not None:
        owner_status = f"created {result.owner_user.username}"
    elif result.owner_existing:
        owner_status = "already configured"

    start_status = "not attempted"
    if result.start_result is not None:
        start_status = "started" if result.start_result.success else "failed"

    lines = [
        f"Web panel setup {setup_status}.",
        f"  Access:         {_web_quickstart_access_label(access)}",
        f"  URL:            {_web_quickstart_url(config.bind_host, config.bind_port)}",
        f"  Bind:           {config.bind_host}:{config.bind_port}",
        f"  HTTPS required: {https_required}",
        f"  Config file:    {config.env_path}",
        f"  Database:       {config.db_path}",
        f"  Owner:          {owner_status}",
        f"  Service:        {result.install_result.service_name}",
        "  Auto-start:     enabled after install",
        f"  Start now:      {start_status}",
    ]
    warning_line = _format_web_exposure_warning(config.bind_host, config.https_required)
    if warning_line is not None:
        lines.append(warning_line)
    for service_result in result.install_result.results:
        marker = "✓" if service_result.success else "✗"
        lines.append(f"  {marker} {service_result.message}")
    if result.start_result is not None:
        marker = "✓" if result.start_result.success else "✗"
        lines.append(f"  {marker} {result.start_result.message}")
    return "\n".join(lines)


def _run_web_quickstart_cli(
    *,
    data_root: Path | None,
    access: str | None,
    port: int,
    https_required: bool | None,
    owner_username: str | None,
) -> None:
    import getpass

    from armactl.web.quickstart import (
        LOCAL_ACCESS,
        WebQuickstartError,
        WebQuickstartRequest,
        bind_host_for_access_mode,
        run_web_quickstart,
        web_owner_exists,
    )

    click.echo("armactl web setup")
    access_mode = access or click.prompt(
        "Access mode (local = this machine only, lan = local network)",
        type=click.Choice(["local", "lan"]),
        default=LOCAL_ACCESS,
    )

    try:
        owner_exists = web_owner_exists(data_root)
    except Exception as error:
        raise click.ClickException(str(error)) from error

    owner_password = None
    if owner_exists:
        click.echo("Owner user already configured.")
    else:
        if owner_username is None:
            owner_username = click.prompt("Owner username", default=getpass.getuser())
        owner_password = click.prompt(
            "Owner password",
            hide_input=True,
            confirmation_prompt=True,
        )

    try:
        result = run_web_quickstart(
            WebQuickstartRequest(
                data_root=data_root,
                bind_host=bind_host_for_access_mode(access_mode),
                bind_port=port,
                https_required=https_required,
                owner_username=owner_username,
                owner_password=owner_password,
            )
        )
    except WebQuickstartError as error:
        raise click.ClickException(str(error)) from error

    click.echo(_format_web_quickstart_summary(result, access_mode))
    sys.exit(_web_quickstart_exit_code(result))


def _web_option_was_provided(ctx: click.Context, parameter_name: str) -> bool:
    return ctx.get_parameter_source(parameter_name) is click.core.ParameterSource.COMMANDLINE


def _format_web_exposure_warning(bind_host: str | None, https_required: bool) -> str | None:
    if not bind_host:
        return None
    from armactl.web.security.exposure import get_exposure_warning

    warning = get_exposure_warning(bind_host, https_required)
    if warning is None:
        return None
    return f"  Exposure warning: {warning.message}"


def _format_web_runtime_init_summary(
    config: WebRuntimeConfig,
    owner_user: UserRecord | None = None,
) -> str:
    https_required = "yes" if config.https_required else "no"
    lines = [
        "Web runtime initialized.",
        f"  Runtime dir:    {config.runtime_dir}",
        f"  Config file:    {config.env_path}",
        f"  Database:       {config.db_path}",
        f"  Audit log:      {config.audit_log_path}",
        f"  Bind:           {config.bind_host}:{config.bind_port}",
        f"  HTTPS required: {https_required}",
    ]
    warning_line = _format_web_exposure_warning(config.bind_host, config.https_required)
    if warning_line is not None:
        lines.append(warning_line)
    if owner_user is not None:
        lines.extend(
            [
                f"  Owner user:     {owner_user.username}",
                f"  Owner role:     {owner_user.role}",
            ]
        )
    return "\n".join(lines)


@web.command("init")
@click.option(
    "--data-root",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Optional runtime data root for local development.",
)
@click.option(
    "--host",
    default=DEFAULT_WEB_HOST,
    show_default=True,
    help="Bind host for the web runtime config.",
)
@click.option(
    "--port",
    type=click.IntRange(1, 65535),
    default=WEB_PANEL_DEFAULT_PORT,
    show_default=True,
    help="TCP port for the web panel.",
)
@click.option(
    "--https-required/--no-https-required",
    default=None,
    help="Require HTTPS-aware deployment settings for web sessions.",
)
@click.option(
    "--owner",
    "owner_username",
    default=None,
    metavar="USERNAME",
    help="Create the initial web owner user after runtime init.",
)
@click.pass_context
def web_init(
    ctx: click.Context,
    data_root: Path | None,
    host: str,
    port: int,
    https_required: bool | None,
    owner_username: str | None,
) -> None:
    """Initialize local web runtime config and database."""
    from dataclasses import replace

    from armactl.web.launcher import validate_web_port
    from armactl.web.runtime import (
        WebRuntimeConfigError,
        ensure_web_runtime,
        load_web_runtime_config,
        save_web_runtime_config,
    )

    owner_user = None

    try:
        if _web_option_was_provided(ctx, "port"):
            try:
                validate_web_port(port)
            except ValueError as e:
                raise WebRuntimeConfigError(str(e)) from e

        config = ensure_web_runtime(data_root)

        target_host = config.bind_host
        target_port = config.bind_port
        target_https_required = config.https_required

        if _web_option_was_provided(ctx, "host"):
            target_host = host
        if _web_option_was_provided(ctx, "port"):
            target_port = port
        if https_required is not None:
            target_https_required = https_required

        if (
            target_host != config.bind_host
            or target_port != config.bind_port
            or target_https_required != config.https_required
        ):
            save_web_runtime_config(
                replace(
                    config,
                    bind_host=target_host,
                    bind_port=target_port,
                    https_required=target_https_required,
                )
            )
            config = load_web_runtime_config(data_root)
    except WebRuntimeConfigError as e:
        raise click.ClickException(str(e)) from e

    if owner_username is not None:
        from armactl.web.auth import WebAuthError
        from armactl.web.auth.setup import owner_user_exists, setup_owner_user

        try:
            if owner_user_exists(config.db_path):
                raise click.ClickException("Web owner user already exists.")
        except WebAuthError as e:
            raise click.ClickException(str(e)) from e

        password = click.prompt(
            "Owner password",
            hide_input=True,
            confirmation_prompt=True,
        )
        try:
            setup_result = setup_owner_user(data_root, owner_username, password)
        except (WebAuthError, WebRuntimeConfigError) as e:
            raise click.ClickException(str(e)) from e

        config = setup_result.config
        owner_user = setup_result.user

    click.echo(_format_web_runtime_init_summary(config, owner_user))


@web.command("run")
@click.option(
    "--host",
    default=None,
    help="Override bind host for this foreground run; default comes from web.env.",
)
@click.option(
    "--port",
    type=click.IntRange(1, 65535),
    default=None,
    help="Override TCP port for this foreground run; default comes from web.env.",
)
@click.option(
    "--dev",
    is_flag=True,
    default=False,
    help="Enable Uvicorn reload for local development.",
)
@click.option(
    "--data-root",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Optional runtime data root for local development.",
)
@click.pass_context
def web_run(
    ctx: click.Context,
    host: str | None,
    port: int | None,
    dev: bool,
    data_root: Path | None,
) -> None:
    """Run the web panel in the foreground."""
    from armactl.web.launcher import (
        WebRunError,
        WebRunRequest,
        format_web_run_startup_summary,
        prepare_web_run,
        run_web_foreground,
    )
    from armactl.web.runtime import WebRuntimeConfigError

    request = WebRunRequest(
        host=host,
        port=port,
        dev=dev,
        data_root=data_root,
    )

    try:
        prepared = prepare_web_run(request)
    except (ValueError, WebRuntimeConfigError) as e:
        raise click.ClickException(str(e)) from e

    click.echo(format_web_run_startup_summary(prepared))

    try:
        run_web_foreground(prepared)
    except WebRunError as e:
        raise click.ClickException(str(e)) from e


def _format_web_service_install_summary(result) -> str:
    config = result.config
    https_required = "yes" if config.https_required else "no"
    lines = [
        "Web service install prepared.",
        f"  Service:        {result.service_name}",
        f"  Service file:   {result.service_path}",
        f"  Data root:      {config.data_root}",
        f"  Runtime dir:    {config.runtime_dir}",
        f"  Config file:    {config.env_path}",
        f"  Database:       {config.db_path}",
        f"  Audit log:      {config.audit_log_path}",
        f"  Bind:           {config.bind_host}:{config.bind_port}",
        f"  HTTPS required: {https_required}",
        "  Auto-start:     enabled after install",
        "  Start now:      no",
    ]
    warning_line = _format_web_exposure_warning(config.bind_host, config.https_required)
    if warning_line is not None:
        lines.append(warning_line)
    for service_result in result.results:
        marker = "✓" if service_result.success else "✗"
        lines.append(f"  {marker} {service_result.message}")
    return "\n".join(lines)


def _web_service_failed(results) -> bool:
    return any(not result.success for result in results)


def _echo_web_service_result(action: str, result) -> None:
    marker = "✓" if result.success else "✗"
    click.echo(f"Web service {action}.")
    click.echo(f"  {marker} {result.message}")
    sys.exit(0 if result.success else result.exit_code or 1)


def _echo_web_service_result_and_wait_for_http(action: str, result) -> None:
    marker = "✓" if result.success else "✗"
    click.echo(f"Web service {action}.")
    click.echo(f"  {marker} {result.message}")
    if not result.success:
        sys.exit(result.exit_code or 1)

    from armactl.web.service import check_web_http_health

    http_result = check_web_http_health(timeout_seconds=20)
    http_marker = "✓" if http_result.success else "✗"
    click.echo(f"  {http_marker} {http_result.message}")
    sys.exit(0 if http_result.success else http_result.exit_code or 1)


@web.group("service", help="Manage the production armactl web systemd service.")
def web_service() -> None:
    pass


@web_service.command("install", help="Install and enable armactl-web.service without starting it.")
@click.option(
    "--data-root",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Optional web runtime data root to bake into the service unit.",
)
def web_service_install(data_root: Path | None) -> None:
    from armactl.web.runtime import WebRuntimeConfigError
    from armactl.web.service import install_web_service

    try:
        result = install_web_service(data_root)
    except WebRuntimeConfigError as e:
        raise click.ClickException(str(e)) from e

    click.echo(_format_web_service_install_summary(result))
    if _web_service_failed(result.results):
        sys.exit(1)


@web_service.command("start", help="Start armactl-web.service.")
def web_service_start() -> None:
    from armactl.web.service import start_web_service

    _echo_web_service_result_and_wait_for_http("start", start_web_service())


@web_service.command("stop", help="Stop armactl-web.service.")
def web_service_stop() -> None:
    from armactl.web.service import stop_web_service

    _echo_web_service_result("stop", stop_web_service())


@web_service.command("restart", help="Restart armactl-web.service.")
def web_service_restart() -> None:
    from armactl.web.service import restart_web_service

    _echo_web_service_result_and_wait_for_http("restart", restart_web_service())


@web_service.command("enable", help="Enable armactl-web.service on boot.")
def web_service_enable() -> None:
    from armactl.web.service import enable_web_service

    _echo_web_service_result("enable", enable_web_service())


@web_service.command("disable", help="Disable armactl-web.service on boot.")
def web_service_disable() -> None:
    from armactl.web.service import disable_web_service

    _echo_web_service_result("disable", disable_web_service())


@web_service.command("status", help="Show armactl-web.service status.")
@click.option(
    "--data-root",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Optional web runtime data root used for safe config summary output.",
)
def web_service_status(data_root: Path | None) -> None:
    from armactl.web.service import get_web_service_status

    status = get_web_service_status(data_root)
    config = status.get("config", {})
    runtime = status.get("runtime", {})
    service_name = status.get("service_name", "armactl-web.service")
    service_file = status.get("service_file", "")
    installed = "yes" if status.get("installed") else "no"
    active = "yes" if status.get("active") else "no"
    enabled = "yes" if status.get("enabled") else "no"
    active_state = status.get("active_state", "unknown")
    main_pid = status.get("main_pid")
    click.echo("Web service status.")
    click.echo(f"  Service:        {service_name}")
    click.echo(f"  Service file:   {service_file}")
    click.echo(f"  Installed:      {installed}")
    click.echo(f"  Active:         {active}")
    click.echo(f"  Enabled:        {enabled}")
    click.echo(f"  State:          {active_state}")
    if main_pid:
        click.echo(f"  PID:            {main_pid}")
    if config.get("available"):
        https_required = "yes" if config.get("https_required") else "no"
        data_root_text = config.get("data_root")
        runtime_dir = config.get("runtime_dir")
        env_path = config.get("env_path")
        bind_host = config.get("bind_host")
        bind_port = config.get("bind_port")
        click.echo(f"  Data root:      {data_root_text}")
        click.echo(f"  Runtime dir:    {runtime_dir}")
        click.echo(f"  Config file:    {env_path}")
        click.echo(f"  Bind:           {bind_host}:{bind_port}")
        click.echo(f"  HTTPS required: {https_required}")
        exposure_warning = config.get("exposure_warning")
        warning_message = ""
        if isinstance(exposure_warning, dict):
            warning_message = str(exposure_warning.get("message") or "")
        if not warning_message:
            warning_line = _format_web_exposure_warning(
                str(bind_host or ""),
                bool(config.get("https_required")),
            )
            if warning_line is not None:
                warning_message = warning_line.replace("  Exposure warning: ", "", 1)
        if warning_message:
            click.echo(f"  Exposure warning: {warning_message}")
    elif config.get("error"):
        config_error = config.get("error")
        click.echo(f"  Runtime config: {config_error}")
    runtime_marker = "✓" if runtime.get("success") else "✗"
    runtime_message = runtime.get("message", "unknown")
    click.echo(f"  Runtime check:  {runtime_marker} {runtime_message}")
    http = status.get("http", {})
    if isinstance(http, dict) and http:
        http_marker = "✓" if http.get("success") else "✗"
        http_message = http.get("message", "unknown")
        click.echo(f"  HTTP check:     {http_marker} {http_message}")


# ---------------------------------------------------------------------------
# Player history commands
# ---------------------------------------------------------------------------


@main.group("player-history")
def player_history() -> None:
    """Manual player-history import tools."""


@player_history.command("collect")
@click.argument(
    "log_files",
    nargs=-1,
    required=True,
    type=click.Path(file_okay=True, dir_okay=True, path_type=Path),
)
@click.option(
    "--data-root",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Optional armactl data root containing instance players.db.",
)
@click.option(
    "--dry-run/--write",
    default=True,
    show_default=True,
    help="Preview parsed counts without writing, or import into players.db.",
)
@click.option(
    "--max-bytes",
    type=click.IntRange(1),
    default=None,
    help="Maximum accepted file size in bytes; larger files are skipped.",
)
@click.option(
    "--max-lines",
    type=click.IntRange(1),
    default=None,
    help="Maximum lines scanned from each accepted file.",
)
@click.pass_context
def player_history_collect(
    ctx: click.Context,
    log_files: tuple[Path, ...],
    data_root: Path | None,
    dry_run: bool,
    max_bytes: int | None,
    max_lines: int | None,
) -> None:
    """Parse explicitly supplied text log files; never reads live journals."""
    from armactl.player_log_collector import (
        DEFAULT_MAX_FILE_BYTES,
        DEFAULT_MAX_FILE_LINES,
        collect_player_log_events,
        format_player_log_collection_summary,
    )
    from armactl.web.services.player_registry import player_registry_db_path

    instance = ctx.obj["instance"]
    root = data_root or paths.DEFAULT_DATA_ROOT
    summary = collect_player_log_events(
        log_files,
        player_registry_db_path(instance, data_root=root),
        dry_run=dry_run,
        max_bytes=max_bytes or DEFAULT_MAX_FILE_BYTES,
        max_lines=max_lines or DEFAULT_MAX_FILE_LINES,
    )
    if ctx.obj["json"]:
        click.echo(json.dumps(summary.to_dict(), indent=2))
    else:
        click.echo(format_player_log_collection_summary(summary))
    if summary.error_count:
        sys.exit(1)


# ---------------------------------------------------------------------------
# Player current-roster cache commands
# ---------------------------------------------------------------------------


@main.group("players")
def players() -> None:
    """Player cache and registry tools."""


def _format_player_session_scheduler_run_result(result) -> str:
    lines = [
        "Player session scheduler checked due jobs.",
        f"  Instance:       {result.instance}",
        f"  Checked at:     {result.checked_at}",
        f"  Checked:        {result.checked_count}",
        f"  Due:            {result.due_count}",
        f"  Enqueued:       {result.enqueued_count}",
        f"  Active:         {result.active_count}",
        f"  Failed:         {result.failed_count}",
        (
            "  Runner mode:    explicit --once only; no service, timer, "
            "or daemon is installed/enabled."
        ),
    ]
    for job in result.jobs:
        job_id = f" job=#{job.job_id}" if job.job_id is not None else ""
        lines.append(
            f"  - {job.job_kind}: {job.outcome}{job_id}; next due {job.next_due_at or 'unknown'}"
        )
    return "\n".join(lines)


@players.group("sessions")
def players_sessions() -> None:
    """Manage explicit player-session tools."""


@players_sessions.group("scheduler")
def players_sessions_scheduler() -> None:
    """Manage the opt-in player-session scheduler runner."""


@players_sessions_scheduler.command("run")
@click.option("--once", is_flag=True, help="Check due jobs once and exit.")
@click.option(
    "--data-root",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Optional armactl data root containing web.db.",
)
@click.pass_context
def players_sessions_scheduler_run(
    ctx: click.Context,
    once: bool,
    data_root: Path | None,
) -> None:
    """Run the explicit player-session scheduler once."""
    from armactl.web.runtime import web_db_file
    from armactl.web.services.player_session_scheduler_runner import (
        PlayerSessionSchedulerRunnerError,
        run_player_session_scheduler_once,
    )

    if not once:
        raise click.ClickException(
            "Only --once is supported; no scheduler service, timer, daemon, "
            "or background thread is installed or enabled."
        )

    root = data_root or paths.DEFAULT_DATA_ROOT
    try:
        result = run_player_session_scheduler_once(
            web_db_file(root),
            instance=ctx.obj["instance"],
        )
    except PlayerSessionSchedulerRunnerError as error:
        raise click.ClickException(str(error)) from error

    if ctx.obj["json"]:
        click.echo(json.dumps(result.to_dict(), indent=2))
    else:
        click.echo(_format_player_session_scheduler_run_result(result))
    if not result.success:
        sys.exit(result.exit_code)


@players.group("current-cache")
def players_current_cache() -> None:
    """Manage the automatic current-roster cache."""


@players_current_cache.command("run")
@click.option("--once", is_flag=True, help="Refresh once and exit.")
@click.option(
    "--interval-seconds",
    type=click.IntRange(10, 3600),
    default=60,
    show_default=True,
    help="Refresh interval for the updater loop.",
)
@click.option(
    "--data-root",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Optional armactl data root containing web.db.",
)
@click.pass_context
def players_current_cache_run(
    ctx: click.Context,
    once: bool,
    interval_seconds: int,
    data_root: Path | None,
) -> None:
    """Run the shared safe current-roster cache updater."""
    from armactl.player_current_cache_updater import (
        refresh_current_roster_cache_once,
        run_current_roster_cache_updater,
    )

    root = data_root or paths.DEFAULT_DATA_ROOT
    if once and ctx.obj["json"]:
        result = refresh_current_roster_cache_once(ctx.obj["instance"], data_root=root)
        click.echo(json.dumps(result.to_dict(), indent=2))
    else:
        result = run_current_roster_cache_updater(
            ctx.obj["instance"],
            once=once,
            interval_seconds=interval_seconds,
            data_root=root,
        )
    if result is not None and not result.success:
        sys.exit(result.exit_code or 1)

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
        try:
            state = discover_manual(
                install_dir=install_dir,
                config_path=config_path,
                instance=instance,
            )
        except paths.UnsafeServerInstallDirError as e:
            click.echo(f"[{instance}] Detection failed: {e}", err=True)
            sys.exit(1)
    else:
        click.echo(f"[{instance}] Running auto-detection...")
        state = discover(instance=instance)

    if state.server_installed:
        binary_path = Path(state.install_dir) / "ArmaReforgerServer"
        click.echo(f"  ✓ Server found at: {state.install_dir}")
        binary_status = "found" if state.binary_exists else "missing"
        click.echo(f"  ✓ Binary:  {binary_status} ({binary_path})")
        click.echo(
            f"  Config:  {'found' if state.config_exists else 'missing'} ({state.config_path})"
        )
        click.echo(f"  ✓ Service: {'found' if state.service_exists else 'missing'}")
        click.echo(f"  ✓ Timer:   {'found' if state.timer_exists else 'missing'}")
        icon = "🟢" if state.server_running else "🔴"
        click.echo(f"  ✓ Status:  {icon} {'running' if state.server_running else 'stopped'}")
        if state.ports.game:
            click.echo(
                f"  Ports:   game={state.ports.game} a2s={state.ports.a2s} rcon={state.ports.rcon}"
            )
        if state.migrated_from:
            click.echo(f"  ⚠ Detected from legacy paths (migrated_from={state.migrated_from})")
        click.echo(f"  State saved to: {paths.state_file(instance)}")
    else:
        if state.has_install_evidence():
            click.echo("  Incomplete or unverified server installation found.")
            click.echo(f"  Install dir: {state.install_dir}")
            click.echo(f"  Package integrity: {state.package_integrity or 'unknown'}")
            click.echo(
                f"  Config:  {'found' if state.config_exists else 'missing'} ({state.config_path})"
            )
            if state.package_missing_files:
                click.echo("  Missing package files: " + ", ".join(state.package_missing_files[:5]))
            click.echo("  Run 'armactl repair' to validate and complete the install.")
            return
        click.echo("  ✗ No server found.")
        click.echo("  Use 'armactl install' to install, or")
        click.echo(
            "  Use 'armactl detect --install-dir <path> --config-path <path>' for manual detection."
        )


@main.command()
@click.pass_context
def install(ctx: click.Context) -> None:
    """Install server from scratch."""
    from armactl.installer import InstallError, run_install

    instance = ctx.obj["instance"]

    click.echo(f"[{instance}] Starting installation...")
    try:
        for msg in run_install(instance=instance):
            click.echo(f"[{instance}] {msg}")
    except InstallError as e:
        click.echo(f"\n[{instance}] Installation failed: {e}", err=True)
        sys.exit(1)


@main.command()
@click.pass_context
def repair(ctx: click.Context) -> None:
    """Repair broken installation."""
    from armactl.repair import RepairError, run_repair

    instance = ctx.obj["instance"]
    state = _get_state(ctx)

    install_dir = state.install_dir or str(paths.server_dir(instance))
    config_path = state.config_path or str(paths.config_file(instance))

    try:
        for output in run_repair(instance, install_dir, config_path):
            click.echo(output)
    except RepairError as e:
        click.echo(f"[{instance}] Repair failed: {e}", err=True)
        sys.exit(1)


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
    from armactl.config_manager import ConfigError, load_config

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
    from armactl.config_manager import ConfigError
    from armactl.server_config_schema import (
        ServerConfigSchemaError,
        save_registered_config_value,
    )

    instance = ctx.obj["instance"]
    state = _get_state(ctx)

    if not state.config_exists:
        click.echo(f"[{instance}] Config not found.", err=True)
        sys.exit(1)

    try:
        save_registered_config_value(state.config_path, "name", name)
        click.echo(f"[{instance}] Server name set to '{name}'.")
    except (ConfigError, ServerConfigSchemaError) as e:
        click.echo(f"[{instance}] {e}", err=True)
        sys.exit(1)


@config.command("set-scenario")
@click.argument("scenario_id")
@click.pass_context
def config_set_scenario(ctx: click.Context, scenario_id: str) -> None:
    """Set scenario ID."""
    from armactl.config_manager import ConfigError
    from armactl.server_config_schema import (
        ServerConfigSchemaError,
        save_registered_config_value,
    )

    instance = ctx.obj["instance"]
    state = _get_state(ctx)

    if not state.config_exists:
        click.echo(f"[{instance}] Config not found.", err=True)
        sys.exit(1)

    try:
        save_registered_config_value(state.config_path, "scenario_id", scenario_id)
        click.echo(f"[{instance}] Scenario ID set to '{scenario_id}'.")
    except (ConfigError, ServerConfigSchemaError) as e:
        click.echo(f"[{instance}] {e}", err=True)
        sys.exit(1)


@config.command("set-maxplayers")
@click.argument("count", type=int)
@click.pass_context
def config_set_maxplayers(ctx: click.Context, count: int) -> None:
    """Set max players."""
    from armactl.config_manager import ConfigError
    from armactl.server_config_schema import (
        ServerConfigSchemaError,
        save_registered_config_value,
    )

    instance = ctx.obj["instance"]
    state = _get_state(ctx)

    if not state.config_exists:
        click.echo(f"[{instance}] Config not found.", err=True)
        sys.exit(1)

    try:
        save_registered_config_value(state.config_path, "max_players", count)
        click.echo(f"[{instance}] Max players set to {count}.")
    except (ConfigError, ServerConfigSchemaError) as e:
        click.echo(f"[{instance}] {e}", err=True)
        sys.exit(1)


@config.command("set-password-admin")
@click.argument("password")
@click.pass_context
def config_set_password_admin(ctx: click.Context, password: str) -> None:
    """Set admin password."""
    from armactl.config_manager import ConfigError
    from armactl.server_config_schema import (
        ServerConfigSchemaError,
        save_registered_config_value,
    )

    instance = ctx.obj["instance"]
    state = _get_state(ctx)

    if not state.config_exists:
        click.echo(f"[{instance}] Config not found.", err=True)
        sys.exit(1)

    try:
        save_registered_config_value(state.config_path, "password_admin", password)
        click.echo(f"[{instance}] Admin password updated.")
    except (ConfigError, ServerConfigSchemaError) as e:
        click.echo(f"[{instance}] {e}", err=True)
        sys.exit(1)


@config.command("set-rcon-password")
@click.argument("password")
@click.pass_context
def config_set_rcon_password(ctx: click.Context, password: str) -> None:
    """Set RCON password."""
    # RCON password uses dedicated server password game properties
    from armactl.config_manager import ConfigError
    from armactl.server_config_schema import (
        ServerConfigSchemaError,
        save_registered_config_value,
    )

    instance = ctx.obj["instance"]
    state = _get_state(ctx)

    if not state.config_exists:
        click.echo(f"[{instance}] Config not found.", err=True)
        sys.exit(1)

    try:
        save_registered_config_value(state.config_path, "rcon_password", password)
        click.echo(f"[{instance}] RCON password updated.")
    except (ConfigError, ServerConfigSchemaError) as e:
        click.echo(f"[{instance}] {e}", err=True)
        sys.exit(1)


@config.command("unset")
@click.argument("path")
@click.pass_context
def config_unset(ctx: click.Context, path: str) -> None:
    """Remove a config value. Example: config unset password"""
    from armactl.config_manager import ConfigError, unset_value

    instance = ctx.obj["instance"]
    state = _get_state(ctx)

    if not state.config_exists:
        click.echo(f"[{instance}] Config not found.", err=True)
        sys.exit(1)

    parts = path.split(".", 1)
    if len(parts) == 1:
        section = ""
        key = parts[0]
    else:
        section = parts[0]
        key = parts[1]

    try:
        unset_value(state.config_path, section, key)
        click.echo(f"[{instance}] Parameter '{path}' removed.")
    except ConfigError as e:
        click.echo(f"[{instance}] {e}", err=True)
        sys.exit(1)


@config.command("validate")
@click.pass_context
def config_validate(ctx: click.Context) -> None:
    """Validate configuration."""
    from armactl.config_manager import ConfigError, validate_config

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

    instance = ctx.obj["instance"]
    service_name = (
        f"armareforger@{instance}.service" if instance != "default" else paths.SERVICE_NAME
    )
    result = enable_service(service_name)
    click.echo(f"[{instance}] {result.message}")


@service.command("disable")
@click.pass_context
def service_disable_cmd(ctx: click.Context) -> None:
    """Disable systemd service."""
    from armactl.service_manager import disable_service

    instance = ctx.obj["instance"]
    service_name = (
        f"armareforger@{instance}.service" if instance != "default" else paths.SERVICE_NAME
    )
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

    instance = ctx.obj["instance"]
    timer_name = (
        f"armareforger-restart@{instance}.timer" if instance != "default" else paths.TIMER_NAME
    )
    result = enable_service(timer_name)
    click.echo(f"[{instance}] {result.message}")


@timer.command("disable")
@click.pass_context
def timer_disable_cmd(ctx: click.Context) -> None:
    """Disable systemd timer."""
    from armactl.service_manager import disable_service

    instance = ctx.obj["instance"]
    timer_name = (
        f"armareforger-restart@{instance}.timer" if instance != "default" else paths.TIMER_NAME
    )
    result = disable_service(timer_name)
    click.echo(f"[{instance}] {result.message}")


# ---------------------------------------------------------------------------
# Mods commands
# ---------------------------------------------------------------------------


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
    from armactl.service_manager import get_timer_status, timer_unit_name

    instance = ctx.obj["instance"]
    timer_name = timer_unit_name(instance)
    status = get_timer_status(timer_name)
    schedule = status.get("schedule") or "Unknown"
    click.echo(f"[{instance}] Schedule: {schedule}")


@schedule.command("set")
@click.argument("cron_expr")
@click.pass_context
def schedule_set(ctx: click.Context, cron_expr: str) -> None:
    """Set restart schedule (OnCalendar expression)."""
    from armactl.service_manager import (
        format_schedule_for_input,
        normalize_on_calendar_entries,
        update_restart_timer_schedule,
    )

    instance = ctx.obj["instance"]
    schedule_entries = normalize_on_calendar_entries(cron_expr)
    if not schedule_entries:
        click.echo(f"[{instance}] No valid restart time provided.")
        return

    display_value = format_schedule_for_input(schedule_entries)
    click.echo(f"[{instance}] Updating schedule to '{display_value}'...")
    results = update_restart_timer_schedule(instance=instance, on_calendar=schedule_entries)
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

    instance = ctx.obj["instance"]
    restart_service_name = (
        f"armareforger-restart@{instance}.service"
        if instance != "default"
        else paths.RESTART_SERVICE_NAME
    )

    click.echo(f"[{instance}] Triggering restart...")
    res = start_service(restart_service_name)
    click.echo(f"[{instance}] {res.message}")


# ---------------------------------------------------------------------------
# Mods commands
# ---------------------------------------------------------------------------


@main.group(invoke_without_command=True)
@click.pass_context
def mods(ctx: click.Context) -> None:
    """Manage server mods."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(mods_list, show_all=False)


@mods.command("list")
@click.option("--all", "show_all", is_flag=True, help="Show active and disabled mods.")
@click.pass_context
def mods_list(ctx: click.Context, show_all: bool) -> None:
    """List all installed mods."""
    from armactl.mods_manager import get_disabled_mods, get_mods

    instance = ctx.obj["instance"]
    state = _get_state(ctx)
    if not state.config_exists:
        click.echo(f"[{instance}] Config not found. Cannot read mods.", err=True)
        sys.exit(1)

    mods_arr = get_mods(state.config_path)
    disabled_mods = get_disabled_mods(state.config_path) if show_all else []

    if ctx.obj["json"]:
        if show_all:
            click.echo(json.dumps({"active": mods_arr, "disabled": disabled_mods}))
        else:
            click.echo(json.dumps(mods_arr))
        return

    if not mods_arr and not disabled_mods:
        click.echo(f"[{instance}] No mods configured.")
        return

    click.echo(f"[{instance}] Active mods ({len(mods_arr)}):")
    for idx, mod in enumerate(mods_arr, 1):
        mod_id = mod.get("modId", "UNKNOWN")
        name = mod.get("name", "Unnamed")
        ver = mod.get("version", "latest")
        click.echo(f"  {idx:2d}. {mod_id:<18} | {name:<30} | {ver}")

    if show_all:
        click.echo(f"[{instance}] Disabled mods ({len(disabled_mods)}):")
        for idx, mod in enumerate(disabled_mods, 1):
            mod_id = mod.get("modId", "UNKNOWN")
            name = mod.get("name", "Unnamed")
            ver = mod.get("version", "latest")
            click.echo(f"  {idx:2d}. {mod_id:<18} | {name:<30} | {ver}")


@mods.command("add")
@click.argument("mod_id")
@click.option("-n", "--name", default="", help="Human-readable name of the mod.")
@click.option("-v", "--version", default="", help="Specific version to load.")
@click.pass_context
def mods_add(ctx: click.Context, mod_id: str, name: str, version: str) -> None:
    """Add a mod by ID to the configuration."""
    from armactl.config_manager import ConfigError
    from armactl.mods_manager import add_mod

    instance = ctx.obj["instance"]
    state = _get_state(ctx)

    if not state.config_exists:
        click.echo(f"[{instance}] Config not found.", err=True)
        sys.exit(1)

    try:
        added = add_mod(state.config_path, mod_id, name, version)
    except ConfigError as e:
        click.echo(f"[{instance}] ✗ Failed to add mod: {e}", err=True)
        sys.exit(1)

    if added:
        click.echo(f"[{instance}] ✓ Mod {mod_id} ({name}) added.")
    else:
        click.echo(f"[{instance}] ! Mod {mod_id} is already in the list.")


@mods.command("remove")
@click.argument("mod_id")
@click.pass_context
def mods_remove(ctx: click.Context, mod_id: str) -> None:
    """Remove a mod by ID from the configuration."""
    from armactl.mods_manager import remove_mod_detailed

    instance = ctx.obj["instance"]
    state = _get_state(ctx)

    if not state.config_exists:
        click.echo(f"[{instance}] Config not found.", err=True)
        sys.exit(1)

    result = remove_mod_detailed(state.config_path, mod_id)
    if result.config_changed:
        cleanup = result.cleanup_result
        if result.enospc_retry_performed:
            click.echo(
                f"[{instance}] ! Disk was full; removed local files for deleted "
                "mod(s) and retried saving config."
            )
        if cleanup and cleanup.deleted:
            click.echo(
                f"[{instance}] ✓ Mod {mod_id} removed; deleted "
                f"{len(cleanup.deleted)} addon dir(s), freed {cleanup.freed_display}."
            )
        else:
            click.echo(f"[{instance}] ✓ Mod {mod_id} removed.")
        if cleanup and cleanup.errors:
            for error in cleanup.errors:
                click.echo(f"[{instance}] ! Addon cleanup warning: {error}", err=True)
    else:
        click.echo(f"[{instance}] ! Mod {mod_id} not found in the list.")


@mods.command("disable")
@click.argument("mod_id")
@click.pass_context
def mods_disable(ctx: click.Context, mod_id: str) -> None:
    """Disable a mod without deleting local addon files."""
    from armactl.mods_manager import disable_mod

    instance = ctx.obj["instance"]
    state = _get_state(ctx)

    if not state.config_exists:
        click.echo(f"[{instance}] Config not found.", err=True)
        sys.exit(1)

    if disable_mod(state.config_path, mod_id):
        click.echo(f"[{instance}] OK Mod {mod_id} disabled. Local addon files were kept.")
    else:
        click.echo(f"[{instance}] ! Active mod {mod_id} not found.")


@mods.command("enable")
@click.argument("mod_id")
@click.pass_context
def mods_enable(ctx: click.Context, mod_id: str) -> None:
    """Enable a previously disabled mod."""
    from armactl.mods_manager import enable_mod

    instance = ctx.obj["instance"]
    state = _get_state(ctx)

    if not state.config_exists:
        click.echo(f"[{instance}] Config not found.", err=True)
        sys.exit(1)

    if enable_mod(state.config_path, mod_id):
        click.echo(f"[{instance}] OK Mod {mod_id} enabled.")
    else:
        click.echo(f"[{instance}] ! Disabled mod {mod_id} not found.")


@mods.command("count")
@click.pass_context
def mods_count(ctx: click.Context) -> None:
    """Show the total number of installed mods."""
    from armactl.mods_manager import get_mods

    instance = ctx.obj["instance"]
    state = _get_state(ctx)

    if not state.config_exists:
        click.echo(f"[{instance}] Config not found.", err=True)
        sys.exit(1)

    mods_arr = get_mods(state.config_path)
    if ctx.obj["json"]:
        click.echo(json.dumps({"count": len(mods_arr)}))
    else:
        click.echo(len(mods_arr))


@mods.command("dedupe")
@click.pass_context
def mods_dedupe(ctx: click.Context) -> None:
    """Remove duplicate mods with the same ID."""
    from armactl.mods_manager import dedupe_mods

    instance = ctx.obj["instance"]
    state = _get_state(ctx)

    if not state.config_exists:
        click.echo(f"[{instance}] Config not found.", err=True)
        sys.exit(1)

    count = dedupe_mods(state.config_path)
    if count > 0:
        click.echo(f"[{instance}] ✓ Removed {count} duplicate mod(s).")
    else:
        click.echo(f"[{instance}] ✓ No duplicates found.")


@mods.command("export")
@click.argument("output_file", type=click.Path())
@click.pass_context
def mods_export(ctx: click.Context, output_file: str) -> None:
    """Export the list of mods to a JSON file."""
    from armactl.mods_manager import export_mods

    instance = ctx.obj["instance"]
    state = _get_state(ctx)

    if not state.config_exists:
        click.echo(f"[{instance}] Config not found.", err=True)
        sys.exit(1)

    count = export_mods(state.config_path, output_file)
    click.echo(f"[{instance}] ✓ Exported {count} mods to {output_file}.")


@mods.command("import")
@click.argument("input_file", type=click.Path(exists=True))
@click.option("--replace", is_flag=True, help="Replace existing mods instead of appending.")
@click.pass_context
def mods_import(ctx: click.Context, input_file: str, replace: bool) -> None:
    """Import a list of mods from a JSON file."""
    from armactl.config_manager import ConfigError
    from armactl.mods_manager import import_mods_detailed

    instance = ctx.obj["instance"]
    state = _get_state(ctx)

    if not state.config_exists:
        click.echo(f"[{instance}] Config not found.", err=True)
        sys.exit(1)

    try:
        added, skipped, update_result = import_mods_detailed(
            state.config_path,
            input_file,
            append=not replace,
        )
        click.echo(f"[{instance}] ✓ Imported mods from {input_file}.")
        click.echo(f"  Added: {added}, Skipped duplicates: {skipped}")
        cleanup = update_result.cleanup_result
        if update_result.enospc_retry_performed:
            click.echo(
                f"[{instance}] ! Disk was full; removed local files for deleted "
                "mod(s) and retried saving config."
            )
        if cleanup and cleanup.deleted:
            click.echo(
                f"[{instance}] ✓ Deleted {len(cleanup.deleted)} addon dir(s), "
                f"freed {cleanup.freed_display}."
            )
        if cleanup and cleanup.errors:
            for error in cleanup.errors:
                click.echo(f"[{instance}] ! Addon cleanup warning: {error}", err=True)
    except ConfigError as e:
        click.echo(f"[{instance}] ✗ Failed to import: {e}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Public statistics commands
# ---------------------------------------------------------------------------


@main.group()
def stats() -> None:
    """Show read-only public server statistics."""


@stats.command("public")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "discord", "json"]),
    default="text",
    show_default=True,
    help="Output format.",
)
@click.pass_context
def stats_public(ctx: click.Context, output_format: str) -> None:
    """Render read-only stats for public/community channels."""
    from armactl.public_stats import (
        load_public_stats,
        render_discord_stats_message,
        render_public_stats_json,
        render_public_stats_text,
    )

    snapshot = load_public_stats(ctx.obj["instance"])
    if ctx.obj["json"] or output_format == "json":
        click.echo(render_public_stats_json(snapshot))
    elif output_format == "discord":
        click.echo(render_discord_stats_message(snapshot))
    else:
        click.echo(render_public_stats_text(snapshot))


@stats.command("discord-message")
@click.pass_context
def stats_discord_message(ctx: click.Context) -> None:
    """Render a Discord-safe read-only server statistics message."""
    from armactl.public_stats import load_public_stats, render_discord_stats_message

    click.echo(render_discord_stats_message(load_public_stats(ctx.obj["instance"])))


@stats.group("discord")
def stats_discord() -> None:
    """Configure and run the read-only Discord statistics publisher."""


@stats_discord.command("configure")
@click.option(
    "--webhook-url",
    default="",
    help="Discord webhook URL. If omitted, the existing value is kept.",
)
@click.option(
    "--interval-seconds",
    type=int,
    default=None,
    help="Refresh interval for the publisher loop.",
)
@click.option(
    "--enabled/--disabled",
    default=None,
    help="Enable or disable Discord statistics publishing.",
)
@click.pass_context
def stats_discord_configure(
    ctx: click.Context,
    webhook_url: str,
    interval_seconds: int | None,
    enabled: bool | None,
) -> None:
    """Save read-only Discord statistics publisher settings."""
    from armactl.discord_stats import (
        DiscordStatsConfig,
        DiscordStatsConfigError,
        load_discord_stats_config,
        save_discord_stats_config,
    )

    current = load_discord_stats_config(ctx.obj["instance"])
    if enabled is True and not webhook_url.strip() and not current.webhook_url.strip():
        webhook_url = click.prompt("Discord webhook URL", hide_input=True)
    updated = DiscordStatsConfig(
        instance=current.instance,
        enabled=current.enabled if enabled is None else enabled,
        webhook_url=webhook_url.strip() or current.webhook_url,
        interval_seconds=interval_seconds or current.interval_seconds,
        message_id=current.message_id,
        env_path=current.env_path,
    )
    try:
        path = save_discord_stats_config(updated)
    except DiscordStatsConfigError as error:
        click.echo(f"Discord statistics config failed: {error}", err=True)
        sys.exit(1)

    click.echo("Discord statistics config saved.")
    click.echo(f"  Enabled:      {'yes' if updated.enabled else 'no'}")
    click.echo(f"  Webhook:      {updated.masked_webhook_url()}")
    click.echo(f"  Interval:     {updated.interval_seconds}s")
    click.echo(f"  Message ID:   {updated.message_id or 'not created yet'}")
    click.echo(f"  Config file:  {path}")


@stats_discord.command("status")
@click.pass_context
def stats_discord_status(ctx: click.Context) -> None:
    """Show read-only Discord statistics publisher settings."""
    from armactl.discord_stats import load_discord_stats_config

    config = load_discord_stats_config(ctx.obj["instance"])
    click.echo("Discord statistics publisher.")
    click.echo(f"  Enabled:      {'yes' if config.enabled else 'no'}")
    click.echo(f"  Webhook:      {config.masked_webhook_url()}")
    click.echo(f"  Interval:     {config.interval_seconds}s")
    click.echo(f"  Message ID:   {config.message_id or 'not created yet'}")
    click.echo(f"  Config file:  {config.env_path}")


@stats_discord.command("preview")
@click.pass_context
def stats_discord_preview(ctx: click.Context) -> None:
    """Render the Discord statistics message without sending it."""
    from armactl.public_stats import load_public_stats, render_discord_stats_message

    click.echo(render_discord_stats_message(load_public_stats(ctx.obj["instance"])))


@stats_discord.command("publish")
@click.pass_context
def stats_discord_publish(ctx: click.Context) -> None:
    """Create or update the configured Discord statistics message once."""
    from armactl.discord_stats import DiscordStatsPublishError, publish_discord_stats
    from armactl.redaction import redact_sensitive_text

    try:
        result = publish_discord_stats(ctx.obj["instance"])
    except DiscordStatsPublishError as error:
        click.echo(
            f"Discord statistics publish failed: {redact_sensitive_text(error)}",
            err=True,
        )
        sys.exit(1)
    click.echo(result.message)
    click.echo(f"  Message ID: {result.message_id}")


@stats_discord.command("run")
@click.option("--once", is_flag=True, help="Publish once and exit.")
@click.pass_context
def stats_discord_run(ctx: click.Context, once: bool) -> None:
    """Run the Discord statistics publisher loop."""
    from armactl.discord_stats import DiscordStatsPublishError, run_discord_stats_publisher
    from armactl.redaction import redact_sensitive_text

    try:
        result = run_discord_stats_publisher(ctx.obj["instance"], once=once)
    except DiscordStatsPublishError as error:
        click.echo(
            f"Discord statistics publisher failed: {redact_sensitive_text(error)}",
            err=True,
        )
        sys.exit(1)
    if result is not None and not result.success:
        sys.exit(result.exit_code or 1)

@stats_discord.group("service")
def stats_discord_service() -> None:
    """Manage the Discord statistics publisher systemd service."""


def _echo_discord_stats_service_result(action: str, result) -> None:
    marker = "✓" if result.success else "✗"
    click.echo(f"Discord statistics service {action}.")
    click.echo(f"  {marker} {result.message}")
    sys.exit(0 if result.success else result.exit_code or 1)


@stats_discord_service.command("install")
@click.pass_context
def stats_discord_service_install(ctx: click.Context) -> None:
    """Install and enable armactl-discord-stats.service."""
    from armactl.discord_stats import install_discord_stats_service

    results = install_discord_stats_service(ctx.obj["instance"])
    click.echo("Discord statistics service install.")
    failed = False
    for result in results:
        marker = "✓" if result.success else "✗"
        click.echo(f"  {marker} {result.message}")
        failed = failed or not result.success
    if failed:
        sys.exit(1)


@stats_discord_service.command("start")
def stats_discord_service_start() -> None:
    """Start armactl-discord-stats.service."""
    from armactl.discord_stats import start_discord_stats_service

    _echo_discord_stats_service_result("start", start_discord_stats_service())


@stats_discord_service.command("stop")
def stats_discord_service_stop() -> None:
    """Stop armactl-discord-stats.service."""
    from armactl.discord_stats import stop_discord_stats_service

    _echo_discord_stats_service_result("stop", stop_discord_stats_service())


@stats_discord_service.command("restart")
def stats_discord_service_restart() -> None:
    """Restart armactl-discord-stats.service."""
    from armactl.discord_stats import restart_discord_stats_service

    _echo_discord_stats_service_result("restart", restart_discord_stats_service())


@stats_discord_service.command("disable")
def stats_discord_service_disable() -> None:
    """Disable armactl-discord-stats.service."""
    from armactl.discord_stats import disable_discord_stats_service

    _echo_discord_stats_service_result("disable", disable_discord_stats_service())


@stats_discord_service.command("status")
def stats_discord_service_status() -> None:
    """Show armactl-discord-stats.service status."""
    from armactl.discord_stats import get_discord_stats_service_status

    status = get_discord_stats_service_status()
    runtime = status.get("runtime", {})
    click.echo("Discord statistics service status.")
    click.echo(f"  Service:        {status.get('service_name', 'armactl-discord-stats.service')}")
    click.echo(f"  Service file:   {status.get('service_file', '')}")
    click.echo(f"  Installed:      {'yes' if status.get('installed') else 'no'}")
    click.echo(f"  Active:         {'yes' if status.get('active') else 'no'}")
    click.echo(f"  Enabled:        {'yes' if status.get('enabled') else 'no'}")
    click.echo(f"  State:          {status.get('active_state', 'unknown')}")
    if status.get("main_pid"):
        click.echo(f"  PID:            {status.get('main_pid')}")
    runtime_marker = "✓" if isinstance(runtime, dict) and runtime.get("success") else "✗"
    runtime_message = runtime.get("message", "unknown") if isinstance(runtime, dict) else "unknown"
    click.echo(f"  Runtime check:  {runtime_marker} {runtime_message}")
