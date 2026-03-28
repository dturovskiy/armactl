"""Repair mode - fix broken or incomplete installations."""

from __future__ import annotations

import secrets
import subprocess
from collections.abc import Iterator
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from armactl.discovery import discover_manual
from armactl.i18n import _, tr
from armactl.paths import start_script
from armactl.service_manager import (
    generate_services,
    install_privileged_systemctl_channel,
    stop_service,
)


class RepairError(Exception):
    """Raised when repair fails fatally."""


def run_repair(
    instance: str,
    install_dir: Path | str,
    config_path: Path | str,
) -> Iterator[str]:
    """Generator that repairs an existing server installation."""
    yield tr("[{instance}] Diagnosing existing installation...", instance=instance)

    install_dir = Path(install_dir)
    config_path = Path(config_path)

    if not install_dir.exists():
        yield tr(
            "  ! Critical: Install directory {path} is completely missing!",
            path=install_dir,
        )
        raise RepairError(
            _("Will not repair missing base directory. Try running 'install' instead.")
        )

    yield tr("[{instance}] Step 1: Stopping services...", instance=instance)
    state = discover_manual(install_dir, config_path, instance=instance, save=False)
    if state.server_running:
        stop_service(state.service_name)
        yield _("  OK Server stopped")
    else:
        yield _("  - Server is already stopped")

    yield tr("[{instance}] Step 2: Validating game files via SteamCMD...", instance=instance)
    cmd = [
        "steamcmd",
        "+force_install_dir",
        str(install_dir.absolute()),
        "+login",
        "anonymous",
        "+app_update",
        "1874900",
        "validate",
        "+quit",
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        yield _("  OK Server files validated and updated")
    except subprocess.CalledProcessError as e:
        raise RepairError(
            tr("SteamCMD failed:\n{details}", details=e.stderr.strip() or e.stdout.strip())
        ) from e
    except FileNotFoundError:
        raise RepairError(_("SteamCMD not found in PATH. Make sure it is installed."))

    yield tr("[{instance}] Step 3: Checking configuration...", instance=instance)
    if not config_path.exists():
        yield _("  ! Config missing! Regenerating default config...")
        project_root = Path(__file__).resolve().parent.parent.parent
        templates_dir = project_root / "templates"

        config_path.parent.mkdir(parents=True, exist_ok=True)
        env = Environment(loader=FileSystemLoader(str(templates_dir)))
        template = env.get_template("config.json.j2")
        config_render = template.render(
            rcon_password=secrets.token_urlsafe(8),
            password_admin=secrets.token_urlsafe(8),
        )
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(config_render)
        yield _("  OK Default config generated")
    else:
        yield _("  OK Config exists (skipping overwrite)")

    yield tr("[{instance}] Step 4: Repairing systemd services...", instance=instance)
    results = generate_services(instance=instance)
    for result in results:
        if result.success:
            yield tr("  OK {message}", message=result.message)
        else:
            raise RepairError(
                tr("Failed to generate systemd units: {message}", message=result.message)
            )

    yield tr("[{instance}] Step 5: Installing secure privileged control...", instance=instance)
    privileged_results = install_privileged_systemctl_channel()
    for result in privileged_results:
        if result.success:
            yield tr("  OK {message}", message=result.message)
        else:
            raise RepairError(
                tr(
                    "Failed to install secure privileged control: {message}",
                    message=result.message,
                )
            )

    yield tr("[{instance}] Step 6: Fixing permissions...", instance=instance)
    script_path = start_script(instance)
    if script_path.exists():
        script_path.chmod(0o755)
        yield _("  OK Start script permissions fixed")
    else:
        yield _("  ! Start script still missing?")

    yield tr("[{instance}] Step 7: Updating server state...", instance=instance)
    discover_manual(install_dir, config_path, instance=instance, save=True)
    yield _("  OK Server state synchronized")

    yield tr(
        "[{instance}] Repair complete! Run 'start' to boot the server.",
        instance=instance,
    )
