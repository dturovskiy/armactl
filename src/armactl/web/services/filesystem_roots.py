"""Allowlisted file-browser roots and root availability checks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from armactl import paths
from armactl.web.services.filesystem_errors import UnknownFileRootError
from armactl.web.services.filesystem_paths import (
    has_forbidden_part,
    is_inside_or_equal,
    is_source_tree_path,
    is_system_path,
    resolved_path,
)

UPLOAD_ROOT_IDS = frozenset({"server"})


@dataclass(frozen=True)
class FileRoot:
    """One fixed root exposed by the web file browser."""

    root_id: str
    label: str
    path: Path
    exists: bool
    available: bool
    reason: str = ""


@dataclass(frozen=True)
class RootDefinition:
    """Static root definition used to build the allowlist."""

    root_id: str
    label: str
    path_builder: Callable[[str, Path], Path]


ROOT_DEFINITIONS: tuple[RootDefinition, ...] = (
    RootDefinition("server", "Server files", paths.server_dir),
    RootDefinition("config", "Config files", paths.config_dir),
    RootDefinition("backups", "Backups", paths.backups_dir),
    RootDefinition("logs", "Logs", paths.logs_dir),
)


def data_root_or_default(data_root: Path | None) -> Path:
    """Return the configured armactl data root or the default."""
    return Path(data_root) if data_root is not None else paths.DEFAULT_DATA_ROOT


def _safe_root_state(root_path: Path, data_root: Path) -> tuple[bool, bool, str]:
    resolved_root = resolved_path(root_path)
    resolved_data_root = resolved_path(data_root)
    exists = root_path.exists()
    if not is_inside_or_equal(resolved_root, resolved_data_root):
        return exists, False, "Root unavailable."
    if has_forbidden_part(resolved_root) or is_system_path(resolved_root):
        return exists, False, "Root unavailable."
    if is_source_tree_path(resolved_root):
        return exists, False, "Root unavailable."
    if not exists or not root_path.is_dir():
        return exists, False, "Root unavailable."
    return exists, True, ""


def list_allowed_roots(
    data_root: Path | None,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> tuple[FileRoot, ...]:
    """Return the fixed, safe roots exposed by the file browser."""
    root_data = data_root_or_default(data_root)
    roots: list[FileRoot] = []
    for definition in ROOT_DEFINITIONS:
        root_path = definition.path_builder(instance, root_data)
        exists, available, reason = _safe_root_state(root_path, root_data)
        roots.append(
            FileRoot(
                root_id=definition.root_id,
                label=definition.label,
                path=root_path,
                exists=exists,
                available=available,
                reason=reason,
            )
        )
    return tuple(roots)


def get_file_root(
    data_root: Path | None,
    root_id: str,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> FileRoot:
    """Return one allowlisted root or raise a controlled error."""
    normalized = str(root_id or "").strip().lower()
    for root in list_allowed_roots(data_root, instance=instance):
        if root.root_id == normalized:
            return root
    raise UnknownFileRootError(UnknownFileRootError.public_message)


def root_allows_upload(root: FileRoot | str) -> bool:
    """Return whether a fixed root currently accepts new file uploads."""
    root_id = root.root_id if isinstance(root, FileRoot) else str(root or "")
    return root_id.strip().lower() in UPLOAD_ROOT_IDS
