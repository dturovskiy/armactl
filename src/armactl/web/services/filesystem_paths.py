"""Relative path validation and jail checks for the web file browser."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from armactl import paths
from armactl.web.services.filesystem_errors import (
    RootUnavailableError,
    UnsafeFilePathError,
)

if TYPE_CHECKING:
    from armactl.web.services.filesystem_roots import FileRoot

FORBIDDEN_PATH_NAMES = frozenset({".git", ".venv"})
SYSTEM_PREFIXES = (
    Path("/etc"),
    Path("/root"),
    Path("/usr"),
    Path("/var"),
    paths.SYSTEMD_DIR,
    paths.SUDOERS_DIR,
)


@dataclass(frozen=True)
class ResolvedBrowserPath:
    """Resolved path after root and relative-path validation."""

    root: FileRoot
    requested_path: Path
    resolved_path: Path
    relative_path: str


def is_inside_or_equal(child: Path, parent: Path) -> bool:
    """Return whether child is parent or contained by parent."""
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def resolved_path(path: Path) -> Path:
    """Resolve a path without requiring the final target to exist."""
    return path.expanduser().resolve(strict=False)


def has_forbidden_part(path: Path) -> bool:
    """Return whether a path contains a browser-denied path segment."""
    return any(part in FORBIDDEN_PATH_NAMES for part in path.parts)


def is_system_path(path: Path) -> bool:
    """Return whether a path is inside a system location denied to the browser."""
    return any(
        is_inside_or_equal(path, resolved_path(prefix)) for prefix in SYSTEM_PREFIXES
    )


def is_source_tree_path(path: Path) -> bool:
    """Return whether a path is inside or wraps the current source tree."""
    source_root = resolved_path(paths.project_root())
    return is_inside_or_equal(path, source_root) or is_inside_or_equal(source_root, path)


def normalize_relative_parts(relative_path: object | None) -> tuple[str, ...]:
    """Normalize one browser relative path into safe POSIX path parts."""
    if relative_path in (None, "", "."):
        return ()
    if not isinstance(relative_path, str):
        raise UnsafeFilePathError(UnsafeFilePathError.public_message)
    raw = relative_path
    if "\x00" in raw or "\\" in raw:
        raise UnsafeFilePathError(UnsafeFilePathError.public_message)
    pure = PurePosixPath(raw)
    if pure.is_absolute():
        raise UnsafeFilePathError(UnsafeFilePathError.public_message)
    parts = tuple(part for part in pure.parts if part not in ("", "."))
    if any(part == ".." or part in FORBIDDEN_PATH_NAMES for part in parts):
        raise UnsafeFilePathError(UnsafeFilePathError.public_message)
    return parts


def relative_path_from_parts(parts: tuple[str, ...]) -> str:
    """Return the URL/query relative path representation for safe parts."""
    return "/".join(parts)


def parent_relative_path(relative_path: str) -> str:
    """Return the parent browser path for a normalized relative path."""
    parts = tuple(part for part in PurePosixPath(relative_path).parts if part not in ("", "."))
    if len(parts) <= 1:
        return ""
    return "/".join(parts[:-1])


def child_relative_path(parent_relative_path: str, filename: str) -> str:
    """Return a child browser path under a normalized parent path."""
    if not parent_relative_path:
        return filename
    return f"{parent_relative_path}/{filename}"


def resolve_browser_path(
    data_root: Path | None,
    root_id: str,
    relative_path: object | None = "",
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> ResolvedBrowserPath:
    """Resolve and validate a browser relative path under a fixed root."""
    from armactl.web.services.filesystem_roots import get_file_root

    root = get_file_root(data_root, root_id, instance=instance)
    if not root.available:
        raise RootUnavailableError(root.reason or RootUnavailableError.public_message)

    parts = normalize_relative_parts(relative_path)
    requested = root.path.joinpath(*parts)
    resolved_root = resolved_path(root.path)
    resolved_target = resolved_path(requested)
    if not is_inside_or_equal(resolved_target, resolved_root):
        raise UnsafeFilePathError(UnsafeFilePathError.public_message)
    if has_forbidden_part(resolved_target) or is_system_path(resolved_target):
        raise UnsafeFilePathError(UnsafeFilePathError.public_message)
    if is_source_tree_path(resolved_target):
        raise UnsafeFilePathError(UnsafeFilePathError.public_message)

    return ResolvedBrowserPath(
        root=root,
        requested_path=requested,
        resolved_path=resolved_target,
        relative_path=relative_path_from_parts(parts),
    )
