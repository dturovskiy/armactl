"""Append-only audit logging for mutating web actions."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from armactl.redaction import redact_sensitive_text

MAX_AUDIT_MESSAGE_LENGTH = 1000
PRIVATE_AUDIT_FILE_MODE = 0o600


class AuditLogError(RuntimeError):
    """Raised when the web audit log cannot be written."""


@dataclass(frozen=True)
class AuditEvent:
    """Structured audit event with no raw secrets."""

    timestamp: str
    username: str
    action: str
    instance: str
    target: str
    service: str
    success: bool
    message: str
    exit_code: int | None


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_text(value: object, *, max_length: int = MAX_AUDIT_MESSAGE_LENGTH) -> str:
    redacted = redact_sensitive_text(value).replace("\r", " ").replace("\n", " ").strip()
    if len(redacted) > max_length:
        return f"{redacted[:max_length]}..."
    return redacted


def append_audit_event(
    audit_log_path: Path,
    *,
    username: str,
    action: str,
    instance: str,
    target: str,
    success: bool,
    message: str,
    exit_code: int | None,
) -> AuditEvent:
    """Append one JSON audit event and return the safe event payload."""
    safe_target = _safe_text(target)
    event = AuditEvent(
        timestamp=_utc_timestamp(),
        username=_safe_text(username),
        action=_safe_text(action),
        instance=_safe_text(instance),
        target=safe_target,
        service=safe_target,
        success=bool(success),
        message=_safe_text(message),
        exit_code=exit_code,
    )
    payload: dict[str, Any] = {
        "timestamp": event.timestamp,
        "username": event.username,
        "action": event.action,
        "instance": event.instance,
        "target": event.target,
        "service": event.service,
        "success": event.success,
        "message": event.message,
        "exit_code": event.exit_code,
    }

    try:
        audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(
            audit_log_path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            PRIVATE_AUDIT_FILE_MODE,
        )
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n")
        audit_log_path.chmod(PRIVATE_AUDIT_FILE_MODE)
    except OSError as exc:
        raise AuditLogError("Failed to write web audit log.") from exc

    return event
