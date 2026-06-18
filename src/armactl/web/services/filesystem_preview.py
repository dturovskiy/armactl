"""Bounded, redacted text previews for the web file browser."""

from __future__ import annotations

import re
from dataclasses import dataclass

from armactl import paths as armactl_paths
from armactl.redaction import redact_sensitive_text
from armactl.web.services.filesystem_listing import FileMetadata, get_file_metadata
from armactl.web.services.filesystem_paths import resolve_browser_path
from armactl.web.services.filesystem_roots import FileRoot

MAX_PREVIEW_BYTES = 64 * 1024
_WEB_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?im)\b((?:ARMACTL_WEB_SESSION_SECRET|session_secret|session_token|"
    r"csrf_token|password_hash|armactl_web_session|armactl_web_csrf|secret|api_key)"
    r"\s*[=:]\s*)([^\s,;]+)"
)
_ARGON2_HASH_RE = re.compile(r"\$argon2(?:id|i|d)\$[^\s<>&]+")


@dataclass(frozen=True)
class FilePreview:
    """Bounded text preview for one file."""

    root: FileRoot
    metadata: FileMetadata
    available: bool
    content: str = ""
    truncated: bool = False
    error: str = ""


def redact_preview_text(text: str) -> str:
    """Redact generic and web-specific secrets from preview text."""
    redacted = redact_sensitive_text(text)
    redacted = _WEB_SECRET_ASSIGNMENT_RE.sub(r"\1***", redacted)
    return _ARGON2_HASH_RE.sub("***", redacted)


def preview_text_file(
    data_root: object | None,
    root_id: str,
    relative_path: object | None,
    *,
    instance: str = armactl_paths.DEFAULT_INSTANCE_NAME,
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
        content=redact_preview_text(text),
        truncated=truncated,
    )
