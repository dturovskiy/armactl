"""Persistent pending operator work for web-visible runtime state."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from armactl import paths
from armactl.redaction import redact_sensitive_text
from armactl.web.runtime.db import ensure_web_db

KIND_CONFIG = "config"
KIND_ADMINS = "admins"
KIND_MODS = "mods"
KIND_SCHEDULE = "schedule"
RESOLUTION_RESTART_GAME_SERVER = "restart game server"
FALLBACK_PENDING_WORK_FILENAME = "pending-work-fallback.json"
FALLBACK_PENDING_WORK_FILE_MODE = 0o600
PENDING_WORK_FALLBACK_WARNING = (
    "Restart-required work was saved to fallback storage because normal pending storage failed."
)
PENDING_WORK_STORAGE_FAILED_MESSAGE = (
    "Changes were saved and require restart, but pending restart storage failed."
)
PENDING_WORK_CLEAR_WARNING = (
    "Restart completed, but some restart-related pending work could not be cleared."
)
DEFAULT_PENDING_WORK_LIMIT = 50
MAX_TEXT_LENGTH = 500
MAX_SHORT_TEXT_LENGTH = 160

_SOURCE_PATHS = {
    KIND_CONFIG: "/config",
    KIND_ADMINS: "/admins",
    KIND_MODS: "/mods",
    KIND_SCHEDULE: "/schedule",
}
_KIND_LABELS = {
    KIND_CONFIG: "Config changes",
    KIND_ADMINS: "Admin changes",
    KIND_MODS: "Mod changes",
    KIND_SCHEDULE: "Schedule changes",
}
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?im)\b((?:ARMACTL_[A-Z0-9_]*SECRET|session_secret|secret|api_key)\s*[=:]\s*)([^\s,;]+)"
)
_FINGERPRINT_RE = re.compile(r"^[a-z0-9_.:-]{1,128}$")


class PendingWorkFallbackError(RuntimeError):
    """Raised when fallback pending-work storage cannot be written."""


@dataclass(frozen=True)
class PendingWorkItem:
    """One saved operator action waiting for a human resolution step."""

    id: int
    instance: str
    kind: str
    source_path: str
    source_action: str
    title: str
    details: str
    resolution_action: str
    created_at: str
    updated_at: str
    created_by_username: str
    baseline_fingerprint: str = ""
    current_fingerprint: str = ""
    storage: str = "db"

    @property
    def kind_label(self) -> str:
        return _KIND_LABELS.get(self.kind, "Saved changes")

    @property
    def resolution_action_label(self) -> str:
        if self.resolution_action == RESOLUTION_RESTART_GAME_SERVER:
            return "Restart game server"
        return self.resolution_action

    @property
    def is_fallback(self) -> bool:
        return self.storage == "fallback"

    @property
    def storage_label(self) -> str:
        if self.is_fallback:
            return "Fallback storage"
        return "Primary storage"


@dataclass(frozen=True)
class PendingWorkClearResult:
    db_cleared: int = 0
    legacy_cleared: int = 0
    fallback_cleared: int = 0
    errors: tuple[str, ...] = ()

    @property
    def total_cleared(self) -> int:
        return self.db_cleared + self.legacy_cleared + self.fallback_cleared

    @property
    def warning(self) -> str:
        return PENDING_WORK_CLEAR_WARNING if self.errors else ""


@dataclass(frozen=True)
class PendingWorkWriteResult:
    warning: str = ""
    error: str = ""


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_text(value: object, *, max_length: int = MAX_TEXT_LENGTH) -> str:
    text = redact_sensitive_text(value)
    text = _SECRET_ASSIGNMENT_RE.sub(r"\1***", text)
    text = text.replace("\r", " ").replace("\n", " ")
    text = "".join(char if ord(char) >= 32 else " " for char in text)
    text = text.strip()
    if len(text) > max_length:
        return f"{text[:max_length]}..."
    return text


def _normalize_instance(instance: str) -> str:
    return (instance or paths.DEFAULT_INSTANCE_NAME).strip() or paths.DEFAULT_INSTANCE_NAME


def _default_source_path(kind: str) -> str:
    return _SOURCE_PATHS.get(kind, "/dashboard")


def _default_title(kind: str) -> str:
    return _KIND_LABELS.get(kind, "Saved changes")


def _safe_source_path(value: object) -> str:
    source_path = _safe_text(value, max_length=MAX_SHORT_TEXT_LENGTH)
    if not source_path.startswith("/") or source_path.startswith("//"):
        return "/dashboard"
    return source_path


def _safe_fingerprint(value: object) -> str:
    fingerprint = _safe_text(value, max_length=128).lower()
    if not fingerprint or _FINGERPRINT_RE.fullmatch(fingerprint) is None:
        return ""
    return fingerprint


def safe_state_fingerprint(payload: object) -> str:
    canonical = json.dumps(
        payload,
        default=str,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _connect(db_path: Path) -> sqlite3.Connection:
    ensure_web_db(db_path)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def fallback_pending_work_path(db_path: Path) -> Path:
    """Return the sidecar path used when web.db cannot store pending work."""
    return Path(db_path).with_name(FALLBACK_PENDING_WORK_FILENAME)


def _row_to_item(row: sqlite3.Row) -> PendingWorkItem:
    return PendingWorkItem(
        id=row["id"],
        instance=row["instance"],
        kind=row["kind"],
        source_path=row["source_path"],
        source_action=row["source_action"],
        title=row["title"],
        details=row["details"],
        resolution_action=row["resolution_action"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        created_by_username=row["created_by_username"],
        baseline_fingerprint=_safe_fingerprint(row["baseline_fingerprint"]),
        current_fingerprint=_safe_fingerprint(row["current_fingerprint"]),
    )


def _pending_work_key(item: PendingWorkItem) -> tuple[str, str, str]:
    return (item.instance, item.kind, item.resolution_action)


def _read_fallback_records(sidecar_path: Path) -> list[dict[str, object]]:
    if not sidecar_path.is_file():
        return []
    try:
        payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    records = payload.get("items", []) if isinstance(payload, dict) else []
    return [record for record in records if isinstance(record, dict)]


def _fallback_record_to_item(record: dict[str, object], index: int) -> PendingWorkItem | None:
    kind = _safe_text(record.get("kind"), max_length=80) or "saved"
    resolution_action = (
        _safe_text(record.get("resolution_action"), max_length=MAX_SHORT_TEXT_LENGTH)
        or RESOLUTION_RESTART_GAME_SERVER
    )
    created_at = _safe_text(record.get("created_at"), max_length=MAX_SHORT_TEXT_LENGTH)
    updated_at = _safe_text(record.get("updated_at"), max_length=MAX_SHORT_TEXT_LENGTH)
    timestamp = updated_at or created_at or _utc_timestamp()
    return PendingWorkItem(
        id=-(index + 1),
        instance=_normalize_instance(str(record.get("instance") or "")),
        kind=kind,
        source_path=_safe_source_path(record.get("source_path") or _default_source_path(kind)),
        source_action=_safe_text(record.get("source_action"), max_length=MAX_SHORT_TEXT_LENGTH),
        title=(
            _safe_text(record.get("title"), max_length=MAX_SHORT_TEXT_LENGTH)
            or _default_title(kind)
        ),
        details=_safe_text(record.get("details")),
        resolution_action=resolution_action,
        created_at=created_at or timestamp,
        updated_at=updated_at or timestamp,
        created_by_username=(
            _safe_text(record.get("created_by_username"), max_length=MAX_SHORT_TEXT_LENGTH)
            or "unknown"
        ),
        baseline_fingerprint=_safe_fingerprint(record.get("baseline_fingerprint")),
        current_fingerprint=_safe_fingerprint(record.get("current_fingerprint")),
        storage="fallback",
    )


def _fallback_record_from_item(item: PendingWorkItem) -> dict[str, object]:
    return {
        "instance": item.instance,
        "kind": item.kind,
        "source_path": item.source_path,
        "source_action": item.source_action,
        "title": item.title,
        "details": item.details,
        "resolution_action": item.resolution_action,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "created_by_username": item.created_by_username,
        "baseline_fingerprint": item.baseline_fingerprint,
        "current_fingerprint": item.current_fingerprint,
    }


def _fallback_record(
    *,
    kind: str,
    source_path: str,
    source_action: str,
    title: str,
    username: str,
    details: object,
    instance: str,
    resolution_action: str,
    created_at: str,
    updated_at: str,
    baseline_fingerprint: str = "",
    current_fingerprint: str = "",
) -> dict[str, object]:
    safe_kind = _safe_text(kind, max_length=80) or "saved"
    return {
        "instance": _normalize_instance(instance),
        "kind": safe_kind,
        "source_path": _safe_source_path(source_path),
        "source_action": _safe_text(source_action, max_length=MAX_SHORT_TEXT_LENGTH),
        "title": _safe_text(title, max_length=MAX_SHORT_TEXT_LENGTH) or _default_title(safe_kind),
        "details": _safe_text(details),
        "resolution_action": (
            _safe_text(resolution_action, max_length=MAX_SHORT_TEXT_LENGTH)
            or RESOLUTION_RESTART_GAME_SERVER
        ),
        "created_at": created_at,
        "updated_at": updated_at,
        "created_by_username": (
            _safe_text(username, max_length=MAX_SHORT_TEXT_LENGTH) or "unknown"
        ),
        "baseline_fingerprint": _safe_fingerprint(baseline_fingerprint),
        "current_fingerprint": _safe_fingerprint(current_fingerprint),
    }


def _write_fallback_records(sidecar_path: Path, records: list[dict[str, object]]) -> None:
    try:
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        if not records:
            sidecar_path.unlink(missing_ok=True)
            return
        payload = {"version": 1, "items": records}
        tmp_path = sidecar_path.with_name(f"{sidecar_path.name}.tmp")
        fd = os.open(
            tmp_path,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            FALLBACK_PENDING_WORK_FILE_MODE,
        )
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n")
        os.replace(tmp_path, sidecar_path)
        sidecar_path.chmod(FALLBACK_PENDING_WORK_FILE_MODE)
    except OSError as exc:
        try:
            tmp_path.unlink(missing_ok=True)
        except (NameError, OSError):
            pass
        raise PendingWorkFallbackError("Failed to write fallback pending work.") from exc


def upsert_pending_work(
    db_path: Path,
    *,
    kind: str,
    source_path: str,
    title: str,
    username: str,
    details: object = "",
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    source_action: str = "",
    resolution_action: str = RESOLUTION_RESTART_GAME_SERVER,
    baseline_fingerprint: str = "",
    current_fingerprint: str = "",
) -> PendingWorkItem:
    """Create or update one pending work item without erasing other categories."""
    normalized_instance = _normalize_instance(instance)
    safe_kind = _safe_text(kind, max_length=80) or "saved"
    safe_source_path = _safe_source_path(source_path)
    safe_source_action = _safe_text(source_action, max_length=MAX_SHORT_TEXT_LENGTH)
    safe_title = _safe_text(title, max_length=MAX_SHORT_TEXT_LENGTH) or "Saved changes"
    safe_username = _safe_text(username, max_length=MAX_SHORT_TEXT_LENGTH) or "unknown"
    safe_details = _safe_text(details)
    safe_resolution = (
        _safe_text(resolution_action, max_length=MAX_SHORT_TEXT_LENGTH)
        or RESOLUTION_RESTART_GAME_SERVER
    )
    safe_baseline_fingerprint = _safe_fingerprint(baseline_fingerprint)
    safe_current_fingerprint = _safe_fingerprint(current_fingerprint)
    now = _utc_timestamp()

    with _connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO web_pending_work(
                instance,
                kind,
                source_path,
                source_action,
                title,
                details,
                resolution_action,
                created_at,
                updated_at,
                created_by_username,
                baseline_fingerprint,
                current_fingerprint
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(instance, kind, resolution_action) DO UPDATE SET
                source_path = excluded.source_path,
                source_action = excluded.source_action,
                title = excluded.title,
                details = excluded.details,
                updated_at = excluded.updated_at,
                created_by_username = excluded.created_by_username,
                baseline_fingerprint = CASE
                    WHEN excluded.baseline_fingerprint != "" THEN excluded.baseline_fingerprint
                    ELSE web_pending_work.baseline_fingerprint
                END,
                current_fingerprint = CASE
                    WHEN excluded.current_fingerprint != "" THEN excluded.current_fingerprint
                    ELSE web_pending_work.current_fingerprint
                END
            """,
            (
                normalized_instance,
                safe_kind,
                safe_source_path,
                safe_source_action,
                safe_title,
                safe_details,
                safe_resolution,
                now,
                now,
                safe_username,
                safe_baseline_fingerprint,
                safe_current_fingerprint,
            ),
        )

    item = get_pending_work(
        db_path,
        instance=normalized_instance,
        kind=safe_kind,
        resolution_action=safe_resolution,
    )
    assert item is not None
    return item


