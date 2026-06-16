"""Safe read-only filesystem adapter for armactl web."""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from urllib.parse import quote

from armactl import paths
from armactl.redaction import redact_sensitive_text

MAX_PREVIEW_BYTES = 64 * 1024
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
_UPLOAD_CHUNK_BYTES = 1024 * 1024
FORBIDDEN_PATH_NAMES = frozenset({".git", ".venv"})
UPLOAD_ROOT_IDS = frozenset({"server"})
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
SYSTEM_PREFIXES = (
    Path("/etc"),
    Path("/root"),
    Path("/usr"),
    Path("/var"),
    paths.SYSTEMD_DIR,
    paths.SUDOERS_DIR,
)
_WEB_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?im)\b((?:ARMACTL_WEB_SESSION_SECRET|session_secret|session_token|"
    r"csrf_token|password_hash|armactl_web_session|armactl_web_csrf|secret|api_key)"
    r"\s*[=:]\s*)([^\s,;]+)"
)
_ARGON2_HASH_RE = re.compile(r"\$argon2(?:id|i|d)\$[^\s<>&]+")


class FileBrowserError(RuntimeError):
    """Raised when the web file browser rejects or cannot load a request."""

    public_message = "Unsafe file path."
    status_code = 400


class UnknownFileRootError(FileBrowserError):
    """Raised when a requested root id is not allowlisted."""

    public_message = "Unknown file root."
    status_code = 404


class RootUnavailableError(FileBrowserError):
    """Raised when a configured root is missing or unsafe."""

    public_message = "Root unavailable."
    status_code = 400


class UnsafeFilePathError(FileBrowserError):
    """Raised when a browser path escapes the selected root."""

    public_message = "Unsafe file path."
    status_code = 400


class PathUnavailableError(FileBrowserError):
    """Raised when a safe relative path does not point at the expected object."""

    public_message = "Path unavailable."
    status_code = 404


class DownloadUnavailableError(FileBrowserError):
    """Raised when a safe path cannot be downloaded as a single file."""

    public_message = "Download unavailable."
    status_code = 400


class UploadUnavailableError(FileBrowserError):
    """Raised when an upload cannot be accepted for a safe location."""

    public_message = "Upload unavailable."
    status_code = 400


class UploadTooLargeError(FileBrowserError):
    """Raised when an upload exceeds the configured streaming limit."""

    public_message = "Upload too large."
    status_code = 413


class InvalidUploadFilenameError(FileBrowserError):
    """Raised when an upload filename is empty or unsafe."""

    public_message = "Invalid filename."
    status_code = 400


class UploadTargetExistsError(FileBrowserError):
    """Raised when an upload would overwrite an existing file."""

    public_message = "File already exists."
    status_code = 409


@dataclass(frozen=True)
class FileRoot:
    """One fixed, read-only root exposed by the web file browser."""

    root_id: str
    label: str
    path: Path
    exists: bool
    available: bool
    reason: str = ""


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


@dataclass(frozen=True)
class DirectoryListing:
    """Directory entries under one safe root."""

    root: FileRoot
    relative_path: str
    breadcrumbs: tuple[Breadcrumb, ...]
    parent_href: str
    entries: tuple[FileMetadata, ...]


@dataclass(frozen=True)
class FilePreview:
    """Bounded text preview for one file."""

    root: FileRoot
    metadata: FileMetadata
    available: bool
    content: str = ""
    truncated: bool = False
    error: str = ""


@dataclass(frozen=True)
class DownloadFile:
    """Validated single-file download target."""

    root: FileRoot
    metadata: FileMetadata
    path: Path
    filename: str


@dataclass(frozen=True)
class UploadedFile:
    """Metadata for one safely uploaded file."""

    root: FileRoot
    filename: str
    path: Path
    relative_path: str
    directory_relative_path: str
    directory_href: str
    size: int


@dataclass(frozen=True)
class ResolvedBrowserPath:
    """Resolved path after root and relative-path validation."""

    root: FileRoot
    requested_path: Path
    resolved_path: Path
    relative_path: str


@dataclass(frozen=True)
class _RootDefinition:
    root_id: str
    label: str
    path_builder: object


ROOT_DEFINITIONS: tuple[_RootDefinition, ...] = (
    _RootDefinition("server", "Server files", paths.server_dir),
    _RootDefinition("config", "Config files", paths.config_dir),
    _RootDefinition("backups", "Backups", paths.backups_dir),
    _RootDefinition("logs", "Logs", paths.logs_dir),
)


def _data_root(data_root: Path | None) -> Path:
    return Path(data_root) if data_root is not None else paths.DEFAULT_DATA_ROOT


def _is_inside_or_equal(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _has_forbidden_part(path: Path) -> bool:
    return any(part in FORBIDDEN_PATH_NAMES for part in path.parts)


def _is_system_path(path: Path) -> bool:
    return any(_is_inside_or_equal(path, _resolved(prefix)) for prefix in SYSTEM_PREFIXES)


def _is_source_tree_path(path: Path) -> bool:
    source_root = _resolved(paths.project_root())
    return _is_inside_or_equal(path, source_root) or _is_inside_or_equal(source_root, path)


def _safe_root_state(root_path: Path, data_root: Path) -> tuple[bool, bool, str]:
    resolved_root = _resolved(root_path)
    resolved_data_root = _resolved(data_root)
    exists = root_path.exists()
    if not _is_inside_or_equal(resolved_root, resolved_data_root):
        return exists, False, "Root unavailable."
    if _has_forbidden_part(resolved_root) or _is_system_path(resolved_root):
        return exists, False, "Root unavailable."
    if _is_source_tree_path(resolved_root):
        return exists, False, "Root unavailable."
    if not exists or not root_path.is_dir():
        return exists, False, "Root unavailable."
    return exists, True, ""


def list_allowed_roots(
    data_root: Path | None,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> tuple[FileRoot, ...]:
    """Return the fixed, safe roots exposed by the read-only file browser."""
    root_data = _data_root(data_root)
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


def _normalize_relative_parts(relative_path: object | None) -> tuple[str, ...]:
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


def _relative_path_from_parts(parts: tuple[str, ...]) -> str:
    return "/".join(parts)


def _query_path(relative_path: str) -> str:
    return quote(relative_path, safe="")


def _files_href(root_id: str, relative_path: str = "") -> str:
    if not relative_path:
        return f"/files/{root_id}"
    return f"/files/{root_id}?path={_query_path(relative_path)}"


def _preview_href(root_id: str, relative_path: str) -> str:
    return f"/files/{root_id}/preview?path={_query_path(relative_path)}"


def _download_href(root_id: str, relative_path: str) -> str:
    return f"/files/{root_id}/download?path={_query_path(relative_path)}"


def _is_preview_candidate(path: Path) -> bool:
    lower_name = path.name.casefold()
    if lower_name in PREVIEW_TEXT_FILENAMES:
        return True
    suffixes = {suffix.casefold() for suffix in path.suffixes}
    if suffixes & PREVIEW_TEXT_SUFFIXES:
        return True
    return False


def resolve_browser_path(
    data_root: Path | None,
    root_id: str,
    relative_path: object | None = "",
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> ResolvedBrowserPath:
    """Resolve and validate a browser relative path under a fixed root."""
    root = get_file_root(data_root, root_id, instance=instance)
    if not root.available:
        raise RootUnavailableError(root.reason or RootUnavailableError.public_message)

    parts = _normalize_relative_parts(relative_path)
    requested = root.path.joinpath(*parts)
    resolved_root = _resolved(root.path)
    resolved_target = _resolved(requested)
    if not _is_inside_or_equal(resolved_target, resolved_root):
        raise UnsafeFilePathError(UnsafeFilePathError.public_message)
    if _has_forbidden_part(resolved_target) or _is_system_path(resolved_target):
        raise UnsafeFilePathError(UnsafeFilePathError.public_message)
    if _is_source_tree_path(resolved_target):
        raise UnsafeFilePathError(UnsafeFilePathError.public_message)

    return ResolvedBrowserPath(
        root=root,
        requested_path=requested,
        resolved_path=resolved_target,
        relative_path=_relative_path_from_parts(parts),
    )


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    value = float(size)
    for unit in ("KiB", "MiB", "GiB"):
        value /= 1024
        if value < 1024:
            return f"{value:.1f} {unit}"
    return f"{value:.1f} TiB"


def _format_modified(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds")


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
        size_text=_format_size(stat_result.st_size) if is_file else "",
        modified_at=_format_modified(stat_result.st_mtime),
        href=_files_href(root.root_id, relative_path) if is_dir else "",
        preview_href=(
            _preview_href(root.root_id, relative_path)
            if is_file and _is_preview_candidate(path)
            else ""
        ),
        download_href=_download_href(root.root_id, relative_path) if is_file else "",
    )


def _entry_metadata(root: FileRoot, base_relative: str, entry: Path) -> FileMetadata | None:
    if entry.name in FORBIDDEN_PATH_NAMES:
        return None
    relative_path = f"{base_relative}/{entry.name}" if base_relative else entry.name
    try:
        resolved_entry = _resolved(entry)
        resolved_root = _resolved(root.path)
    except OSError:
        return None
    if not _is_inside_or_equal(resolved_entry, resolved_root):
        return None
    if _has_forbidden_part(resolved_entry) or _is_system_path(resolved_entry):
        return None
    if _is_source_tree_path(resolved_entry):
        return None
    try:
        return _metadata_from_path(root, entry, relative_path)
    except PathUnavailableError:
        return None


def _breadcrumbs(root_id: str, relative_path: str) -> tuple[Breadcrumb, ...]:
    if not relative_path:
        return (Breadcrumb("Root", "", _files_href(root_id), is_current=True),)
    items = [Breadcrumb("Root", "", _files_href(root_id))]
    parts = tuple(part for part in PurePosixPath(relative_path).parts if part not in ("", "."))
    current: list[str] = []
    for index, part in enumerate(parts):
        current.append(part)
        crumb_path = "/".join(current)
        items.append(
            Breadcrumb(
                part,
                crumb_path,
                _files_href(root_id, crumb_path),
                is_current=index == len(parts) - 1,
            )
        )
    return tuple(items)


def parent_relative_path(relative_path: str) -> str:
    """Return the parent browser path for a normalized relative path."""
    parts = tuple(part for part in PurePosixPath(relative_path).parts if part not in ("", "."))
    if len(parts) <= 1:
        return ""
    return "/".join(parts[:-1])


def list_directory(
    data_root: Path | None,
    root_id: str,
    relative_path: object | None = "",
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
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
            _files_href(resolved.root.root_id, parent_path)
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
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> FileMetadata:
    """Return safe metadata for one file or directory."""
    resolved = resolve_browser_path(data_root, root_id, relative_path, instance=instance)
    if not resolved.resolved_path.exists():
        raise PathUnavailableError(PathUnavailableError.public_message)
    return _metadata_from_path(resolved.root, resolved.resolved_path, resolved.relative_path)


def _safe_download_filename(name: str) -> str:
    """Return a conservative attachment filename from a basename only."""
    basename = Path(name).name.replace("/", "_").replace("\\", "_")
    safe_chars = []
    for char in basename:
        if ord(char) < 32 or char == "\x7f" or char in {'"', "'"}:
            safe_chars.append("_")
        else:
            safe_chars.append(char)
    return "".join(safe_chars).strip(" .") or "download"


def _safe_upload_filename(filename: object | None) -> str:
    """Return a safe basename for a new uploaded file or raise a controlled error."""
    if not isinstance(filename, str):
        raise InvalidUploadFilenameError(InvalidUploadFilenameError.public_message)
    raw = filename.strip()
    if not raw or raw in {".", ".."}:
        raise InvalidUploadFilenameError(InvalidUploadFilenameError.public_message)
    if "\x00" in raw or "/" in raw or "\\" in raw:
        raise InvalidUploadFilenameError(InvalidUploadFilenameError.public_message)
    if any(ord(char) < 32 or char == "\x7f" for char in raw):
        raise InvalidUploadFilenameError(InvalidUploadFilenameError.public_message)

    basename = Path(raw).name
    if basename != raw or basename in FORBIDDEN_PATH_NAMES:
        raise InvalidUploadFilenameError(InvalidUploadFilenameError.public_message)
    return basename


def _child_relative_path(parent_relative_path: str, filename: str) -> str:
    if not parent_relative_path:
        return filename
    return f"{parent_relative_path}/{filename}"


def _fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        fd = os.open(directory, flags)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        return
    finally:
        os.close(fd)


def upload_file(
    data_root: Path | None,
    root_id: str,
    directory_path: object | None,
    filename: object | None,
    source: BinaryIO,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    max_bytes: int | None = None,
) -> UploadedFile:
    """Upload one new file into a safe directory without overwriting."""
    safe_filename = _safe_upload_filename(filename)
    limit = MAX_UPLOAD_BYTES if max_bytes is None else max_bytes
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise UploadUnavailableError(UploadUnavailableError.public_message)

    directory = resolve_browser_path(data_root, root_id, directory_path, instance=instance)
    if not root_allows_upload(directory.root):
        raise UploadUnavailableError(UploadUnavailableError.public_message)
    if not directory.resolved_path.exists() or not directory.resolved_path.is_dir():
        raise UploadUnavailableError(UploadUnavailableError.public_message)

    target_relative_path = _child_relative_path(directory.relative_path, safe_filename)
    target = resolve_browser_path(data_root, root_id, target_relative_path, instance=instance)
    if target.requested_path.exists() or target.requested_path.is_symlink():
        raise UploadTargetExistsError(UploadTargetExistsError.public_message)
    if target.resolved_path.exists():
        raise UploadTargetExistsError(UploadTargetExistsError.public_message)

    temp_path: Path | None = None
    total = 0
    published = False
    try:
        with tempfile.NamedTemporaryFile(
            dir=directory.resolved_path,
            prefix=".armactl-upload-",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            while True:
                chunk = source.read(_UPLOAD_CHUNK_BYTES)
                if chunk in (b"", ""):
                    break
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8")
                total += len(chunk)
                if total > limit:
                    raise UploadTooLargeError(UploadTooLargeError.public_message)
                temp_file.write(chunk)
            temp_file.flush()
            os.fsync(temp_file.fileno())

        try:
            os.link(temp_path, target.requested_path)
        except FileExistsError as exc:
            raise UploadTargetExistsError(UploadTargetExistsError.public_message) from exc
        published = True
        _fsync_directory(directory.resolved_path)
    except FileBrowserError:
        raise
    except OSError as exc:
        raise UploadUnavailableError(UploadUnavailableError.public_message) from exc
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    if not published:
        raise UploadUnavailableError(UploadUnavailableError.public_message)

    return UploadedFile(
        root=directory.root,
        filename=safe_filename,
        path=target.requested_path,
        relative_path=target_relative_path,
        directory_relative_path=directory.relative_path,
        directory_href=_files_href(directory.root.root_id, directory.relative_path),
        size=total,
    )


def resolve_download_file(
    data_root: Path | None,
    root_id: str,
    relative_path: object | None,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> DownloadFile:
    """Resolve one safe file target for attachment download."""
    resolved = resolve_browser_path(data_root, root_id, relative_path, instance=instance)
    metadata = get_file_metadata(data_root, root_id, relative_path, instance=instance)
    if not metadata.is_file:
        raise DownloadUnavailableError(DownloadUnavailableError.public_message)
    return DownloadFile(
        root=resolved.root,
        metadata=metadata,
        path=resolved.resolved_path,
        filename=_safe_download_filename(metadata.name),
    )


def _redact_preview_text(text: str) -> str:
    redacted = redact_sensitive_text(text)
    redacted = _WEB_SECRET_ASSIGNMENT_RE.sub(r"\1***", redacted)
    return _ARGON2_HASH_RE.sub("***", redacted)


def preview_text_file(
    data_root: Path | None,
    root_id: str,
    relative_path: object | None,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> FilePreview:
    """Return a bounded, redacted text preview for one safe file."""
    metadata = get_file_metadata(data_root, root_id, relative_path, instance=instance)
    resolved = resolve_browser_path(data_root, root_id, relative_path, instance=instance)
    if not metadata.is_file:
        return FilePreview(resolved.root, metadata, False, error="Preview unavailable.")

    try:
        with resolved.resolved_path.open("rb") as handle:
            data = handle.read(MAX_PREVIEW_BYTES + 1)
    except OSError:
        return FilePreview(resolved.root, metadata, False, error="Preview unavailable.")
    if b"\x00" in data[:4096]:
        return FilePreview(resolved.root, metadata, False, error="Preview unavailable.")

    truncated = len(data) > MAX_PREVIEW_BYTES
    if truncated:
        data = data[:MAX_PREVIEW_BYTES]
    text = data.decode("utf-8", errors="replace")
    return FilePreview(
        root=resolved.root,
        metadata=metadata,
        available=True,
        content=_redact_preview_text(text),
        truncated=truncated,
    )
