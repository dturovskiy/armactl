"""Synchronize supported mod admin ACLs from official ``game.admins``."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from armactl import admins_manager, sat_admin_guard
from armactl.config_manager import ConfigError

WCS_CONFIG_FILENAME = "WCS_Admin.json"
SAT_ROLE_KEYS = ("admins", "gameMasters")
WCS_ROLE_KEY = "gameMaster"
_LOCK_FILENAME = ".admin-acl-sync.lock"


class AdminAclSyncError(ConfigError):
    """Raised when supported admin ACLs cannot be synchronized safely."""

    def __init__(self, message: str, *, rollback_complete: bool = True) -> None:
        super().__init__(message)
        self.rollback_complete = rollback_complete


@dataclass(frozen=True)
class AdminAclSyncResult:
    """Counts-only synchronization result."""

    checked_configs: int
    changed_configs: int
    admin_count: int
    backup_count: int


@dataclass(frozen=True)
class AdminAclMutationResult:
    """Result of one official admin mutation plus supported ACL sync."""

    changed: bool
    created: bool | None
    sync: AdminAclSyncResult


@dataclass(frozen=True)
class _FileSnapshot:
    path: Path
    existed: bool
    content: bytes
    mode: int | None


@dataclass(frozen=True)
class _PreparedAcl:
    path: Path
    rendered: bytes


def _instance_root_for_config(config_path: Path) -> Path:
    if config_path.name != "config.json":
        raise AdminAclSyncError("Expected an instance config.json path.")
    if config_path.parent.name == "config":
        return config_path.parent.parent
    return config_path.parent


def _optional_profile_config(config_path: Path, filename: str) -> Path | None:
    candidates = (
        config_path.parent / "profile" / filename,
        config_path.parent / filename,
    )
    existing = tuple(path for path in candidates if path.is_file())
    if len(existing) > 1:
        raise AdminAclSyncError(
            "Multiple supported mod admin configs were found; synchronization was not run."
        )
    if existing:
        selected = existing[0]
        if selected.is_symlink() or selected.parent.is_symlink():
            raise AdminAclSyncError(
                "A supported mod admin config uses a symlink; synchronization was not run."
            )
        try:
            selected.resolve(strict=True).relative_to(
                config_path.parent.resolve(strict=True)
            )
        except (OSError, ValueError) as exc:
            raise AdminAclSyncError(
                "A supported mod admin config is outside the instance config boundary."
            ) from exc
    return existing[0] if existing else None


def sat_config_path_for_config(config_path: Path | str) -> Path | None:
    """Return the one existing SAT config supported for synchronization."""
    return _optional_profile_config(Path(config_path), sat_admin_guard.SAT_CONFIG_FILENAME)


def wcs_config_path_for_config(config_path: Path | str) -> Path | None:
    """Return the one existing WCS admin config supported for synchronization."""
    return _optional_profile_config(Path(config_path), WCS_CONFIG_FILENAME)


def _acl_paths(config_path: Path) -> tuple[Path, ...]:
    candidates = (
        sat_config_path_for_config(config_path),
        wcs_config_path_for_config(config_path),
    )
    return tuple(path for path in candidates if path is not None)


def _snapshot(path: Path) -> _FileSnapshot:
    if not path.exists():
        return _FileSnapshot(path=path, existed=False, content=b"", mode=None)
    try:
        stat = path.stat()
        return _FileSnapshot(
            path=path,
            existed=True,
            content=path.read_bytes(),
            mode=stat.st_mode & 0o777,
        )
    except OSError as exc:
        raise AdminAclSyncError("Admin permission files could not be read safely.") from exc


def _fsync_directory(path: Path) -> None:
    try:
        directory_fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    except OSError:
        pass
    finally:
        os.close(directory_fd)


def _atomic_write_bytes(path: Path, content: bytes, *, mode: int | None) -> None:
    temp_path: Path | None = None
    try:
        fd, raw_temp_path = tempfile.mkstemp(
            prefix=".armactl-admin-acl-",
            suffix=".tmp",
            dir=path.parent,
        )
        temp_path = Path(raw_temp_path)
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            temp_path.chmod(mode)
        os.replace(temp_path, path)
        _fsync_directory(path.parent)
    except OSError as exc:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise AdminAclSyncError("Admin permission files could not be published safely.") from exc


def _restore_snapshots(snapshots: tuple[_FileSnapshot, ...]) -> bool:
    restored = True
    for snapshot in reversed(snapshots):
        try:
            if snapshot.existed:
                _atomic_write_bytes(
                    snapshot.path,
                    snapshot.content,
                    mode=snapshot.mode,
                )
            else:
                snapshot.path.unlink(missing_ok=True)
                _fsync_directory(snapshot.path.parent)
        except Exception:  # noqa: BLE001 - report uncertain rollback to the operator.
            restored = False
    return restored


@contextmanager
def _admin_acl_lock(config_path: Path) -> Iterator[None]:
    lock_path = _instance_root_for_config(config_path) / _LOCK_FILENAME
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            lock_path.chmod(0o600)
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    except OSError as exc:
        raise AdminAclSyncError("Admin permission synchronization lock is unavailable.") from exc


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdminAclSyncError(
            "A supported mod admin config is invalid; synchronization was not run."
        ) from exc
    if not isinstance(payload, dict):
        raise AdminAclSyncError(
            "A supported mod admin config has an invalid root; synchronization was not run."
        )
    return payload


def _normalized_role_values(value: object, *, field_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        normalized: list[str] = []
        seen: set[str] = set()
        for identity, label in value.items():
            if not isinstance(identity, str) or not isinstance(label, str):
                raise AdminAclSyncError(
                    f"Supported mod admin field {field_name} must map strings to strings."
                )
            text = identity.strip()
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            normalized.append(text)
            seen.add(key)
        return normalized

    if not isinstance(value, list):
        raise AdminAclSyncError(
            f"Supported mod admin field {field_name} must be a list or object."
        )
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise AdminAclSyncError(
                f"Supported mod admin field {field_name} must contain only strings."
            )
        text = str(item or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        normalized.append(text)
        seen.add(key)
    return normalized


def _render_payload(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=4) + "\n").encode("utf-8")


def _role_style(payload: dict[str, Any], keys: tuple[str, ...]) -> str:
    styles = {
        "mapping" if isinstance(payload.get(key), dict) else "list"
        for key in keys
        if payload.get(key) is not None
        and isinstance(payload.get(key), (dict, list))
    }
    if len(styles) > 1:
        raise AdminAclSyncError(
            "Supported mod admin role fields use conflicting storage formats."
        )
    return next(iter(styles), "mapping")


def _desired_role_value(
    current: object,
    desired_entries: tuple[tuple[str, str], ...],
    *,
    style: str,
    field_name: str,
) -> object:
    _normalized_role_values(current, field_name=field_name)
    existing_labels = (
        {identity.casefold(): label for identity, label in current.items()}
        if isinstance(current, dict)
        else {}
    )
    if style == "mapping":
        return {
            identity: existing_labels.get(identity.casefold())
            or label
            or "armactl admin"
            for identity, label in desired_entries
        }
    return [identity for identity, _label in desired_entries]


def _prepare_acl_updates(config_path: Path) -> tuple[tuple[_PreparedAcl, ...], int]:
    sat_path = sat_config_path_for_config(config_path)
    wcs_path = wcs_config_path_for_config(config_path)
    if sat_path is None and wcs_path is None:
        return (), 0

    desired_entries, missing = sat_admin_guard.desired_sat_admin_entries(
        config_path,
        migrate=False,
    )
    if missing:
        raise AdminAclSyncError(
            "Some game admins have no reliable mod identity mapping; "
            "the admin change was not applied."
        )
    desired_values = [identity for identity, _label in desired_entries]
    updates: list[_PreparedAcl] = []

    if sat_path is not None:
        payload = _load_json_object(sat_path)
        style = _role_style(payload, SAT_ROLE_KEYS)
        changed = False
        for key in SAT_ROLE_KEYS:
            current = _normalized_role_values(payload.get(key), field_name=key)
            if current != desired_values:
                payload[key] = _desired_role_value(
                    payload.get(key),
                    desired_entries,
                    style=style,
                    field_name=key,
                )
                changed = True
        if changed:
            updates.append(
                _PreparedAcl(
                    path=sat_path,
                    rendered=_render_payload(payload),
                )
            )

    if wcs_path is not None:
        payload = _load_json_object(wcs_path)
        style = _role_style(payload, (WCS_ROLE_KEY,))
        current = _normalized_role_values(
            payload.get(WCS_ROLE_KEY),
            field_name=WCS_ROLE_KEY,
        )
        if current != desired_values:
            payload[WCS_ROLE_KEY] = _desired_role_value(
                payload.get(WCS_ROLE_KEY),
                desired_entries,
                style=style,
                field_name=WCS_ROLE_KEY,
            )
            updates.append(
                _PreparedAcl(
                    path=wcs_path,
                    rendered=_render_payload(payload),
                )
            )

    return tuple(updates), len(desired_values)


def _backup_directory(config_path: Path) -> Path:
    return _instance_root_for_config(config_path) / "backups" / "admin-permissions"


def _rotate_acl_backups(backup_dir: Path, *, max_backups: int = 50) -> None:
    try:
        backups = sorted(backup_dir.glob("*.bak"), key=lambda path: path.stat().st_mtime)
    except OSError:
        return
    for old_backup in backups[:-max_backups]:
        try:
            old_backup.unlink()
        except OSError:
            pass


def _backup_changed_acls(
    config_path: Path,
    updates: tuple[_PreparedAcl, ...],
) -> int:
    if not updates:
        return 0
    backup_dir = _backup_directory(config_path)
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_dir.chmod(0o700)
        _rotate_acl_backups(backup_dir, max_backups=48)
        timestamp = time.time_ns()
        for index, update in enumerate(updates, 1):
            backup_path = backup_dir / f"{update.path.name}.{timestamp}.{index}.bak"
            shutil.copy2(update.path, backup_path)
            backup_path.chmod(0o600)
        _rotate_acl_backups(backup_dir)
        _fsync_directory(backup_dir)
    except OSError as exc:
        raise AdminAclSyncError(
            "Admin permission backups could not be created; synchronization was not run."
        ) from exc
    return len(updates)


def _sync_admin_acls_unlocked(config_path: Path) -> AdminAclSyncResult:
    acl_paths = _acl_paths(config_path)
    updates, admin_count = _prepare_acl_updates(config_path)
    snapshots = tuple(_snapshot(path) for path in acl_paths)
    try:
        backup_count = _backup_changed_acls(config_path, updates)
        modes = {snapshot.path: snapshot.mode for snapshot in snapshots}
        for update in updates:
            _atomic_write_bytes(
                update.path,
                update.rendered,
                mode=modes.get(update.path),
            )
    except Exception as exc:
        rollback_complete = _restore_snapshots(snapshots)
        if isinstance(exc, AdminAclSyncError):
            raise AdminAclSyncError(
                str(exc),
                rollback_complete=rollback_complete,
            ) from exc
        raise AdminAclSyncError(
            "Admin permission synchronization failed.",
            rollback_complete=rollback_complete,
        ) from exc

    return AdminAclSyncResult(
        checked_configs=len(acl_paths),
        changed_configs=len(updates),
        admin_count=admin_count,
        backup_count=backup_count,
    )


def sync_admin_acls(config_path: Path | str) -> AdminAclSyncResult:
    """Synchronize existing supported mod ACLs from official ``game.admins``."""
    path = Path(config_path)
    with _admin_acl_lock(path):
        return _sync_admin_acls_unlocked(path)


def _mutation_paths(config_path: Path) -> tuple[Path, ...]:
    return (
        config_path,
        admins_manager.admins_state_path_for_config(config_path),
        *_acl_paths(config_path),
    )


def _run_mutation(
    config_path: Path,
    mutation: Callable[[], bool],
    *,
    is_remove: bool,
) -> AdminAclMutationResult:
    with _admin_acl_lock(config_path):
        snapshots = tuple(_snapshot(path) for path in _mutation_paths(config_path))
        try:
            backend_result = mutation()
        except Exception as exc:
            rollback_complete = _restore_snapshots(snapshots)
            if not rollback_complete:
                raise AdminAclSyncError(
                    "Admin change failed and rollback could not be completed; "
                    "inspect admin permissions before restarting.",
                    rollback_complete=False,
                ) from exc
            raise

        if is_remove and not backend_result:
            return AdminAclMutationResult(
                changed=False,
                created=None,
                sync=AdminAclSyncResult(0, 0, 0, 0),
            )

        try:
            sync_result = _sync_admin_acls_unlocked(config_path)
        except Exception as exc:
            rollback_complete = _restore_snapshots(snapshots)
            message = (
                "Admin change failed and rollback could not be completed; "
                "inspect admin permissions before restarting."
                if not rollback_complete
                else "Admin change was rolled back because supported mod permissions "
                "could not be synchronized."
            )
            raise AdminAclSyncError(
                message,
                rollback_complete=rollback_complete,
            ) from exc

    return AdminAclMutationResult(
        changed=True,
        created=None if is_remove else backend_result,
        sync=sync_result,
    )


def add_admin_and_sync(
    config_path: Path | str,
    admin_reference: str,
    name: str = "",
) -> AdminAclMutationResult:
    """Add/update an official admin and synchronize supported mod ACLs."""
    path = Path(config_path)
    return _run_mutation(
        path,
        lambda: admins_manager.add_admin(path, admin_reference, name),
        is_remove=False,
    )


def remove_admin_and_sync(
    config_path: Path | str,
    admin_reference: str,
) -> AdminAclMutationResult:
    """Remove an official admin and synchronize supported mod ACLs."""
    path = Path(config_path)
    return _run_mutation(
        path,
        lambda: admins_manager.remove_admin(path, admin_reference),
        is_remove=True,
    )