def mark_restart_pending(
    db_path: Path,
    *,
    kind: str,
    username: str,
    details: object = "",
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    source_action: str = "",
    source_path: str | None = None,
    title: str | None = None,
    baseline_fingerprint: str = "",
    current_fingerprint: str = "",
) -> PendingWorkItem:
    """Record saved work that needs a game server restart to apply."""
    return upsert_pending_work(
        db_path,
        kind=kind,
        source_path=source_path or _default_source_path(kind),
        source_action=source_action,
        title=title or _default_title(kind),
        username=username,
        details=details,
        instance=instance,
        resolution_action=RESOLUTION_RESTART_GAME_SERVER,
        baseline_fingerprint=baseline_fingerprint,
        current_fingerprint=current_fingerprint,
    )


def upsert_fallback_pending_work(
    db_path: Path,
    *,
    kind: str,
    source_path: str,
    title: str,
    username: str,
    details: object = "",
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    source_action: str = "",
    resolution_action: str = RESOLUTION_RESTART_GAME_SERVER,
    baseline_fingerprint: str = "",
    current_fingerprint: str = "",
) -> PendingWorkItem:
    """Create or update fallback pending work without relying on web.db."""
    sidecar_path = fallback_pending_work_path(db_path)
    now = _utc_timestamp()
    candidate = _fallback_record(
        kind=kind,
        source_path=source_path,
        source_action=source_action,
        title=title,
        username=username,
        details=details,
        instance=instance,
        resolution_action=resolution_action,
        created_at=now,
        updated_at=now,
        baseline_fingerprint=baseline_fingerprint,
        current_fingerprint=current_fingerprint,
    )
    candidate_key = (
        str(candidate["instance"]),
        str(candidate["kind"]),
        str(candidate["resolution_action"]),
    )
    records: list[dict[str, object]] = []
    replaced = False
    for index, raw_record in enumerate(_read_fallback_records(sidecar_path)):
        item = _fallback_record_to_item(raw_record, index)
        if item is None:
            continue
        record = _fallback_record_from_item(item)
        if _pending_work_key(item) == candidate_key:
            candidate["created_at"] = item.created_at
            if not str(candidate.get("baseline_fingerprint") or ""):
                candidate["baseline_fingerprint"] = item.baseline_fingerprint
            if not str(candidate.get("current_fingerprint") or ""):
                candidate["current_fingerprint"] = item.current_fingerprint
            record = candidate
            replaced = True
        records.append(record)
    if not replaced:
        records.append(candidate)
    _write_fallback_records(sidecar_path, records)
    item = get_fallback_pending_work(
        db_path,
        instance=str(candidate["instance"]),
        kind=str(candidate["kind"]),
        resolution_action=str(candidate["resolution_action"]),
    )
    assert item is not None
    return item


