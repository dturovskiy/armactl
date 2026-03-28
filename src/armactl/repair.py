"""Repair mode - fix broken or incomplete installations."""

from __future__ import annotations

import secrets
import subprocess
from collections.abc import Iterator
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from armactl.discovery import discover_manual
from armactl.paths import start_script
from armactl.service_manager import generate_services, stop_service


class RepairError(Exception):
    """Raised when repair fails fatally."""


def run_repair(
    instance: str,
    install_dir: Path | str,
    config_path: Path | str,
) -> Iterator[str]:
    """Generator that repairs an existing server installation."""
    yield f"[{instance}] Diagnosing existing installation..."

    install_dir = Path(install_dir)
    config_path = Path(config_path)

    if not install_dir.exists():
        yield f"  ! Critical: Install directory {install_dir} is completely missing!"
        raise RepairError(
            "Will not repair missing base directory. Try running 'install' instead."
        )

    yield f"[{instance}] Step 1: Stopping services..."
    state = discover_manual(install_dir, config_path, instance=instance, save=False)
    if state.server_running:
        stop_service(state.service_name)
        yield "  OK Server stopped"
    else:
        yield "  - Server is already stopped"

    yield f"[{instance}] Step 2: Validating game files via SteamCMD..."
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
        yield "  OK Server files validated and updated"
    except subprocess.CalledProcessError as e:
        raise RepairError(
            f"SteamCMD failed:\n{e.stderr.strip() or e.stdout.strip()}"
        ) from e
    except FileNotFoundError:
        raise RepairError("SteamCMD not found in PATH. Make sure it is installed.")

    yield f"[{instance}] Step 3: Checking configuration..."
    if not config_path.exists():
        yield "  ! Config missing! Regenerating default config..."
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
        yield "  OK Default config generated"
    else:
        yield "  OK Config exists (skipping overwrite)"

    yield f"[{instance}] Step 4: Repairing systemd services..."
    results = generate_services(instance=instance)
    for result in results:
        if result.success:
            yield f"  OK {result.message}"
        else:
            raise RepairError(f"Failed to generate systemd units: {result.message}")

    yield f"[{instance}] Step 5: Fixing permissions..."
    script_path = start_script(instance)
    if script_path.exists():
        script_path.chmod(0o755)
        yield "  OK Start script permissions fixed"
    else:
        yield "  ! Start script still missing?"

    yield f"[{instance}] Step 6: Updating server state..."
    discover_manual(install_dir, config_path, instance=instance, save=True)
    yield "  OK Server state synchronized"

    yield f"[{instance}] Repair complete! Run 'start' to boot the server."
