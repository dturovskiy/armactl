"""Repair mode - fix broken or incomplete installations."""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from armactl import paths
from armactl.admins_manager import migrate_legacy_admins
from armactl.config_manager import ConfigError, validate_config
from armactl.discovery import discover_manual
from armactl.i18n import _, tr
from armactl.installer import InstallError, stream_server_update
from armactl.integrity import (
    IntegrityError,
    check_package_integrity,
    clear_install_marker,
    mark_install_started,
    write_package_manifest,
)
from armactl.mods_state import migrate_legacy_disabled_mods
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

    install_dir = Path(install_dir) if str(install_dir).strip() else paths.server_dir(instance)
    config_path = Path(config_path) if str(config_path).strip() else paths.config_file(instance)
    try:
        install_dir = paths.validate_server_install_dir(install_dir, instance=instance)
    except paths.UnsafeServerInstallDirError as e:
        raise RepairError(str(e)) from e

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
    previous_integrity = check_package_integrity(install_dir)
    mark_install_started(install_dir)
    try:
        yield from stream_server_update(install_dir, instance=instance)
        write_package_manifest(install_dir)
        clear_install_marker(install_dir)
        integrity = check_package_integrity(install_dir, verify_hashes=False)
        if not integrity.complete:
            raise RepairError(
                tr(
                    "Package integrity check failed after SteamCMD validate: {details}",
                    details=integrity.summary(),
                )
            )
        yield _("  OK Server files validated and updated")
        yield _("  OK Package integrity manifest refreshed")
    except InstallError as e:
        if previous_integrity.complete:
            clear_install_marker(install_dir)
        raise RepairError(str(e)) from e
    except (IntegrityError, OSError) as e:
        if previous_integrity.complete:
            clear_install_marker(install_dir)
        raise RepairError(
            tr("Failed to record package integrity manifest: {error}", error=e)
        ) from e

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

    try:
        migration = migrate_legacy_disabled_mods(config_path)
    except ConfigError as e:
        raise RepairError(tr("Config migration failed: {error}", error=e)) from e
    if migration.migrated:
        yield tr(
            "  OK Migrated {count} disabled mod(s) to {path}",
            count=migration.legacy_entries,
            path=migration.sidecar_path,
        )
        yield _("  OK Removed unsupported game.disabledMods from server config")

    try:
        admins_migration = migrate_legacy_admins(config_path)
    except ConfigError as e:
        raise RepairError(tr("Admin operator migration failed: {error}", error=e)) from e
    if admins_migration.migrated:
        yield tr(
            "  OK Normalized {count} server admin entry/entries and restored "
            "{restored} ID(s) from {path}",
            count=admins_migration.legacy_entries,
            restored=admins_migration.restored_from_sidecar,
            path=admins_migration.sidecar_path,
        )
        yield _("  OK Official game.admins server ACL synchronized")

    config_errors = validate_config(config_path=config_path)
    if config_errors:
        for error in config_errors:
            yield tr("  ! Config validation: {error}", error=error)
        raise RepairError(
            _(
                "Configuration validation failed. Fix config.json before "
                "starting the server."
            )
        )
    yield _("  OK Config validation passed")

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
    script_path = paths.start_script(instance)
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