def mark_restart_pending_fallback(
    db_path: Path,
    *,
    kind: str,
    username: str,
    details: object = "",
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    source_action: str = "",
    source_path: str | None = None,
    title: str | None = None,
    baseline_fingerprint: str = "",
    current_fingerprint: str = "",
) -> PendingWorkItem:
    """Record restart-required work in a private sidecar fallback file."""
    return upsert_fallback_pending_work(
        db_path,
        kind=kind,
        source_path=source_path or _default_source_path(kind),
        source_action=source_action,
        title=title or _default_title(kind),
        username=username,
        details=details,
        instance=instance,
        resolution_action=RESOLUTION_RESTART_GAME_SERVER,
        baseline_fingerprint=baseline_fingerprint,
        current_fingerprint=current_fingerprint,
    )


def mark_restart_pending_safely(
    db_path: Path,
    *,
    kind: str,
    username: str,
    details: object = "",
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    source_action: str = "",
    source_path: str | None = None,
    title: str | None = None,
    baseline_fingerprint: str = "",
    current_fingerprint: str = "",
) -> tuple[PendingWorkItem, bool]:
    """Record restart-required work, falling back to sidecar storage if web.db fails."""
    try:
        return (
            mark_restart_pending(
                db_path,
                kind=kind,
                username=username,
                details=details,
                instance=instance,
                source_action=source_action,
                source_path=source_path,
                title=title,
                baseline_fingerprint=baseline_fingerprint,
                current_fingerprint=current_fingerprint,
            ),
            False,
        )
    except Exception:  # noqa: BLE001 - web.db failure falls back to private sidecar.
        try:
            return (
                mark_restart_pending_fallback(
                    db_path,
                    kind=kind,
                    username=username,
                    details=details,
                    instance=instance,
                    source_action=source_action,
                    source_path=source_path,
                    title=title,
                    baseline_fingerprint=baseline_fingerprint,
                    current_fingerprint=current_fingerprint,
                ),
                True,
            )
        except Exception as exc:  # noqa: BLE001 - both stores failed.
            raise PendingWorkFallbackError(PENDING_WORK_STORAGE_FAILED_MESSAGE) from exc


