"""Bounded manual collector for parsed player log events."""

from __future__ import annotations

import hashlib
import os
import re
import stat as stat_module
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from armactl.player_log_events import PlayerLogEvent, parse_player_log_event
from armactl.web.services import player_registry
from armactl.web.services.player_identity import safe_player_text

DEFAULT_MAX_FILE_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_FILE_LINES = 100_000
PLAYER_LOG_COLLECTOR_LABEL_MAX_LENGTH = 160
_BINARY_SNIFF_BYTES = 4096
_IPV4_ADDRESS_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?\b")
_BRACKETED_IPV6_ADDRESS_RE = re.compile(r"\[[0-9A-Fa-f:.]{2,}\](?::\d{1,5})?")


@dataclass(frozen=True)
class PlayerLogCollectionError:
    """Controlled collector error safe for summaries and CLI output."""

    source: str
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "source": self.source,
            "code": self.code,
            "message": self.message,
        }


@dataclass(frozen=True)
class PlayerLogCollectionFileSummary:
    """Summary for one explicitly requested log file."""

    source: str
    status: str
    bytes_scanned: int = 0
    lines_scanned: int = 0
    matched_events: int = 0
    stored_events: int = 0
    duplicate_events: int = 0
    unmatched_lines: int = 0
    skipped_lines: int = 0
    limited: bool = False
    limit_reason: str | None = None
    errors: tuple[PlayerLogCollectionError, ...] = ()

    @property
    def error_count(self) -> int:
        return len(self.errors)

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "status": self.status,
            "bytes_scanned": self.bytes_scanned,
            "lines_scanned": self.lines_scanned,
            "matched_events": self.matched_events,
            "stored_events": self.stored_events,
            "duplicate_events": self.duplicate_events,
            "unmatched_lines": self.unmatched_lines,
            "skipped_lines": self.skipped_lines,
            "limited": self.limited,
            "limit_reason": self.limit_reason,
            "errors": [error.to_dict() for error in self.errors],
        }


@dataclass(frozen=True)
class PlayerLogCollectionSummary:
    """Structured result for one manual collector run."""

    dry_run: bool
    files_requested: int
    files_scanned: int
    files_skipped: int
    lines_scanned: int
    matched_events: int
    stored_events: int
    duplicate_events: int
    unmatched_lines: int
    skipped_lines: int
    errors: tuple[PlayerLogCollectionError, ...]
    files: tuple[PlayerLogCollectionFileSummary, ...]

    @property
    def error_count(self) -> int:
        return len(self.errors)

    def to_dict(self) -> dict[str, object]:
        return {
            "dry_run": self.dry_run,
            "files_requested": self.files_requested,
            "files_scanned": self.files_scanned,
            "files_skipped": self.files_skipped,
            "lines_scanned": self.lines_scanned,
            "matched_events": self.matched_events,
            "stored_events": self.stored_events,
            "duplicate_events": self.duplicate_events,
            "unmatched_lines": self.unmatched_lines,
            "skipped_lines": self.skipped_lines,
            "error_count": self.error_count,
            "errors": [error.to_dict() for error in self.errors],
            "files": [file_summary.to_dict() for file_summary in self.files],
        }


def collect_player_log_events(
    log_paths: os.PathLike[str] | str | Iterable[os.PathLike[str] | str],
    db_path: Path,
    *,
    dry_run: bool = False,
    max_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_lines: int = DEFAULT_MAX_FILE_LINES,
    ingested_at: str | None = None,
) -> PlayerLogCollectionSummary:
    """Parse explicitly supplied bounded text logs and optionally ingest events.

    Files larger than max_bytes fail closed and are skipped without a partial
    read. Files within the byte bound are read line-by-line up to max_lines.
    Source refs use only a sanitized basename and line number.
    """
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    if max_lines < 1:
        raise ValueError("max_lines must be positive")

    requested_paths = _normalize_log_paths(log_paths)
    file_summaries: list[PlayerLogCollectionFileSummary] = []
    for log_path in requested_paths:
        file_summary, parsed_events = _scan_log_file(
            log_path,
            max_bytes=max_bytes,
            max_lines=max_lines,
        )
        if not dry_run and parsed_events and not file_summary.errors:
            file_summary = _ingest_file_events(
                db_path,
                file_summary,
                parsed_events,
                ingested_at=ingested_at,
            )
        file_summaries.append(file_summary)

    return _build_collection_summary(dry_run=dry_run, file_summaries=tuple(file_summaries))


