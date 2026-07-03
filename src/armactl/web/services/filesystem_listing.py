"""Directory listing DTOs and metadata for the web file browser."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from armactl import paths as armactl_paths
from armactl.web.services.file_replacements import is_replacement_candidate
from armactl.web.services.filesystem_errors import PathUnavailableError
from armactl.web.services.filesystem_paths import (
    FORBIDDEN_PATH_NAMES,
    has_forbidden_part,
    is_inside_or_equal,
    is_source_tree_path,
    is_system_path,
    parent_relative_path,
    resolve_browser_path,
    resolved_path,
)
from armactl.web.services.filesystem_roots import FileRoot
from armactl.web.services.filesystem_urls import (
    download_href,
    files_href,
    preview_href,
    replace_href,
)

PREVIEW_TEXT_SUFFIXES = frozenset(
    {
        ".bat",
        ".cfg",
        ".conf",
        ".csv",
        ".env",
        ".ini",
        ".json",
        ".log",
        ".md",
        ".properties",
        ".rpt",
        ".sh",
        ".txt",
        ".xml",
        ".yaml",
        ".yml",
    }
)
PREVIEW_TEXT_FILENAMES = frozenset(
    {
        "changelog",
        "license",
        "notice",
        "readme",
        "version",
    }
)


@dataclass(frozen=True)
class Breadcrumb:
    """Breadcrumb item for a relative browser path."""

    label: str
    relative_path: str
    href: str
    is_current: bool = False


@dataclass(frozen=True)
class FileMetadata:
    """Safe metadata for a filesystem entry."""

    name: str
    relative_path: str
    entry_type: str
    is_dir: bool
    is_file: bool
    size: int
    size_text: str
    modified_at: str
    href: str
    preview_href: str = ""
    download_href: str = ""
    replace_href: str = ""


@dataclass(frozen=True)
class DirectoryListing:
    """Directory entries under one safe root."""

    root: FileRoot
    relative_path: str
    breadcrumbs: tuple[Breadcrumb, ...]
    parent_href: str
    entries: tuple[FileMetadata, ...]


def format_size(size: int) -> str:
    """Return a compact binary-size label."""
    if size < 1024:
        return f"{size} B"
    value = float(size)
    for unit in ("KiB", "MiB", "GiB"):
        value /= 1024
        if value < 1024:
            return f"{value:.1f} {unit}"
    return f"{value:.1f} TiB"


def format_modified(timestamp: float) -> str:
    """Return a UTC ISO timestamp suitable for browser display."""
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds")


def is_preview_candidate(path: Path) -> bool:
    """Return whether a filename should expose a preview link."""
    lower_name = path.name.casefold()
    if lower_name in PREVIEW_TEXT_FILENAMES:
        return True
    suffixes = {suffix.casefold() for suffix in path.suffixes}
    if suffixes & PREVIEW_TEXT_SUFFIXES:
        return True
    return False


def _metadata_from_path(root: FileRoot, path: Path, relative_path: str) -> FileMetadata:
    try:
        stat_result = path.stat()
        is_dir = path.is_dir()
        is_file = path.is_file()
    except OSError as exc:
        raise PathUnavailableError(PathUnavailableError.public_message) from exc

    entry_type = "Directory" if is_dir else "File"
    return FileMetadata(
        name=path.name or root.label,
        relative_path=relative_path,
        entry_type=entry_type,
        is_dir=is_dir,
        is_file=is_file,
        size=stat_result.st_size if is_file else 0,
        size_text=format_size(stat_result.st_size) if is_file else "",
        modified_at=format_modified(stat_result.st_mtime),
        href=files_href(root.root_id, relative_path) if is_dir else "",
        preview_href=(
            preview_href(root.root_id, relative_path)
            if is_file and is_preview_candidate(path)
            else ""
        ),
        download_href=download_href(root.root_id, relative_path) if is_file else "",
        replace_href=(
            replace_href(root.root_id, relative_path)
            if is_file and is_replacement_candidate(root, relative_path, path)
            else ""
        ),
    )


def _entry_metadata(root: FileRoot, base_relative: str, entry: Path) -> FileMetadata | None:
    if entry.name in FORBIDDEN_PATH_NAMES:
        return None
    relative_path = f"{base_relative}/{entry.name}" if base_relative else entry.name
    try:
        resolved_entry = resolved_path(entry)
        resolved_root = resolved_path(root.path)
    except OSError:
        return None
    if not is_inside_or_equal(resolved_entry, resolved_root):
        return None
    if has_forbidden_part(resolved_entry) or is_system_path(resolved_entry):
        return None
    if is_source_tree_path(resolved_entry):
        return None
    try:
        return _metadata_from_path(root, entry, relative_path)
    except PathUnavailableError:
        return None


def _breadcrumbs(root_id: str, relative_path: str) -> tuple[Breadcrumb, ...]:
    if not relative_path:
        return (Breadcrumb("Root", "", files_href(root_id), is_current=True),)
    items = [Breadcrumb("Root", "", files_href(root_id))]
    parts = tuple(part for part in PurePosixPath(relative_path).parts if part not in ("", "."))
    current: list[str] = []
    for index, part in enumerate(parts):
        current.append(part)
        crumb_path = "/".join(current)
        items.append(
            Breadcrumb(
                part,
                crumb_path,
                files_href(root_id, crumb_path),
                is_current=index == len(parts) - 1,
            )
        )
    return tuple(items)


def list_directory(
    data_root: Path | None,
    root_id: str,
    relative_path: object | None = "",
    *,
    instance: str = armactl_paths.DEFAULT_INSTANCE_NAME,
) -> DirectoryListing:
    """List one safe directory under an allowlisted root."""
    resolved = resolve_browser_path(data_root, root_id, relative_path, instance=instance)
    if not resolved.resolved_path.exists() or not resolved.resolved_path.is_dir():
        raise PathUnavailableError(PathUnavailableError.public_message)

    entries: list[FileMetadata] = []
    try:
        children = list(resolved.resolved_path.iterdir())
    except OSError as exc:
        raise PathUnavailableError(PathUnavailableError.public_message) from exc
    for entry in children:
        metadata = _entry_metadata(resolved.root, resolved.relative_path, entry)
        if metadata is not None:
            entries.append(metadata)
    entries.sort(key=lambda item: (not item.is_dir, item.name.casefold()))

    parent_path = parent_relative_path(resolved.relative_path)
    return DirectoryListing(
        root=resolved.root,
        relative_path=resolved.relative_path,
        breadcrumbs=_breadcrumbs(resolved.root.root_id, resolved.relative_path),
        parent_href=(
            files_href(resolved.root.root_id, parent_path)
            if resolved.relative_path
            else ""
        ),
        entries=tuple(entries),
    )


def get_file_metadata(
    data_root: Path | None,
    root_id: str,
    relative_path: object | None,
    *,
    instance: str = armactl_paths.DEFAULT_INSTANCE_NAME,
) -> FileMetadata:
    """Return safe metadata for one file or directory."""
    resolved = resolve_browser_path(data_root, root_id, relative_path, instance=instance)
    if not resolved.resolved_path.exists():
        raise PathUnavailableError(PathUnavailableError.public_message)
    return _metadata_from_path(resolved.root, resolved.resolved_path, resolved.relative_path)