def mark_restart_pending_for_service(
    db_path: Path,
    *,
    kind: str,
    username: str,
    details: object = "",
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    source_action: str = "",
    source_path: str | None = None,
    title: str | None = None,
) -> PendingWorkWriteResult:
    """Record restart-required work and return operator-facing status text."""
    try:
        _item, fallback_used = mark_restart_pending_safely(
            db_path,
            kind=kind,
            username=username,
            details=details,
            instance=instance,
            source_action=source_action,
            source_path=source_path,
            title=title,
        )
    except PendingWorkFallbackError as exc:
        return PendingWorkWriteResult(error=str(exc))
    return PendingWorkWriteResult(
        warning=PENDING_WORK_FALLBACK_WARNING if fallback_used else ""
    )


def _existing_restart_pending_item(
    db_path: Path,
    *,
    kind: str,
    instance: str,
) -> tuple[PendingWorkItem | None, Exception | None]:
    db_error: Exception | None = None
    normal_item: PendingWorkItem | None = None
    try:
        normal_item = get_pending_work(
            db_path,
            kind=kind,
            instance=instance,
            resolution_action=RESOLUTION_RESTART_GAME_SERVER,
        )
    except Exception as exc:  # noqa: BLE001 - callers can fall back to sidecar state.
        db_error = exc

    fallback_item: PendingWorkItem | None = None
    try:
        fallback_item = get_fallback_pending_work(
            db_path,
            kind=kind,
            instance=instance,
            resolution_action=RESOLUTION_RESTART_GAME_SERVER,
        )
    except Exception:  # noqa: BLE001 - fallback read failure should not hide DB state.
        fallback_item = None
    return normal_item or fallback_item, db_error