def format_player_log_collection_summary(summary: PlayerLogCollectionSummary) -> str:
    """Return a compact, path-safe human summary for CLI output."""
    mode = "dry-run" if summary.dry_run else "import"
    lines = [
        f"Player log collection {mode} complete.",
        f"  Files requested: {summary.files_requested}",
        f"  Files scanned:   {summary.files_scanned}",
        f"  Files skipped:   {summary.files_skipped}",
        f"  Lines scanned:   {summary.lines_scanned}",
        f"  Matched events:  {summary.matched_events}",
        f"  Stored events:   {summary.stored_events}",
        f"  Duplicates:      {summary.duplicate_events}",
        f"  Unmatched lines: {summary.unmatched_lines}",
        f"  Skipped lines:   {summary.skipped_lines}",
    ]
    if summary.files:
        lines.append("  Files:")
        for file_summary in summary.files:
            limit_text = ""
            if file_summary.limited and file_summary.limit_reason:
                limit_text = f", limited={file_summary.limit_reason}"
            lines.append(
                "    "
                f"{file_summary.source}: {file_summary.status}; "
                f"lines={file_summary.lines_scanned}, "
                f"matched={file_summary.matched_events}, "
                f"stored={file_summary.stored_events}, "
                f"duplicates={file_summary.duplicate_events}"
                f"{limit_text}"
            )
    if summary.errors:
        lines.append("  Errors:")
        for error in summary.errors:
            lines.append(f"    {error.source}: {error.code} - {error.message}")
    return "\n".join(lines)


def _normalize_log_paths(
    log_paths: os.PathLike[str] | str | Iterable[os.PathLike[str] | str],
) -> tuple[Path, ...]:
    if isinstance(log_paths, str | os.PathLike):
        return (Path(log_paths),)
    return tuple(Path(path) for path in log_paths)


def _scan_log_file(
    log_path: Path,
    *,
    max_bytes: int,
    max_lines: int,
) -> tuple[PlayerLogCollectionFileSummary, tuple[PlayerLogEvent, ...]]:
    source = _safe_path_label(log_path)
    try:
        stat_result = log_path.stat()
    except FileNotFoundError:
        return _error_file_summary(source, "missing_file", "file does not exist")
    except OSError:
        return _error_file_summary(source, "stat_failed", "file metadata could not be read")

    if not stat_module.S_ISREG(stat_result.st_mode):
        return _error_file_summary(source, "not_file", "path is not a regular file")
    if stat_result.st_size > max_bytes:
        return _error_file_summary(
            source,
            "file_too_large",
            f"file size exceeds max_bytes={max_bytes}; skipped without partial read",
        )

    try:
        with log_path.open("rb") as handle:
            sample = handle.read(min(_BINARY_SNIFF_BYTES, max_bytes))
            if b"\0" in sample:
                return _error_file_summary(source, "binary_file", "file is not a text log")
            handle.seek(0)
            return _scan_text_handle(
                handle,
                source=source,
                source_ref_prefix=_safe_source_ref_prefix(log_path, source),
                max_lines=max_lines,
            )
    except OSError:
        return _error_file_summary(source, "read_failed", "file could not be read")


