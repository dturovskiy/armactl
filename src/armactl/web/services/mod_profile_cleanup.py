"""Safe cleanup for stale profile settings references to disabled mods."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from armactl import discovery, mods_diagnostics, paths
from armactl.addon_cleanup import normalize_mod_id
from armactl.mods_state import load_disabled_mods
from armactl.web.services import mutation_recovery, pending_work
from armactl.web.services.audit import AuditLogError, append_audit_event
from armactl.web.services.filesystem_paths import (
    has_forbidden_part,
    is_inside_or_equal,
    is_source_tree_path,
    is_system_path,
    resolved_path,
)
from armactl.web.services.mod_actions import ModActionResult

ACTION_PROFILE_SETTINGS_CLEANUP = "mod.profile-settings-cleanup"
TARGET_PROFILE_SETTINGS = "profile settings"
BACKUP_SUBDIR = "profile-settings-cleanup"

PROFILE_CLEANUP_UNAVAILABLE_MESSAGE = "Profile settings cleanup is unavailable."
PROFILE_CLEANUP_INTENT_AUDIT_FAILED_MESSAGE = (
    "Profile settings cleanup was not run because audit logging failed."
)
PROFILE_CLEANUP_OUTCOME_AUDIT_FAILED_MESSAGE = (
    "Profile settings cleanup completed but audit logging failed."
)
PROFILE_CLEANUP_FAILED_MESSAGE = "Profile settings cleanup failed before changing files."
PROFILE_CLEANUP_PARTIAL_MESSAGE = (
    "Profile settings cleanup partially completed; review diagnostics and pending restart "
    "before retrying."
)
PROFILE_CLEANUP_PENDING_FAILED_MESSAGE = (
    "Profile settings cleanup changed files, but restart tracking failed."
)
PROFILE_CLEANUP_NOOP_MESSAGE = "No stale profile settings references found."
PROFILE_CLEANUP_SKIPPED_MESSAGE = (
    "Profile settings cleanup skipped ambiguous references; no files were changed."
)
PROFILE_CLEANUP_SUCCESS_MESSAGE = "Profile settings cleanup removed stale references."

_COUNT_FIELDS = (
    "files_considered",
    "files_changed",
    "modules_removed",
    "skipped_ambiguous",
    "skipped_missing",
)
_SAFE_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_MODULE_BOUNDARY = r"(?<![A-Za-z0-9_]){}(?![A-Za-z0-9_])"
_DENIED_TOP_LEVEL_CANDIDATE_DIRS = frozenset({"logs", "backups", "server"})


class ProfileCleanupError(ValueError):
    """Raised when profile settings cleanup cannot safely continue."""


@dataclass(frozen=True)
class ProfileCleanupCounts:
    """Counts-only summary safe for UI and audit output."""

    files_considered: int = 0
    files_changed: int = 0
    modules_removed: int = 0
    skipped_ambiguous: int = 0
    skipped_missing: int = 0

    def details(self) -> dict[str, str]:
        """Return audit/template safe count strings."""
        return {field: str(getattr(self, field)) for field in _COUNT_FIELDS}


@dataclass(frozen=True)
class TextCleanupResult:
    """Text cleanup result for one profile settings file."""

    text: str
    modules_removed: int = 0
    skipped_ambiguous: int = 0
    skipped_missing: int = 0


@dataclass(frozen=True)
class ProfileSettingsFilePlan:
    """Prepared update for one allowlisted profile settings file."""

    path: Path
    relative_path: str
    original_bytes: bytes
    original_text: str
    updated_text: str
    modules_removed: int
    skipped_ambiguous: int
    skipped_missing: int

    @property
    def changed(self) -> bool:
        return self.updated_text != self.original_text

    @property
    def original_sha256(self) -> str:
        return _sha256_bytes(self.original_bytes)

    @property
    def updated_sha256(self) -> str:
        return _sha256_bytes(self.updated_text.encode("utf-8"))


@dataclass(frozen=True)
class ProfileCleanupPlan:
    """Prepared cleanup plan with no writes performed yet."""

    config_path: Path
    file_plans: tuple[ProfileSettingsFilePlan, ...]
    counts: ProfileCleanupCounts

    @property
    def changed_file_plans(self) -> tuple[ProfileSettingsFilePlan, ...]:
        return tuple(plan for plan in self.file_plans if plan.changed)


@dataclass(frozen=True)
class PublishedProfileSettingsUpdate:
    """One file published by the cleanup workflow."""

    file_plan: ProfileSettingsFilePlan
    backup_path: Path


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_result_details(counts: ProfileCleanupCounts) -> dict[str, object]:
    return counts.details()


def _result(
    *,
    instance: str,
    success: bool,
    changed: bool,
    message: str,
    counts: ProfileCleanupCounts,
    exit_code: int,
    audit_written: bool = True,
    intent_audited: bool = True,
    backend_success: bool | None = None,
    backend_message: str = "",
    pending_work_warning: str = "",
    pending_work_error: str = "",
) -> ModActionResult:
    return ModActionResult(
        action=ACTION_PROFILE_SETTINGS_CLEANUP,
        instance=instance,
        target=TARGET_PROFILE_SETTINGS,
        success=success,
        changed=changed,
        message=message,
        exit_code=exit_code,
        restart_required=changed,
        audit_written=audit_written,
        intent_audited=intent_audited,
        backend_success=backend_success,
        backend_message=backend_message,
        pending_work_warning=pending_work_warning,
        pending_work_error=pending_work_error,
        details=_safe_result_details(counts),
    )


def _config_path(instance: str) -> Path:
    try:
        state = discovery.discover(instance=instance, save=False)
    except Exception as exc:  # noqa: BLE001 - discovery failures must stay controlled.
        raise ProfileCleanupError(PROFILE_CLEANUP_UNAVAILABLE_MESSAGE) from exc
    if not state.config_path:
        raise ProfileCleanupError(PROFILE_CLEANUP_UNAVAILABLE_MESSAGE)
    config_path = Path(state.config_path)
    if not config_path.is_file() or config_path.is_symlink():
        raise ProfileCleanupError(PROFILE_CLEANUP_UNAVAILABLE_MESSAGE)
    resolved_config = resolved_path(config_path)
    if (
        has_forbidden_part(resolved_config)
        or is_system_path(resolved_config)
        or is_source_tree_path(resolved_config)
    ):
        raise ProfileCleanupError(PROFILE_CLEANUP_UNAVAILABLE_MESSAGE)
    return config_path


def _known_disabled_modules(
    config_path: Path,
    known_modules: Mapping[str, tuple[str, ...]],
) -> tuple[str, ...]:
    try:
        disabled_mods = load_disabled_mods(config_path)
    except Exception as exc:  # noqa: BLE001 - sidecar parse errors fail closed.
        raise ProfileCleanupError(PROFILE_CLEANUP_UNAVAILABLE_MESSAGE) from exc

    disabled_ids: set[str] = set()
    for raw in disabled_mods:
        if not isinstance(raw, dict):
            continue
        mod_id = normalize_mod_id(str(raw.get("modId") or raw.get("mod_id") or ""))
        if mod_id:
            disabled_ids.add(mod_id)

    modules: list[str] = []
    seen: set[str] = set()
    for mod_id in sorted(disabled_ids):
        for module_name in known_modules.get(mod_id, ()):
            if not _SAFE_MODULE_RE.fullmatch(module_name):
                continue
            if module_name not in seen:
                modules.append(module_name)
                seen.add(module_name)
    return tuple(modules)


def _relative_candidate_parts(relative_path: Path) -> tuple[str, ...]:
    if relative_path.is_absolute():
        raise ProfileCleanupError(PROFILE_CLEANUP_UNAVAILABLE_MESSAGE)
    parts = tuple(part for part in relative_path.parts if part not in ("", "."))
    if not parts:
        raise ProfileCleanupError(PROFILE_CLEANUP_UNAVAILABLE_MESSAGE)
    lowered = tuple(part.casefold() for part in parts)
    if any(part == ".." for part in parts):
        raise ProfileCleanupError(PROFILE_CLEANUP_UNAVAILABLE_MESSAGE)
    if any("/" in part or "\\" in part or "\x00" in part for part in parts):
        raise ProfileCleanupError(PROFILE_CLEANUP_UNAVAILABLE_MESSAGE)
    if lowered[0] in _DENIED_TOP_LEVEL_CANDIDATE_DIRS:
        raise ProfileCleanupError(PROFILE_CLEANUP_UNAVAILABLE_MESSAGE)
    if any(part in {".git", ".venv"} for part in lowered):
        raise ProfileCleanupError(PROFILE_CLEANUP_UNAVAILABLE_MESSAGE)
    return parts


def _path_contains_symlink(root: Path, parts: Iterable[str]) -> bool:
    current = root
    for part in parts:
        current = current / part
        try:
            if current.is_symlink():
                return True
        except OSError:
            return True
    return False


def _candidate_path(config_dir: Path, relative_path: Path) -> tuple[Path, str]:
    parts = _relative_candidate_parts(relative_path)
    candidate = config_dir.joinpath(*parts)
    resolved_config_dir = resolved_path(config_dir)
    resolved_candidate = resolved_path(candidate)
    if not is_inside_or_equal(resolved_candidate, resolved_config_dir):
        raise ProfileCleanupError(PROFILE_CLEANUP_UNAVAILABLE_MESSAGE)
    if (
        has_forbidden_part(resolved_candidate)
        or is_system_path(resolved_candidate)
        or is_source_tree_path(resolved_candidate)
    ):
        raise ProfileCleanupError(PROFILE_CLEANUP_UNAVAILABLE_MESSAGE)
    if _path_contains_symlink(config_dir, parts):
        raise ProfileCleanupError(PROFILE_CLEANUP_UNAVAILABLE_MESSAGE)
    return candidate, "/".join(parts)


def _scan_braces(text: str, *, start_index: int = 0, stop_at_matching: bool = False) -> int | None:
    depth = 0
    index = start_index
    state = "normal"
    quote = ""
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""

        if state == "line_comment":
            if char == "\n":
                state = "normal"
            index += 1
            continue

        if state == "block_comment":
            if char == "*" and next_char == "/":
                state = "normal"
                index += 2
                continue
            index += 1
            continue

        if state == "string":
            if char == "\\":
                index += 2
                continue
            if char == quote:
                state = "normal"
                quote = ""
            index += 1
            continue

        if char in {'"', "'"}:
            state = "string"
            quote = char
            index += 1
            continue
        if char == "/" and next_char == "/":
            state = "line_comment"
            index += 2
            continue
        if char == "/" and next_char == "*":
            state = "block_comment"
            index += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                return None
            if stop_at_matching and depth == 0:
                return index
        index += 1

    if state in {"block_comment", "string"}:
        return None
    if stop_at_matching:
        return None
    return -1 if depth == 0 else None


def _text_has_balanced_braces(text: str) -> bool:
    return _scan_braces(text) == -1


def _line_start(text: str, index: int) -> int:
    return text.rfind("\n", 0, index) + 1


def _header_opening_brace_index(text: str, start_index: int) -> int | None:
    index = start_index
    state = "normal"
    quote = ""
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""

        if state == "line_comment":
            return None

        if state == "block_comment":
            if char == "*" and next_char == "/":
                state = "normal"
                index += 2
                continue
            index += 1
            continue

        if state == "string":
            if char == "\\":
                index += 2
                continue
            if char == quote:
                state = "normal"
                quote = ""
            index += 1
            continue

        if char in "\r\n":
            return None
        if char in (chr(34), chr(39)):
            state = "string"
            quote = char
            index += 1
            continue
        if char == "/" and next_char == "/":
            state = "line_comment"
            index += 2
            continue
        if char == "/" and next_char == "*":
            state = "block_comment"
            index += 2
            continue
        if char == "{":
            return index
        index += 1

    return None


def _safe_module_header_remainder(value: str) -> bool:
    if not value.strip():
        return True
    return all(
        char.isalnum() or char in " \t_{}\"-" or char == chr(39)
        for char in value
    )


def _block_span_for_match(text: str, match: re.Match[str]) -> tuple[int, int] | None:
    start = match.start()
    line_start = _line_start(text, start)
    if text[line_start:start].strip():
        return None

    brace_index = _header_opening_brace_index(text, match.end())
    if brace_index is None:
        return None
    if not _safe_module_header_remainder(text[match.end() : brace_index]):
        return None

    close_index = _scan_braces(text, start_index=brace_index, stop_at_matching=True)
    if close_index is None:
        return None

    end = close_index + 1
    while end < len(text) and text[end] in " \t":
        end += 1
    if end < len(text) and text[end] == ";":
        end += 1
        while end < len(text) and text[end] in " \t":
            end += 1

    if end >= len(text):
        return line_start, end
    if text[end] == "\r":
        end += 1
        if end < len(text) and text[end] == "\n":
            end += 1
        return line_start, end
    if text[end] == "\n":
        return line_start, end + 1
    return None


def _module_pattern(module_name: str) -> re.Pattern[str]:
    return re.compile(_MODULE_BOUNDARY.format(re.escape(module_name)))


def remove_known_module_blocks(
    text: str,
    module_names: Iterable[str],
) -> TextCleanupResult:
    """Remove exact known module blocks when their balanced stanza is unambiguous."""
    spans: list[tuple[int, int]] = []
    modules_removed = 0
    skipped_ambiguous = 0
    skipped_missing = 0

    for module_name in module_names:
        matches = list(_module_pattern(module_name).finditer(text))
        if not matches:
            skipped_missing += 1
            continue

        header_matches = [
            match
            for match in matches
            if not text[_line_start(text, match.start()) : match.start()].strip()
        ]
        if not header_matches:
            skipped_ambiguous += len(matches)
            continue

        valid_for_module = 0
        for match in header_matches:
            span = _block_span_for_match(text, match)
            if span is None:
                skipped_ambiguous += 1
                continue
            spans.append(span)
            valid_for_module += 1
        modules_removed += valid_for_module

    if not spans:
        return TextCleanupResult(
            text,
            modules_removed=0,
            skipped_ambiguous=skipped_ambiguous,
            skipped_missing=skipped_missing,
        )

    spans.sort()
    checked_spans: list[tuple[int, int]] = []
    last_end = -1
    for start, end in spans:
        if start < last_end:
            skipped_ambiguous += 1
            modules_removed -= 1
            continue
        checked_spans.append((start, end))
        last_end = end

    updated = text
    for start, end in reversed(checked_spans):
        updated = updated[:start] + updated[end:]

    return TextCleanupResult(
        updated,
        modules_removed=max(0, modules_removed),
        skipped_ambiguous=skipped_ambiguous,
        skipped_missing=skipped_missing,
    )


def _prepare_file_plan(
    path: Path,
    relative_path: str,
    module_names: tuple[str, ...],
) -> ProfileSettingsFilePlan:
    try:
        original_bytes = path.read_bytes()
    except OSError as exc:
        raise ProfileCleanupError(PROFILE_CLEANUP_UNAVAILABLE_MESSAGE) from exc

    if b"\x00" in original_bytes:
        return ProfileSettingsFilePlan(
            path=path,
            relative_path=relative_path,
            original_bytes=original_bytes,
            original_text="",
            updated_text="",
            modules_removed=0,
            skipped_ambiguous=max(1, len(module_names)),
            skipped_missing=0,
        )
    try:
        original_text = original_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return ProfileSettingsFilePlan(
            path=path,
            relative_path=relative_path,
            original_bytes=original_bytes,
            original_text="",
            updated_text="",
            modules_removed=0,
            skipped_ambiguous=max(1, len(module_names)),
            skipped_missing=0,
        )

    cleanup = remove_known_module_blocks(original_text, module_names)
    if cleanup.modules_removed and not _text_has_balanced_braces(cleanup.text):
        cleanup = TextCleanupResult(
            original_text,
            modules_removed=0,
            skipped_ambiguous=cleanup.skipped_ambiguous + cleanup.modules_removed,
            skipped_missing=cleanup.skipped_missing,
        )

    return ProfileSettingsFilePlan(
        path=path,
        relative_path=relative_path,
        original_bytes=original_bytes,
        original_text=original_text,
        updated_text=cleanup.text,
        modules_removed=cleanup.modules_removed,
        skipped_ambiguous=cleanup.skipped_ambiguous,
        skipped_missing=cleanup.skipped_missing,
    )


def _counts_for_file_plans(file_plans: Iterable[ProfileSettingsFilePlan]) -> ProfileCleanupCounts:
    plans = tuple(file_plans)
    return ProfileCleanupCounts(
        files_considered=len(plans),
        files_changed=sum(1 for plan in plans if plan.changed),
        modules_removed=sum(plan.modules_removed for plan in plans if plan.changed),
        skipped_ambiguous=sum(plan.skipped_ambiguous for plan in plans),
        skipped_missing=sum(plan.skipped_missing for plan in plans),
    )


def prepare_profile_settings_cleanup(
    config_path: Path | str,
    *,
    known_modules: Mapping[str, tuple[str, ...]] = mods_diagnostics.KNOWN_DISABLED_MOD_MODULE_NAMES,
) -> ProfileCleanupPlan:
    """Build a cleanup plan without mutating profile settings files."""
    path = Path(config_path)
    if not path.is_file() or path.is_symlink():
        raise ProfileCleanupError(PROFILE_CLEANUP_UNAVAILABLE_MESSAGE)

    module_names = _known_disabled_modules(path, known_modules)
    if not module_names:
        return ProfileCleanupPlan(path, (), ProfileCleanupCounts())

    config_dir = path.parent
    file_plans: list[ProfileSettingsFilePlan] = []
    for relative_path in mods_diagnostics.PROFILE_SETTINGS_CANDIDATES:
        candidate, safe_relative_path = _candidate_path(config_dir, relative_path)
        try:
            exists = candidate.exists()
        except OSError as exc:
            raise ProfileCleanupError(PROFILE_CLEANUP_UNAVAILABLE_MESSAGE) from exc
        if not exists:
            continue
        if not candidate.is_file() or candidate.is_symlink():
            raise ProfileCleanupError(PROFILE_CLEANUP_UNAVAILABLE_MESSAGE)
        file_plans.append(_prepare_file_plan(candidate, safe_relative_path, module_names))

    return ProfileCleanupPlan(path, tuple(file_plans), _counts_for_file_plans(file_plans))


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


def _backup_root(config_path: Path) -> Path:
    if config_path.parent.name == "config":
        instance_root = config_path.parent.parent
    else:
        instance_root = config_path.parent
    return instance_root / "backups" / BACKUP_SUBDIR


def _safe_backup_name(relative_path: str) -> str:
    safe = "".join(char if char.isalnum() or char in "._-" else "_" for char in relative_path)
    return safe.strip("._") or "profile-settings"


def create_profile_settings_backup(
    file_plan: ProfileSettingsFilePlan,
    *,
    config_path: Path,
) -> Path:
    """Write a backup of the original profile settings bytes before publishing."""
    backup_root = _backup_root(config_path)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_name = _safe_backup_name(file_plan.relative_path)
    backup_path = backup_root / f"{safe_name}.before-web-profile-cleanup-{timestamp}.bak"
    suffix = 1
    while backup_path.exists():
        backup_path = backup_root / (
            f"{safe_name}.before-web-profile-cleanup-{timestamp}.{suffix}.bak"
        )
        suffix += 1

    try:
        backup_root.mkdir(parents=True, exist_ok=True)
        with backup_path.open("wb") as handle:
            handle.write(file_plan.original_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            shutil.copystat(file_plan.path, backup_path, follow_symlinks=False)
        except OSError:
            pass
        _fsync_file(backup_path)
        _fsync_directory(backup_root)
    except OSError as exc:
        try:
            backup_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise ProfileCleanupError("Failed to create profile settings backup.") from exc
    return backup_path


def _stage_profile_settings_update(file_plan: ProfileSettingsFilePlan) -> Path:
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=file_plan.path.parent,
            prefix=f".armactl-profile-cleanup-{file_plan.path.name}-",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(file_plan.updated_text.encode("utf-8"))
            temp_file.flush()
            os.fsync(temp_file.fileno())
    except OSError as exc:
        if temp_path is not None:
            _cleanup_temp_path(temp_path)
        raise ProfileCleanupError(PROFILE_CLEANUP_UNAVAILABLE_MESSAGE) from exc
    assert temp_path is not None
    return temp_path


def _cleanup_temp_path(temp_path: Path) -> None:
    try:
        temp_path.unlink(missing_ok=True)
    except OSError:
        return


def _publish_staged_update(
    file_plan: ProfileSettingsFilePlan,
    staged_path: Path,
    backup_path: Path,
) -> None:
    if not backup_path.is_file():
        raise ProfileCleanupError(PROFILE_CLEANUP_UNAVAILABLE_MESSAGE)
    if not file_plan.path.is_file() or file_plan.path.is_symlink():
        raise ProfileCleanupError(PROFILE_CLEANUP_UNAVAILABLE_MESSAGE)
    try:
        current_bytes = file_plan.path.read_bytes()
    except OSError as exc:
        raise ProfileCleanupError(PROFILE_CLEANUP_UNAVAILABLE_MESSAGE) from exc
    if current_bytes != file_plan.original_bytes:
        raise ProfileCleanupError(PROFILE_CLEANUP_UNAVAILABLE_MESSAGE)
    if not _text_has_balanced_braces(file_plan.updated_text):
        raise ProfileCleanupError(PROFILE_CLEANUP_UNAVAILABLE_MESSAGE)
    try:
        os.replace(staged_path, file_plan.path)
    except OSError as exc:
        raise ProfileCleanupError(PROFILE_CLEANUP_UNAVAILABLE_MESSAGE) from exc
    _fsync_directory(file_plan.path.parent)


def _apply_file_plan(
    file_plan: ProfileSettingsFilePlan,
    *,
    config_path: Path,
) -> PublishedProfileSettingsUpdate:
    backup_path = create_profile_settings_backup(file_plan, config_path=config_path)
    staged_path = _stage_profile_settings_update(file_plan)
    try:
        _publish_staged_update(file_plan, staged_path, backup_path)
    finally:
        _cleanup_temp_path(staged_path)
    return PublishedProfileSettingsUpdate(file_plan=file_plan, backup_path=backup_path)


def _counts_for_published(
    plan: ProfileCleanupPlan,
    published: tuple[PublishedProfileSettingsUpdate, ...],
) -> ProfileCleanupCounts:
    return replace(
        plan.counts,
        files_changed=len(published),
        modules_removed=sum(item.file_plan.modules_removed for item in published),
    )


def _message_for_completed_counts(counts: ProfileCleanupCounts) -> tuple[bool, str, int]:
    if counts.modules_removed > 0 and counts.skipped_ambiguous > 0:
        return False, PROFILE_CLEANUP_PARTIAL_MESSAGE, 1
    if counts.modules_removed > 0:
        return True, PROFILE_CLEANUP_SUCCESS_MESSAGE, 0
    if counts.skipped_ambiguous > 0:
        return False, PROFILE_CLEANUP_SKIPPED_MESSAGE, 1
    return True, PROFILE_CLEANUP_NOOP_MESSAGE, 0


def _apply_cleanup_plan(
    plan: ProfileCleanupPlan,
    *,
    instance: str,
) -> tuple[ModActionResult, tuple[PublishedProfileSettingsUpdate, ...]]:
    published: list[PublishedProfileSettingsUpdate] = []
    try:
        for file_plan in plan.changed_file_plans:
            published.append(_apply_file_plan(file_plan, config_path=plan.config_path))
    except Exception:  # noqa: BLE001 - return controlled partial-change state.
        counts = _counts_for_published(plan, tuple(published))
        changed = bool(published)
        return (
            _result(
                instance=instance,
                success=False,
                changed=changed,
                message=(
                    PROFILE_CLEANUP_PARTIAL_MESSAGE if changed else PROFILE_CLEANUP_FAILED_MESSAGE
                ),
                counts=counts,
                exit_code=1,
                backend_success=False,
                backend_message="Profile settings cleanup did not complete cleanly.",
            ),
            tuple(published),
        )

    counts = _counts_for_published(plan, tuple(published))
    success, message, exit_code = _message_for_completed_counts(counts)
    return (
        _result(
            instance=instance,
            success=success,
            changed=bool(published),
            message=message,
            counts=counts,
            exit_code=exit_code,
        ),
        tuple(published),
    )


def _fingerprint_for_published(
    published: tuple[PublishedProfileSettingsUpdate, ...],
    *,
    use_updated: bool,
) -> str:
    payload = [
        {
            "source": item.file_plan.relative_path,
            "sha256": (
                item.file_plan.updated_sha256
                if use_updated
                else item.file_plan.original_sha256
            ),
        }
        for item in published
    ]
    return pending_work.safe_state_fingerprint(
        {"scope": ACTION_PROFILE_SETTINGS_CLEANUP, "files": payload}
    )


def _mark_restart_pending(
    result: ModActionResult,
    published: tuple[PublishedProfileSettingsUpdate, ...],
    *,
    db_path: Path | None,
    username: str,
) -> ModActionResult:
    if db_path is None or not result.changed:
        return result

    baseline_fingerprint = _fingerprint_for_published(published, use_updated=False)
    current_fingerprint = _fingerprint_for_published(published, use_updated=True)
    write_result = mutation_recovery.mark_restart_pending_for_mutation(
        mutation_recovery.RestartPendingRecovery(
            db_path=db_path,
            instance=result.instance,
            kind=pending_work.KIND_MODS,
            source_action=ACTION_PROFILE_SETTINGS_CLEANUP,
            source_path="/mods",
            title="Mod changes",
            username=username,
            details="profile settings references",
            baseline_fingerprint=baseline_fingerprint,
            current_fingerprint=current_fingerprint,
        )
    )

    updated = replace(
        result,
        pending_work_warning=write_result.warning,
        pending_work_error=write_result.error,
    )
    if write_result.error:
        return replace(
            updated,
            success=False,
            message=PROFILE_CLEANUP_PENDING_FAILED_MESSAGE,
            exit_code=1,
            backend_success=result.success,
            backend_message=result.message,
        )
    return updated


def _audit_details(phase: str, counts: ProfileCleanupCounts) -> dict[str, object]:
    return {"phase": phase, **counts.details()}


def _audit_cleanup(
    *,
    audit_log_path: Path,
    username: str,
    instance: str,
    success: bool,
    message: str,
    exit_code: int,
    phase: str,
    counts: ProfileCleanupCounts,
) -> None:
    append_audit_event(
        audit_log_path,
        username=username,
        action=ACTION_PROFILE_SETTINGS_CLEANUP,
        instance=instance,
        target=TARGET_PROFILE_SETTINGS,
        success=success,
        message=message,
        exit_code=exit_code,
        details=_audit_details(phase, counts),
    )


def _intent_audit_failure(instance: str, counts: ProfileCleanupCounts) -> ModActionResult:
    return _result(
        instance=instance,
        success=False,
        changed=False,
        message=PROFILE_CLEANUP_INTENT_AUDIT_FAILED_MESSAGE,
        counts=counts,
        exit_code=1,
        audit_written=False,
        intent_audited=False,
        backend_success=False,
    )


def _counts_from_result(result: ModActionResult) -> ProfileCleanupCounts:
    return ProfileCleanupCounts(
        files_considered=int(result.details.get("files_considered", 0)),
        files_changed=int(result.details.get("files_changed", 0)),
        modules_removed=int(result.details.get("modules_removed", 0)),
        skipped_ambiguous=int(result.details.get("skipped_ambiguous", 0)),
        skipped_missing=int(result.details.get("skipped_missing", 0)),
    )


def cleanup_profile_settings_and_audit(
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    audit_log_path: Path,
    username: str,
    db_path: Path | None = None,
) -> ModActionResult:
    """Clean stale disabled-mod profile settings references with safe audit output."""
    normalized_instance = instance or paths.DEFAULT_INSTANCE_NAME
    try:
        config_path = _config_path(normalized_instance)
        plan = prepare_profile_settings_cleanup(config_path)
    except ProfileCleanupError:
        return _result(
            instance=normalized_instance,
            success=False,
            changed=False,
            message=PROFILE_CLEANUP_UNAVAILABLE_MESSAGE,
            counts=ProfileCleanupCounts(),
            exit_code=1,
            backend_success=False,
        )

    try:
        _audit_cleanup(
            audit_log_path=audit_log_path,
            username=username,
            instance=normalized_instance,
            success=True,
            message="Profile settings cleanup requested.",
            exit_code=0,
            phase="intent",
            counts=plan.counts,
        )
    except AuditLogError:
        return _intent_audit_failure(normalized_instance, plan.counts)

    result, published = _apply_cleanup_plan(plan, instance=normalized_instance)
    result = _mark_restart_pending(
        result,
        published,
        db_path=db_path,
        username=username,
    )

    try:
        _audit_cleanup(
            audit_log_path=audit_log_path,
            username=username,
            instance=normalized_instance,
            success=result.success,
            message=result.message,
            exit_code=result.exit_code,
            phase="outcome",
            counts=_counts_from_result(result),
        )
    except AuditLogError:
        return replace(
            result,
            success=False,
            message=PROFILE_CLEANUP_OUTCOME_AUDIT_FAILED_MESSAGE,
            exit_code=1,
            backend_success=result.success,
            backend_message=result.message,
            audit_written=False,
        )
    return result