def clear_restart_pending_kind_safely(
    db_path: Path,
    *,
    kind: str,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> PendingWorkClearResult:
    normalized_instance = _normalize_instance(instance)
    safe_kind = _safe_text(kind, max_length=80) or "saved"
    db_cleared = 0
    fallback_cleared = 0
    errors: list[str] = []
    try:
        db_cleared = clear_pending_work(
            db_path,
            instance=normalized_instance,
            resolution_action=RESOLUTION_RESTART_GAME_SERVER,
            kind=safe_kind,
        )
    except Exception as exc:  # noqa: BLE001 - caller reports a controlled warning.
        errors.append(_safe_text(exc))
    try:
        fallback_cleared = clear_restart_pending_fallback(
            db_path,
            instance=normalized_instance,
            kind=safe_kind,
        )
    except Exception as exc:  # noqa: BLE001 - caller reports a controlled warning.
        errors.append(_safe_text(exc))
    return PendingWorkClearResult(
        db_cleared=db_cleared,
        fallback_cleared=fallback_cleared,
        errors=tuple(errors),
    )


def mark_restart_pending_for_state(
    db_path: Path,
    *,
    kind: str,
    username: str,
    baseline_fingerprint: str,
    current_fingerprint: str,
    details: object = "",
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    source_action: str = "",
    source_path: str | None = None,
    title: str | None = None,
) -> PendingWorkWriteResult:
    safe_kind = _safe_text(kind, max_length=80) or "saved"
    safe_baseline = _safe_fingerprint(baseline_fingerprint)
    safe_current = _safe_fingerprint(current_fingerprint)
    if not safe_baseline or not safe_current:
        return mark_restart_pending_for_service(
            db_path,
            kind=safe_kind,
            username=username,
            details=details,
            instance=instance,
            source_action=source_action,
            source_path=source_path,
            title=title,
        )

    normalized_instance = _normalize_instance(instance)
    existing_item, _db_read_error = _existing_restart_pending_item(
        db_path,
        kind=safe_kind,
        instance=normalized_instance,
    )
    effective_baseline = (
        _safe_fingerprint(existing_item.baseline_fingerprint)
        if existing_item is not None
        else ""
    ) or safe_baseline

    if safe_current == effective_baseline:
        clear_result = clear_restart_pending_kind_safely(
            db_path,
            kind=safe_kind,
            instance=normalized_instance,
        )
        return PendingWorkWriteResult(warning=clear_result.warning)

    try:
        mark_restart_pending(
            db_path,
            kind=safe_kind,
            source_action=source_action,
            source_path=source_path or _default_source_path(safe_kind),
            title=title or _default_title(safe_kind),
            username=username,
            details=details,
            instance=normalized_instance,
            baseline_fingerprint=effective_baseline,
            current_fingerprint=safe_current,
        )
        try:
            clear_restart_pending_fallback(
                db_path,
                instance=normalized_instance,
                kind=safe_kind,
            )
        except Exception:  # noqa: BLE001 - DB marker is authoritative, fallback is suppressed.
            return PendingWorkWriteResult(warning=PENDING_WORK_CLEAR_WARNING)
        return PendingWorkWriteResult()
    except Exception:  # noqa: BLE001 - web.db failure falls back to private sidecar.
        try:
            mark_restart_pending_fallback(
                db_path,
                kind=safe_kind,
                source_action=source_action,
                source_path=source_path or _default_source_path(safe_kind),
                title=title or _default_title(safe_kind),
                username=username,
                details=details,
                instance=normalized_instance,
                baseline_fingerprint=effective_baseline,
                current_fingerprint=safe_current,
            )
        except Exception:  # noqa: BLE001 - both stores failed.
            return PendingWorkWriteResult(error=PENDING_WORK_STORAGE_FAILED_MESSAGE)
        return PendingWorkWriteResult(warning=PENDING_WORK_FALLBACK_WARNING)


def get_pending_work(
    db_path: Path,
    *,
    kind: str,
    resolution_action: str = RESOLUTION_RESTART_GAME_SERVER,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> PendingWorkItem | None:
    """Return one pending work item for an instance/category/resolution."""
    normalized_instance = _normalize_instance(instance)
    with _connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT id, instance, kind, source_path, source_action, title, details,
                   resolution_action, created_at, updated_at, created_by_username,
                   baseline_fingerprint, current_fingerprint
            FROM web_pending_work
            WHERE instance = ? AND kind = ? AND resolution_action = ?
            """,
            (normalized_instance, kind, resolution_action),
        ).fetchone()
    if row is None:
        return None
    return _row_to_item(row)


def list_pending_work(
    db_path: Path,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    limit: int = DEFAULT_PENDING_WORK_LIMIT,
) -> list[PendingWorkItem]:
    """List pending operator work newest-first for one instance."""
    normalized_instance = _normalize_instance(instance)
    bounded_limit = max(1, min(limit, DEFAULT_PENDING_WORK_LIMIT))
    with _connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT id, instance, kind, source_path, source_action, title, details,
                   resolution_action, created_at, updated_at, created_by_username,
                   baseline_fingerprint, current_fingerprint
            FROM web_pending_work
            WHERE instance = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (normalized_instance, bounded_limit),
        ).fetchall()
    return [_row_to_item(row) for row in rows]


def list_fallback_pending_work(
    db_path: Path,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    limit: int = DEFAULT_PENDING_WORK_LIMIT,
) -> list[PendingWorkItem]:
    """List fallback pending work from the private sidecar file."""
    normalized_instance = _normalize_instance(instance)
    bounded_limit = max(1, min(limit, DEFAULT_PENDING_WORK_LIMIT))
    items: list[PendingWorkItem] = []
    for index, raw_record in enumerate(_read_fallback_records(fallback_pending_work_path(db_path))):
        item = _fallback_record_to_item(raw_record, index)
        if item is not None and item.instance == normalized_instance:
            items.append(item)
    items.sort(key=lambda item: (item.updated_at, item.created_at, item.kind), reverse=True)
    return items[:bounded_limit]


def get_fallback_pending_work(
    db_path: Path,
    *,
    kind: str,
    resolution_action: str = RESOLUTION_RESTART_GAME_SERVER,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> PendingWorkItem | None:
    """Return one fallback pending work item, if present."""
    normalized_instance = _normalize_instance(instance)
    for item in list_fallback_pending_work(db_path, instance=normalized_instance):
        if item.kind == kind and item.resolution_action == resolution_action:
            return item
    return None


def list_pending_work_with_fallback(
    db_path: Path,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    limit: int = DEFAULT_PENDING_WORK_LIMIT,
) -> list[PendingWorkItem]:
    """List DB pending work plus non-duplicated fallback markers."""
    bounded_limit = max(1, min(limit, DEFAULT_PENDING_WORK_LIMIT))
    normal_items: list[PendingWorkItem]
    try:
        normal_items = list_pending_work(
            db_path,
            instance=instance,
            limit=DEFAULT_PENDING_WORK_LIMIT,
        )
    except Exception:  # noqa: BLE001 - listing degrades to fallback sidecar records.
        normal_items = []
    normal_keys = {_pending_work_key(item) for item in normal_items}
    fallback_items = [
        item
        for item in list_fallback_pending_work(
            db_path,
            instance=instance,
            limit=DEFAULT_PENDING_WORK_LIMIT,
        )
        if _pending_work_key(item) not in normal_keys
    ]
    items = [*normal_items, *fallback_items]
    items.sort(key=lambda item: (item.updated_at, item.created_at, item.kind), reverse=True)
    return items[:bounded_limit]


def clear_pending_work(
    db_path: Path,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    resolution_action: str | None = None,
    kind: str | None = None,
) -> int:
    normalized_instance = _normalize_instance(instance)
    safe_kind = _safe_text(kind, max_length=80) if kind is not None else ""
    with _connect(db_path) as connection:
        if resolution_action is None and not safe_kind:
            cursor = connection.execute(
                "DELETE FROM web_pending_work WHERE instance = ?",
                (normalized_instance,),
            )
        elif resolution_action is None:
            cursor = connection.execute(
                "DELETE FROM web_pending_work WHERE instance = ? AND kind = ?",
                (normalized_instance, safe_kind),
            )
        elif not safe_kind:
            cursor = connection.execute(
                "DELETE FROM web_pending_work WHERE instance = ? AND resolution_action = ?",
                (normalized_instance, resolution_action),
            )
        else:
            cursor = connection.execute(
                (
                    "DELETE FROM web_pending_work "
                    "WHERE instance = ? AND kind = ? AND resolution_action = ?"
                ),
                (normalized_instance, safe_kind, resolution_action),
            )
    return cursor.rowcount


def clear_restart_pending_fallback(
    db_path: Path,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    kind: str | None = None,
) -> int:
    normalized_instance = _normalize_instance(instance)
    safe_kind = _safe_text(kind, max_length=80) if kind is not None else ""
    sidecar_path = fallback_pending_work_path(db_path)
    remaining: list[dict[str, object]] = []
    cleared = 0
    for index, raw_record in enumerate(_read_fallback_records(sidecar_path)):
        item = _fallback_record_to_item(raw_record, index)
        if item is None:
            continue
        if (
            item.instance == normalized_instance
            and item.resolution_action == RESOLUTION_RESTART_GAME_SERVER
            and (not safe_kind or item.kind == safe_kind)
        ):
            cleared += 1
            continue
        remaining.append(_fallback_record_from_item(item))
    if cleared:
        _write_fallback_records(sidecar_path, remaining)
    return cleared


def clear_restart_pending_work_safely(db_path, *, instance=paths.DEFAULT_INSTANCE_NAME):
    normalized_instance = _normalize_instance(instance)
    db_cleared = 0
    legacy_cleared = 0
    fallback_cleared = 0
    errors: list[str] = []
    try:
        db_cleared = clear_pending_work(
            db_path,
            instance=normalized_instance,
            resolution_action=RESOLUTION_RESTART_GAME_SERVER,
        )
        with _connect(db_path) as connection:
            legacy_cursor = connection.execute(
                "DELETE FROM web_pending_restarts WHERE instance = ?",
                (normalized_instance,),
            )
            work_cursor = connection.execute(
                "DELETE FROM web_pending_work WHERE instance = ? AND resolution_action = ?",
                (normalized_instance, RESOLUTION_RESTART_GAME_SERVER),
            )
            db_cleared += work_cursor.rowcount
        legacy_cleared = legacy_cursor.rowcount
    except Exception as exc:
        errors.append(_safe_text(exc))
    try:
        fallback_cleared = clear_restart_pending_fallback(
            db_path,
            instance=normalized_instance,
        )
    except Exception as exc:
        errors.append(_safe_text(exc))
    return PendingWorkClearResult(
        db_cleared=db_cleared,
        legacy_cleared=legacy_cleared,
        fallback_cleared=fallback_cleared,
        errors=tuple(errors),
    )


def clear_restart_pending_work(db_path, *, instance=paths.DEFAULT_INSTANCE_NAME):
    clear_result = clear_restart_pending_work_safely(db_path, instance=instance)
    if clear_result.errors:
        raise PendingWorkFallbackError(PENDING_WORK_CLEAR_WARNING)
    return clear_result.total_cleared
