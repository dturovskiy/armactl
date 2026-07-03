"""Audited, allowlisted safe replacement workflow for file-browser files."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from armactl import paths
from armactl.web.services import config_edit, pending_work
from armactl.web.services.audit import AuditLogError, append_audit_event
from armactl.web.services.filesystem_errors import (
    FileBrowserError,
    ReplacementInvalidContentError,
    ReplacementTooLargeError,
    ReplacementUnavailableError,
)
from armactl.web.services.filesystem_paths import (
    ResolvedBrowserPath,
    parent_relative_path,
    resolve_browser_path,
)
from armactl.web.services.filesystem_roots import FileRoot, data_root_or_default
from armactl.web.services.filesystem_urls import files_href

MAX_REPLACEMENT_BYTES = 512 * 1024
_REPLACEMENT_CHUNK_BYTES = 256 * 1024
_SAFE_CONFIG_PART_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_TEXT_SUFFIXES = frozenset(
    {
        ".cfg",
        ".conf",
        ".ini",
        ".json",
        ".properties",
        ".txt",
        ".yaml",
        ".yml",
    }
)
_MUTABLE_CONFIG_DIRS = frozenset({"profile", "profiles", "settings"})
_MUTABLE_NESTED_CONFIG_DIRS = {
    "adminserversettings": 2,
    "profile/cmplayerstatshud": 3,
}
_READ_ONLY_CONFIG_DIRS = frozenset({"logs", "backups"})
_DENIED_REPLACEMENT_SUFFIXES = (
    ".bak",
    ".old",
    ".orig",
    ".tmp",
)

FILE_REPLACE_ACTION = "file.replace"
REPLACE_AUDIT_FAILED_MESSAGE = (
    "File replacement was not published because audit logging failed."
)
REPLACE_OUTCOME_AUDIT_FAILED_MESSAGE = (
    "File replacement was published but audit logging failed."
)
REPLACE_PUBLISH_FAILED_MESSAGE = "File replacement was audited but publishing failed."
REPLACE_PENDING_WARNING_MESSAGE = (
    "File replacement was published but restart tracking used fallback storage."
)
REPLACE_PENDING_FAILED_MESSAGE = (
    "File replacement was published but restart tracking failed."
)


class FileReplaceAuditError(RuntimeError):
    """Raised when replacement audit logging fails."""

    def __init__(self, message: str, *, result: ReplacedFile | None = None) -> None:
        super().__init__(message)
        self.result = result


class FileReplacePublishError(RuntimeError):
    """Raised when replacement was audited but could not be published."""


class FileReplaceTrackingError(RuntimeError):
    """Raised after publish when restart tracking needs operator attention."""

    def __init__(self, message: str, *, result: ReplacedFile) -> None:
        super().__init__(message)
        self.result = result


@dataclass(frozen=True)
class ReplacementValidation:
    """Validated replacement metadata that is safe to audit."""

    changed_fields: tuple[str, ...]
    baseline_fingerprint: str = ""
    current_fingerprint: str = ""
    requires_restart: bool = False


@dataclass(frozen=True)
class StagedReplacement:
    """Temporary replacement bytes validated for one final target."""

    root: FileRoot
    temp_path: Path
    target_path: Path
    relative_path: str
    directory_relative_path: str
    directory_href: str
    size: int
    old_sha256: str
    new_sha256: str
    file_kind: str
    validation: ReplacementValidation


@dataclass(frozen=True)
class ReplacedFile:
    """Result for one safely replaced file."""

    root: FileRoot
    path: Path
    relative_path: str
    directory_relative_path: str
    directory_href: str
    size: int
    backup_path: Path
    changed_fields: tuple[str, ...]
    pending_work_warning: str = ""
    pending_work_error: str = ""
    audit_written: bool = True


def _replacement_parts(relative_path: str) -> tuple[str, ...]:
    return tuple(part for part in PurePosixPath(relative_path).parts if part not in ("", "."))


def _safe_replacement_part(part: str) -> bool:
    return _SAFE_CONFIG_PART_RE.fullmatch(part) is not None


def _safe_backup_name(relative_path: str) -> str:
    parts = _replacement_parts(relative_path)
    joined = "__".join(parts) or "replacement"
    safe = "".join(char if char.isalnum() or char in "._-" else "_" for char in joined)
    return safe.strip(".") or "replacement"


def _is_safe_config_replacement_path(relative_path: str) -> bool:
    parts = _replacement_parts(relative_path)
    if not parts:
        return False
    if any(not _safe_replacement_part(part) for part in parts):
        return False
    lowered = tuple(part.casefold() for part in parts)
    if any(part in {".git", ".venv"} for part in lowered):
        return False
    if any(part.startswith(".") or part.startswith(".armactl-") for part in parts):
        return False
    if lowered[0] in _READ_ONLY_CONFIG_DIRS:
        return False

    filename = parts[-1]
    lower_name = filename.casefold()
    if lower_name.endswith(_DENIED_REPLACEMENT_SUFFIXES):
        return False
    suffix = Path(filename).suffix.casefold()
    if lower_name == "config.json":
        return len(parts) == 1
    if len(parts) == 1:
        return suffix in _TEXT_SUFFIXES
    if len(parts) == 2 and lowered[0] in _MUTABLE_CONFIG_DIRS:
        return suffix in _TEXT_SUFFIXES

    normalized_dir = "/".join(lowered[:-1])
    expected_depth = _MUTABLE_NESTED_CONFIG_DIRS.get(normalized_dir)
    if expected_depth is None or len(parts) != expected_depth:
        return False
    return suffix in _TEXT_SUFFIXES


def is_replacement_candidate(
    root: FileRoot,
    relative_path: str,
    path: Path | None = None,
) -> bool:
    """Return whether a safe listed file may show an explicit replace action."""
    if root.root_id != "config" or not root.available:
        return False
    if not _is_safe_config_replacement_path(relative_path):
        return False
    if path is None:
        return True
    try:
        return path.is_file() and not path.is_symlink()
    except OSError:
        return False


def _path_contains_symlink(root_path: Path, relative_path: str) -> bool:
    current = root_path
    for part in _replacement_parts(relative_path):
        current = current / part
        try:
            if current.is_symlink():
                return True
        except OSError:
            return True
    return False


def _validated_replacement_target(
    data_root: Path | None,
    root_id: str,
    relative_path: object | None,
    *,
    instance: str,
) -> ResolvedBrowserPath:
    resolved = resolve_browser_path(data_root, root_id, relative_path, instance=instance)
    if not is_replacement_candidate(
        resolved.root,
        resolved.relative_path,
        resolved.requested_path,
    ):
        raise ReplacementUnavailableError(ReplacementUnavailableError.public_message)
    if _path_contains_symlink(resolved.root.path, resolved.relative_path):
        raise ReplacementUnavailableError(ReplacementUnavailableError.public_message)
    try:
        if not resolved.requested_path.exists() or not resolved.requested_path.is_file():
            raise ReplacementUnavailableError(ReplacementUnavailableError.public_message)
    except OSError as exc:
        raise ReplacementUnavailableError(ReplacementUnavailableError.public_message) from exc
    return resolved


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _state_fingerprint(relative_path: str, digest: str) -> str:
    return pending_work.safe_state_fingerprint(
        {"file": relative_path, "sha256": digest, "scope": "file.replace"}
    )


def _read_text_replacement(path: Path) -> tuple[bytes, str]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ReplacementUnavailableError(ReplacementUnavailableError.public_message) from exc
    if b"\x00" in data:
        raise ReplacementInvalidContentError("Replacement file must be UTF-8 text.")
    if any(byte < 32 and byte not in (9, 10, 13) for byte in data):
        raise ReplacementInvalidContentError("Replacement file must be UTF-8 text.")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReplacementInvalidContentError("Replacement file must be UTF-8 text.") from exc
    return data, text


def _rewrite_staged_text(path: Path, text: str) -> bytes:
    data = text.encode("utf-8")
    try:
        with path.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise ReplacementUnavailableError(ReplacementUnavailableError.public_message) from exc
    return data


def _validate_json_text(text: str) -> None:
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReplacementInvalidContentError(
            f"Invalid JSON replacement at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc


def _validate_replacement_content(
    resolved: ResolvedBrowserPath,
    temp_path: Path,
    old_data: bytes,
) -> tuple[bytes, ReplacementValidation, str]:
    new_data, text = _read_text_replacement(temp_path)
    lower_name = Path(resolved.relative_path).name.casefold()
    if lower_name == "config.json":
        try:
            config_validation = config_edit.validate_raw_config_replacement(
                resolved.requested_path,
                text,
            )
        except config_edit.ConfigEditError as exc:
            raise ReplacementInvalidContentError(str(exc)) from exc
        new_data = _rewrite_staged_text(temp_path, config_validation.replacement_text)
        return (
            new_data,
            ReplacementValidation(
                changed_fields=config_validation.changed_fields,
                baseline_fingerprint=config_validation.baseline_fingerprint,
                current_fingerprint=config_validation.current_fingerprint,
                requires_restart=bool(config_validation.changed_fields),
            ),
            "config-json",
        )

    if Path(resolved.relative_path).suffix.casefold() == ".json":
        _validate_json_text(text)
    old_digest = _sha256_bytes(old_data)
    new_digest = _sha256_bytes(new_data)
    changed = old_digest != new_digest
    return (
        new_data,
        ReplacementValidation(
            changed_fields=("profile_file",) if changed else (),
            baseline_fingerprint=_state_fingerprint(resolved.relative_path, old_digest),
            current_fingerprint=_state_fingerprint(resolved.relative_path, new_digest),
            requires_restart=changed,
        ),
        "profile-file",
    )


def _stage_replacement_file(
    resolved: ResolvedBrowserPath,
    source: BinaryIO,
    *,
    max_bytes: int,
) -> StagedReplacement:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ReplacementUnavailableError(ReplacementUnavailableError.public_message)
    try:
        old_data = resolved.requested_path.read_bytes()
    except OSError as exc:
        raise ReplacementUnavailableError(ReplacementUnavailableError.public_message) from exc

    temp_path: Path | None = None
    total = 0
    try:
        with tempfile.NamedTemporaryFile(
            dir=resolved.requested_path.parent,
            prefix=f".armactl-replace-{resolved.requested_path.name}-",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            while True:
                chunk = source.read(_REPLACEMENT_CHUNK_BYTES)
                if chunk in (b"", ""):
                    break
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8")
                total += len(chunk)
                if total > max_bytes:
                    raise ReplacementTooLargeError(ReplacementTooLargeError.public_message)
                temp_file.write(chunk)
            temp_file.flush()
            os.fsync(temp_file.fileno())
    except FileBrowserError:
        if temp_path is not None:
            cleanup_staged_replacement_path(temp_path)
        raise
    except OSError as exc:
        if temp_path is not None:
            cleanup_staged_replacement_path(temp_path)
        raise ReplacementUnavailableError(ReplacementUnavailableError.public_message) from exc

    if temp_path is None:
        raise ReplacementUnavailableError(ReplacementUnavailableError.public_message)

    try:
        new_data, validation, file_kind = _validate_replacement_content(
            resolved,
            temp_path,
            old_data,
        )
    except FileBrowserError:
        cleanup_staged_replacement_path(temp_path)
        raise

    directory_relative_path = parent_relative_path(resolved.relative_path)
    return StagedReplacement(
        root=resolved.root,
        temp_path=temp_path,
        target_path=resolved.requested_path,
        relative_path=resolved.relative_path,
        directory_relative_path=directory_relative_path,
        directory_href=files_href(resolved.root.root_id, directory_relative_path),
        size=len(new_data),
        old_sha256=_sha256_bytes(old_data),
        new_sha256=_sha256_bytes(new_data),
        file_kind=file_kind,
        validation=validation,
    )


def cleanup_staged_replacement_path(temp_path: Path) -> bool:
    """Best-effort cleanup for one staged replacement temp path."""
    try:
        temp_path.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def cleanup_staged_replacement(staged: StagedReplacement) -> bool:
    """Best-effort cleanup for one staged replacement."""
    return cleanup_staged_replacement_path(staged.temp_path)


def _fsync_file(path: Path) -> None:
    try:
        with path.open("rb") as handle:
            os.fsync(handle.fileno())
    except OSError:
        return

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


def create_replacement_backup(
    staged: StagedReplacement,
    *,
    data_root: Path | None,
    instance: str,
) -> Path:
    """Copy the old target into the instance backup root before publication."""
    backup_root = paths.backups_dir(instance, data_root_or_default(data_root)) / "file-replacements"
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_name = _safe_backup_name(staged.relative_path)
    backup_path = backup_root / f"{safe_name}.before-web-file-replace-{timestamp}.bak"
    suffix = 1
    while backup_path.exists():
        backup_path = backup_root / (
            f"{safe_name}.before-web-file-replace-{timestamp}.{suffix}.bak"
        )
        suffix += 1
    try:
        backup_root.mkdir(parents=True, exist_ok=True)
        shutil.copy2(staged.target_path, backup_path)
        _fsync_file(backup_path)
        _fsync_directory(backup_root)
    except OSError as exc:
        try:
            backup_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise ReplacementUnavailableError("Failed to create file replacement backup.") from exc
    return backup_path


def publish_staged_replacement(staged: StagedReplacement) -> None:
    """Atomically publish a staged replacement over the existing target."""
    if not staged.target_path.exists() or staged.target_path.is_symlink():
        raise ReplacementUnavailableError(ReplacementUnavailableError.public_message)
    try:
        os.replace(staged.temp_path, staged.target_path)
    except OSError as exc:
        raise ReplacementUnavailableError(ReplacementUnavailableError.public_message) from exc
    _fsync_directory(staged.target_path.parent)


def _backup_name(backup_path: Path | None) -> str:
    return backup_path.name if backup_path is not None else ""


def _replace_audit_details(
    staged: StagedReplacement,
    *,
    phase: str,
    backup_path: Path | None = None,
    pending_warning: str = "",
    pending_error: str = "",
) -> dict[str, object]:
    return {
        "phase": phase,
        "root": staged.root.root_id,
        "path": staged.relative_path,
        "size": str(staged.size),
        "file_kind": staged.file_kind,
        "changed_fields": staged.validation.changed_fields,
        "backup_created": bool(backup_path),
        "backup_name": _backup_name(backup_path),
        "pending_restart": staged.validation.requires_restart,
        "pending_warning": bool(pending_warning),
        "pending_error": bool(pending_error),
    }


def _audit_replace(
    staged: StagedReplacement,
    *,
    audit_log_path: Path,
    username: str,
    instance: str,
    success: bool,
    message: str,
    phase: str,
    action: str = FILE_REPLACE_ACTION,
    backup_path: Path | None = None,
    pending_warning: str = "",
    pending_error: str = "",
) -> None:
    append_audit_event(
        audit_log_path,
        username=username,
        action=action,
        instance=instance,
        target=f"{staged.root.root_id}:{staged.relative_path}",
        success=success,
        message=message,
        exit_code=0 if success else 1,
        details=_replace_audit_details(
            staged,
            phase=phase,
            backup_path=backup_path,
            pending_warning=pending_warning,
            pending_error=pending_error,
        ),
    )


def _mark_restart_pending_for_replacement(
    staged: StagedReplacement,
    *,
    db_path: Path | None,
    username: str,
    instance: str,
) -> pending_work.PendingWorkWriteResult:
    if db_path is None or not staged.validation.requires_restart:
        return pending_work.PendingWorkWriteResult()
    details = ", ".join(staged.validation.changed_fields) or staged.relative_path
    return pending_work.mark_restart_pending_for_state(
        db_path,
        instance=instance,
        kind=pending_work.KIND_CONFIG,
        source_action=FILE_REPLACE_ACTION,
        source_path="/files/config",
        title="Config/profile file changes",
        username=username,
        details=details,
        baseline_fingerprint=staged.validation.baseline_fingerprint,
        current_fingerprint=staged.validation.current_fingerprint,
    )


def replace_file_and_audit(
    data_root: Path | None,
    root_id: str,
    relative_path: object | None,
    source: BinaryIO,
    *,
    audit_log_path: Path,
    username: str,
    db_path: Path | None = None,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    max_bytes: int | None = None,
) -> ReplacedFile:
    """Stage, validate, audit, backup, atomically replace, and track restart work."""
    limit = MAX_REPLACEMENT_BYTES if max_bytes is None else max_bytes
    resolved = _validated_replacement_target(
        data_root,
        root_id,
        relative_path,
        instance=instance,
    )
    staged = _stage_replacement_file(resolved, source, max_bytes=limit)
    backup_path: Path | None = None
    try:
        try:
            _audit_replace(
                staged,
                audit_log_path=audit_log_path,
                username=username,
                instance=instance,
                success=True,
                message="File replacement requested.",
                phase="intent",
            )
        except AuditLogError as exc:
            raise FileReplaceAuditError(REPLACE_AUDIT_FAILED_MESSAGE) from exc

        try:
            backup_path = create_replacement_backup(
                staged,
                data_root=data_root,
                instance=instance,
            )
            publish_staged_replacement(staged)
        except FileBrowserError as exc:
            try:
                _audit_replace(
                    staged,
                    audit_log_path=audit_log_path,
                    username=username,
                    instance=instance,
                    success=False,
                    message=exc.public_message,
                    phase="outcome",
                    action="file.replace.publish-failed",
                    backup_path=backup_path,
                )
            except AuditLogError:
                pass
            raise FileReplacePublishError(REPLACE_PUBLISH_FAILED_MESSAGE) from exc

        result = ReplacedFile(
            root=staged.root,
            path=staged.target_path,
            relative_path=staged.relative_path,
            directory_relative_path=staged.directory_relative_path,
            directory_href=staged.directory_href,
            size=staged.size,
            backup_path=backup_path,
            changed_fields=staged.validation.changed_fields,
        )
        pending_result = _mark_restart_pending_for_replacement(
            staged,
            db_path=db_path,
            username=username,
            instance=instance,
        )
        result = replace(
            result,
            pending_work_warning=pending_result.warning,
            pending_work_error=pending_result.error,
        )

        try:
            _audit_replace(
                staged,
                audit_log_path=audit_log_path,
                username=username,
                instance=instance,
                success=True,
                message="File replacement published.",
                phase="outcome",
                backup_path=backup_path,
                pending_warning=result.pending_work_warning,
                pending_error=result.pending_work_error,
            )
        except AuditLogError as exc:
            raise FileReplaceAuditError(
                REPLACE_OUTCOME_AUDIT_FAILED_MESSAGE,
                result=replace(result, audit_written=False),
            ) from exc

        if result.pending_work_error:
            raise FileReplaceTrackingError(REPLACE_PENDING_FAILED_MESSAGE, result=result)
        if result.pending_work_warning:
            raise FileReplaceTrackingError(REPLACE_PENDING_WARNING_MESSAGE, result=result)
        return result
    finally:
        cleanup_staged_replacement(staged)