def _scan_text_handle(
    handle,
    *,
    source: str,
    source_ref_prefix: str,
    max_lines: int,
) -> tuple[PlayerLogCollectionFileSummary, tuple[PlayerLogEvent, ...]]:
    bytes_scanned = 0
    lines_scanned = 0
    unmatched_lines = 0
    skipped_lines = 0
    limited = False
    parsed_events: list[PlayerLogEvent] = []

    for raw_line in handle:
        if lines_scanned >= max_lines:
            limited = True
            skipped_lines = 1
            break
        bytes_scanned += len(raw_line)
        lines_scanned += 1
        if b"\0" in raw_line:
            return _error_file_summary(source, "binary_file", "file is not a text log")
        try:
            line = raw_line.decode("utf-8")
        except UnicodeDecodeError:
            return _error_file_summary(source, "decode_failed", "file is not valid UTF-8 text")

        event = parse_player_log_event(
            line,
            raw_source_ref=f"{source_ref_prefix}:{lines_scanned}",
        )
        if event is None:
            unmatched_lines += 1
            continue
        parsed_events.append(event)

    file_summary = PlayerLogCollectionFileSummary(
        source=source,
        status="scanned",
        bytes_scanned=bytes_scanned,
        lines_scanned=lines_scanned,
        matched_events=len(parsed_events),
        unmatched_lines=unmatched_lines,
        skipped_lines=skipped_lines,
        limited=limited,
        limit_reason="max_lines" if limited else None,
    )
    return file_summary, tuple(parsed_events)


def _ingest_file_events(
    db_path: Path,
    file_summary: PlayerLogCollectionFileSummary,
    parsed_events: tuple[PlayerLogEvent, ...],
    *,
    ingested_at: str | None,
) -> PlayerLogCollectionFileSummary:
    try:
        result = player_registry.ingest_player_log_events(
            db_path,
            parsed_events,
            ingested_at=ingested_at,
        )
    except Exception as error:
        ingest_error = _collection_error(
            file_summary.source,
            "ingest_failed",
            f"storage ingest failed ({type(error).__name__})",
        )
        return replace(
            file_summary,
            status="error",
            errors=(*file_summary.errors, ingest_error),
        )

    return replace(
        file_summary,
        stored_events=result.stored_count,
        duplicate_events=result.duplicate_count,
    )


def _build_collection_summary(
    *,
    dry_run: bool,
    file_summaries: tuple[PlayerLogCollectionFileSummary, ...],
) -> PlayerLogCollectionSummary:
    errors = tuple(error for file_summary in file_summaries for error in file_summary.errors)
    return PlayerLogCollectionSummary(
        dry_run=dry_run,
        files_requested=len(file_summaries),
        files_scanned=sum(1 for item in file_summaries if item.status == "scanned"),
        files_skipped=sum(1 for item in file_summaries if item.status != "scanned"),
        lines_scanned=sum(item.lines_scanned for item in file_summaries),
        matched_events=sum(item.matched_events for item in file_summaries),
        stored_events=sum(item.stored_events for item in file_summaries),
        duplicate_events=sum(item.duplicate_events for item in file_summaries),
        unmatched_lines=sum(item.unmatched_lines for item in file_summaries),
        skipped_lines=sum(item.skipped_lines for item in file_summaries),
        errors=errors,
        files=file_summaries,
    )


def _error_file_summary(
    source: str,
    code: str,
    message: str,
) -> tuple[PlayerLogCollectionFileSummary, tuple[PlayerLogEvent, ...]]:
    error = _collection_error(source, code, message)
    return (
        PlayerLogCollectionFileSummary(
            source=source,
            status="error",
            errors=(error,),
        ),
        (),
    )


def _collection_error(source: str, code: str, message: str) -> PlayerLogCollectionError:
    safe_source = _safe_label(source)
    safe_message = _safe_label(message, max_length=240)
    return PlayerLogCollectionError(
        source=safe_source,
        code=_safe_label(code, max_length=80),
        message=safe_message,
    )



def _safe_source_ref_prefix(path: Path, source: str) -> str:
    digest = hashlib.sha256(
        str(path.resolve(strict=False)).encode("utf-8", "replace")
    ).hexdigest()[:12]
    return f"{source}:{digest}"

def _safe_path_label(path: Path) -> str:
    return _safe_label(path.name or "log")


def _safe_label(value: object, *, max_length: int = PLAYER_LOG_COLLECTOR_LABEL_MAX_LENGTH) -> str:
    text = safe_player_text(value, max_length=max_length)
    text = text.replace("/", "_").replace("\\", "_")
    text = _IPV4_ADDRESS_RE.sub("***", text)
    text = _BRACKETED_IPV6_ADDRESS_RE.sub("***", text).strip()
    return text or "log"
