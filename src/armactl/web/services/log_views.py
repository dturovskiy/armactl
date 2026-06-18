"""Bounded, redacted log/report views for armactl web."""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from armactl import discovery, logs, paths, report
from armactl.redaction import redact_sensitive_text
from armactl.web.runtime import WebRuntimeConfig

DEFAULT_LOG_LINES = 120
MAX_LOG_LINES = 500
MAX_RENDER_BYTES = 128 * 1024
TRUNCATED_PREFIX = "... output truncated ...\n"
_WEB_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?im)\b((?:ARMACTL_WEB_SESSION_SECRET|session_secret|session_token|"
    r"csrf_token|password_hash|armactl_web_session|armactl_web_csrf|secret|api_key)"
    r"\s*[=:]\s*)([^\s,;]+)"
)
_ARGON2_HASH_RE = re.compile(r"\$argon2(?:id|i|d)\$[^\s<>&]+")

SOURCE_AUDIT = "audit"
SOURCE_SERVER_JOURNAL = "server-journal"
SOURCE_BOT_JOURNAL = "bot-journal"
SOURCE_WEB_SERVICE = "web-service"
SOURCE_REPORT = "report"


class LogViewError(RuntimeError):
    """Raised when a log view cannot be loaded safely."""


class UnknownLogSourceError(LogViewError):
    """Raised when a requested log source is not allowlisted."""


@dataclass(frozen=True)
class LogSource:
    """Fixed log/report source exposed to the web UI."""

    source_id: str
    title: str
    description: str
    path: str


@dataclass(frozen=True)
class LogView:
    """Rendered, bounded, redacted log/report content for templates."""

    source_id: str
    title: str
    description: str
    path: str
    lines: int
    available: bool
    content: str
    truncated: bool
    error: str = ""


@dataclass(frozen=True)
class _LoadedOutput:
    content: str
    available: bool = True
    truncated: bool = False
    error: str = ""


class _Loader(Protocol):
    def __call__(self, config: WebRuntimeConfig, lines: int) -> _LoadedOutput: ...


SOURCES: tuple[LogSource, ...] = (
    LogSource(
        SOURCE_AUDIT,
        "Audit log",
        "Mutating web action audit trail",
        "/logs/audit",
    ),
    LogSource(
        SOURCE_SERVER_JOURNAL,
        "Server journal",
        "Recent Arma service journal lines",
        "/logs/server-journal",
    ),
    LogSource(
        SOURCE_BOT_JOURNAL,
        "Bot journal",
        "Recent Telegram bot service journal lines",
        "/logs/bot-journal",
    ),
    LogSource(
        SOURCE_WEB_SERVICE,
        "Web service journal",
        "Recent armactl web service journal lines",
        "/logs/web-service",
    ),
    LogSource(
        SOURCE_REPORT,
        "Diagnostic report",
        "Redacted diagnostic report preview",
        "/report",
    ),
)

_SOURCE_MAP = {source.source_id: source for source in SOURCES}


def list_log_sources() -> tuple[LogSource, ...]:
    """Return fixed log/report sources for navigation."""
    return SOURCES


def normalize_source_id(source_id: object | None) -> str:
    """Normalize a source id from a route segment without accepting paths."""
    if not isinstance(source_id, str):
        return ""
    return source_id.strip().lower()


def normalize_line_count(value: object | None) -> int:
    """Clamp requested line count to the safe bounded range."""
    if value in (None, ""):
        return DEFAULT_LOG_LINES
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return DEFAULT_LOG_LINES
    return min(max(parsed, 1), MAX_LOG_LINES)


def _normalize_output(text: object) -> str:
    redacted = redact_sensitive_text(text)
    redacted = _WEB_SECRET_ASSIGNMENT_RE.sub(r"\1***", redacted)
    redacted = _ARGON2_HASH_RE.sub("***", redacted)
    redacted = redacted.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(
        char if char in {"\n", "\t"} or ord(char) >= 32 else " " for char in redacted
    )


def _bound_output(text: object, *, max_bytes: int = MAX_RENDER_BYTES) -> tuple[str, bool]:
    clean = _normalize_output(text)
    data = clean.encode("utf-8")
    if len(data) <= max_bytes:
        return clean, False

    prefix = TRUNCATED_PREFIX.encode("utf-8")
    tail_size = max(max_bytes - len(prefix), 0)
    tail = data[-tail_size:] if tail_size else b""
    return (prefix + tail).decode("utf-8", errors="replace"), True


def _tail_text_file(
    path: Path,
    lines: int,
    *,
    max_read_bytes: int = MAX_RENDER_BYTES * 2,
) -> _LoadedOutput:
    try:
        if not path.exists():
            return _LoadedOutput("", available=False, error="Log source unavailable.")
        if not path.is_file():
            return _LoadedOutput("", available=False, error="Log source unavailable.")
        size = path.stat().st_size
        truncated = size > max_read_bytes
        with path.open("rb") as handle:
            if truncated:
                handle.seek(size - max_read_bytes)
            data = handle.read(max_read_bytes)
        if truncated:
            first_newline = data.find(b"\n")
            if first_newline != -1:
                data = data[first_newline + 1 :]
    except OSError:
        return _LoadedOutput("", available=False, error="Log source unavailable.")

    text = data.decode("utf-8", errors="replace")
    items: deque[str] = deque(maxlen=lines)
    for line in text.splitlines():
        items.append(line)
    content = "\n".join(items)
    if truncated and content:
        content = f"{TRUNCATED_PREFIX}{content}"
    elif truncated:
        content = TRUNCATED_PREFIX.rstrip("\n")
    return _LoadedOutput(content, truncated=truncated)


def _load_audit_log(config: WebRuntimeConfig, lines: int) -> _LoadedOutput:
    return _tail_text_file(config.audit_log_path, lines)


def _server_service_name() -> str:
    try:
        state = discovery.discover(instance=paths.DEFAULT_INSTANCE_NAME, save=False)
    except Exception:  # noqa: BLE001 - diagnostics must fail closed and controlled.
        return paths.SERVICE_NAME
    return str(getattr(state, "service_name", "") or paths.SERVICE_NAME)


def _load_journal(service_name: str, lines: int) -> _LoadedOutput:
    try:
        output = logs.get_logs_text(service_name, lines=lines)
    except Exception:  # noqa: BLE001 - diagnostics fail controlled.
        return _LoadedOutput("", available=False, error="Log source unavailable.")
    return _LoadedOutput(output)


def _load_server_journal(config: WebRuntimeConfig, lines: int) -> _LoadedOutput:
    del config
    return _load_journal(_server_service_name(), lines)


def _load_bot_journal(config: WebRuntimeConfig, lines: int) -> _LoadedOutput:
    del config
    return _load_journal(paths.BOT_SERVICE_NAME, lines)


def _load_web_service_journal(config: WebRuntimeConfig, lines: int) -> _LoadedOutput:
    del config
    return _load_journal(paths.WEB_SERVICE_NAME, lines)


def _load_report(config: WebRuntimeConfig, lines: int) -> _LoadedOutput:
    del config
    try:
        output = report.build_report(
            paths.DEFAULT_INSTANCE_NAME,
            lines=lines,
            include_journal=False,
        )
    except Exception:  # noqa: BLE001 - report preview must fail controlled.
        return _LoadedOutput("", available=False, error="Log source unavailable.")
    return _LoadedOutput(output)


_LOADERS: dict[str, _Loader] = {
    SOURCE_AUDIT: _load_audit_log,
    SOURCE_SERVER_JOURNAL: _load_server_journal,
    SOURCE_BOT_JOURNAL: _load_bot_journal,
    SOURCE_WEB_SERVICE: _load_web_service_journal,
    SOURCE_REPORT: _load_report,
}


def build_unknown_log_view(source_id: object | None, lines: object | None = None) -> LogView:
    """Build a controlled placeholder for unknown sources."""
    normalized_lines = normalize_line_count(lines)
    normalized_source = normalize_source_id(source_id) or "unknown"
    return LogView(
        source_id=normalized_source,
        title="Unknown log source",
        description="Unknown log source",
        path="/logs",
        lines=normalized_lines,
        available=False,
        content="",
        truncated=False,
        error="Unknown log source.",
    )


def build_log_view(
    config: WebRuntimeConfig,
    source_id: object | None = SOURCE_AUDIT,
    *,
    lines: object | None = None,
) -> LogView:
    """Load a fixed log/report source with bounded, redacted output."""
    normalized_source = normalize_source_id(source_id) or SOURCE_AUDIT
    source = _SOURCE_MAP.get(normalized_source)
    if source is None:
        raise UnknownLogSourceError("Unknown log source.")

    normalized_lines = normalize_line_count(lines)
    loaded = _LOADERS[normalized_source](config, normalized_lines)
    content, truncated = _bound_output(loaded.content)
    return LogView(
        source_id=source.source_id,
        title=source.title,
        description=source.description,
        path=source.path,
        lines=normalized_lines,
        available=loaded.available,
        content=content,
        truncated=loaded.truncated or truncated,
        error=loaded.error,
    )
