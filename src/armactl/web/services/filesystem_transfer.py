"""Download and upload transfer helpers for the web file browser."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from armactl import paths as armactl_paths
from armactl.web.services.filesystem_errors import (
    DownloadUnavailableError,
    FileBrowserError,
    InvalidUploadFilenameError,
    UploadTargetExistsError,
    UploadTooLargeError,
    UploadUnavailableError,
)
from armactl.web.services.filesystem_listing import FileMetadata, get_file_metadata
from armactl.web.services.filesystem_paths import (
    FORBIDDEN_PATH_NAMES,
    ResolvedBrowserPath,
    child_relative_path,
    resolve_browser_path,
)
from armactl.web.services.filesystem_roots import FileRoot, root_allows_upload
from armactl.web.services.filesystem_urls import files_href

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
_UPLOAD_CHUNK_BYTES = 1024 * 1024


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
class StagedUpload:
    """Temporary upload bytes validated for one final no-overwrite target."""

    root: FileRoot
    filename: str
    temp_path: Path
    target_path: Path
    relative_path: str
    directory_relative_path: str
    directory_href: str
    size: int


def safe_download_filename(name: str) -> str:
    """Return a conservative attachment filename from a basename only."""
    basename = Path(name).name.replace("/", "_").replace("\\", "_")
    safe_chars = []
    for char in basename:
        if ord(char) < 32 or char == "\x7f" or char in {'"', "'"}:
            safe_chars.append("_")
        else:
            safe_chars.append(char)
    return "".join(safe_chars).strip(" .") or "download"


def safe_upload_filename(filename: object | None) -> str:
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


def _validated_upload_target(
    data_root: Path | None,
    root_id: str,
    directory_path: object | None,
    filename: object | None,
    *,
    instance: str,
) -> tuple[ResolvedBrowserPath, str, ResolvedBrowserPath]:
    safe_filename = safe_upload_filename(filename)
    directory = resolve_browser_path(data_root, root_id, directory_path, instance=instance)
    if not root_allows_upload(directory.root):
        raise UploadUnavailableError(UploadUnavailableError.public_message)
    if not directory.resolved_path.exists() or not directory.resolved_path.is_dir():
        raise UploadUnavailableError(UploadUnavailableError.public_message)

    target_relative_path = child_relative_path(directory.relative_path, safe_filename)
    target = resolve_browser_path(data_root, root_id, target_relative_path, instance=instance)
    if target.requested_path.exists() or target.requested_path.is_symlink():
        raise UploadTargetExistsError(UploadTargetExistsError.public_message)
    if target.resolved_path.exists():
        raise UploadTargetExistsError(UploadTargetExistsError.public_message)
    return directory, safe_filename, target


def stage_upload_file(
    data_root: Path | None,
    root_id: str,
    directory_path: object | None,
    filename: object | None,
    source: BinaryIO,
    *,
    instance: str = armactl_paths.DEFAULT_INSTANCE_NAME,
    max_bytes: int | None = None,
) -> StagedUpload:
    """Validate and write upload bytes to a temporary staged file."""
    limit = MAX_UPLOAD_BYTES if max_bytes is None else max_bytes
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise UploadUnavailableError(UploadUnavailableError.public_message)

    directory, safe_filename, target = _validated_upload_target(
        data_root,
        root_id,
        directory_path,
        filename,
        instance=instance,
    )

    temp_path: Path | None = None
    total = 0
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
    except FileBrowserError:
        if temp_path is not None:
            cleanup_staged_upload_path(temp_path)
        raise
    except OSError as exc:
        if temp_path is not None:
            cleanup_staged_upload_path(temp_path)
        raise UploadUnavailableError(UploadUnavailableError.public_message) from exc

    if temp_path is None:
        raise UploadUnavailableError(UploadUnavailableError.public_message)

    return StagedUpload(
        root=directory.root,
        filename=safe_filename,
        temp_path=temp_path,
        target_path=target.requested_path,
        relative_path=target.relative_path,
        directory_relative_path=directory.relative_path,
        directory_href=files_href(directory.root.root_id, directory.relative_path),
        size=total,
    )


def cleanup_staged_upload_path(temp_path: Path) -> bool:
    """Best-effort cleanup for one staged upload temp path."""
    try:
        temp_path.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def cleanup_staged_upload(staged: StagedUpload) -> bool:
    """Best-effort cleanup for one staged upload."""
    return cleanup_staged_upload_path(staged.temp_path)


def publish_staged_upload(staged: StagedUpload) -> UploadedFile:
    """Atomically publish a staged upload to its final target without overwrite."""
    if staged.target_path.exists() or staged.target_path.is_symlink():
        raise UploadTargetExistsError(UploadTargetExistsError.public_message)
    try:
        os.link(staged.temp_path, staged.target_path)
    except FileExistsError as exc:
        raise UploadTargetExistsError(UploadTargetExistsError.public_message) from exc
    except OSError as exc:
        raise UploadUnavailableError(UploadUnavailableError.public_message) from exc
    _fsync_directory(staged.target_path.parent)
    return UploadedFile(
        root=staged.root,
        filename=staged.filename,
        path=staged.target_path,
        relative_path=staged.relative_path,
        directory_relative_path=staged.directory_relative_path,
        directory_href=staged.directory_href,
        size=staged.size,
    )


def upload_file(
    data_root: Path | None,
    root_id: str,
    directory_path: object | None,
    filename: object | None,
    source: BinaryIO,
    *,
    instance: str = armactl_paths.DEFAULT_INSTANCE_NAME,
    max_bytes: int | None = None,
) -> UploadedFile:
    """Upload one new file into a safe directory without overwriting."""
    staged = stage_upload_file(
        data_root,
        root_id,
        directory_path,
        filename,
        source,
        instance=instance,
        max_bytes=max_bytes,
    )
    try:
        return publish_staged_upload(staged)
    finally:
        cleanup_staged_upload(staged)


def resolve_download_file(
    data_root: Path | None,
    root_id: str,
    relative_path: object | None,
    *,
    instance: str = armactl_paths.DEFAULT_INSTANCE_NAME,
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
        filename=safe_download_filename(metadata.name),
    )
