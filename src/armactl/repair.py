"""Repair mode — fix broken or incomplete installations."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

from armactl.discovery import discover_manual
from armactl.service_manager import generate_services, stop_service
from armactl.state import ServerState


class RepairError(Exception):
    """Raised when repair fails fatally."""
    pass


def run_repair(instance: str, install_dir: Path | str, config_path: Path | str) -> Iterator[str]:
    """Generator that repairs an existing server installation."""
    yield f"[{instance}] Diagnosing existing installation..."
    
    install_dir = Path(install_dir)
    config_path = Path(config_path)

    if not install_dir.exists():
        yield f"  ! Critical: Install directory {install_dir} is completely missing!"
        raise RepairError("Will not repair missing base directory. Try running 'install' instead.")

    # 1. Stop existing service
    yield f"[{instance}] Step 1: Stopping services..."
    state = discover_manual(install_dir, config_path, instance=instance, save=False)
    if state.server_running:
        stop_service(state.service_name)
        yield "  ✓ Server stopped"
    else:
        yield "  - Server is already stopped"

    # 2. Re-install / Update / Validate SteamCMD game files
    yield f"[{instance}] Step 2: Validating game files via SteamCMD..."
    cmd = [
        "steamcmd",
        "+force_install_dir", str(install_dir.absolute()),
        "+login", "anonymous",
        "+app_update", "1874900", "validate",
        "+quit",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        yield "  ✓ Server files validated and updated"
    except subprocess.CalledProcessError as e:
        raise RepairError(f"SteamCMD failed:\n{(e.stderr.strip() or e.stdout.strip())}")
    except FileNotFoundError:
        raise RepairError("SteamCMD not found in PATH. Make sure it is installed.")

    # 3. Regenerate Config (ONLY if completely missing)
    yield f"[{instance}] Step 3: Checking configuration..."
    if not config_path.exists():
        yield "  ! Config missing! Regenerating default config..."
        from jinja2 import Environment, FileSystemLoader
        import secrets
        
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
        yield "  ✓ Default config generated"
    else:
        yield "  ✓ Config exists (skipping overwrite)"

    # 4. Regenerate Systemd files
    yield f"[{instance}] Step 4: Repairing systemd services..."
    res = generate_services(
        instance_name=instance,
        install_dir=install_dir,
        config_path=config_path,
        schedule="*-*-* 06:00:00",  # Always restore default schedule on repair if we need to regenerate
        auto_start=True
    )
    if res.success:
        yield f"  ✓ Systemd files regenerated"
    else:
        raise RepairError(f"Failed to generate systemd units: {res.message}")

    # 5. Fix permissions (make script executable)
    yield f"[{instance}] Step 5: Fixing permissions..."
    from armactl.paths import start_script_path
    script_path = start_script_path(instance)
    if script_path.exists():
        script_path.chmod(0o755)
        yield "  ✓ Start script permissions fixed"
    else:
        yield "  ! Start script still missing?"

    # 6. Re-detect state
    yield f"[{instance}] Step 6: Updating server state..."
    final_state = discover_manual(install_dir, config_path, instance=instance, save=True)
    yield "  ✓ Server state synchronized"

    yield f"[{instance}] Repair complete! Run 'start' to boot the server."
