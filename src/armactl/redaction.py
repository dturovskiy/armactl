"""Helpers for masking secrets in UI messages and logs."""

from __future__ import annotations

import re

REDACTED = "***"

_ASSIGNMENT_PATTERNS = [
    re.compile(r'(?im)\b(ARMACTL_BOT_TOKEN\s*=\s*)([^\r\n#]+)'),
    re.compile(r'(?im)\b((?:passwordAdmin|password|token)\s*[=:]\s*)([^\s,;]+)'),
    re.compile(
        r'(?im)("(?:(?:ARMACTL_BOT_TOKEN)|(?:passwordAdmin)|(?:password)|(?:token))"\s*:\s*")'
        r'([^"]*)'
        r'(")'
    ),
    re.compile(
        r"(?im)('(?:(?:ARMACTL_BOT_TOKEN)|(?:passwordAdmin)|(?:password)|(?:token))'\s*:\s*')"
        r"([^']*)"
        r"(')"
    ),
]
_BOT_TOKEN_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{10,}\b")


def redact_sensitive_text(value: object | None) -> str:
    """Redact obvious secrets from arbitrary text."""
    text = "" if value is None else str(value)
    for pattern in _ASSIGNMENT_PATTERNS:
        text = pattern.sub(_replace_assignment_match, text)
    return _BOT_TOKEN_RE.sub(REDACTED, text)


def safe_subprocess_error(stderr: str | None, stdout: str | None = None) -> str:
    """Return a redacted subprocess error string."""
    raw = (stderr or "").strip() or (stdout or "").strip()
    return redact_sensitive_text(raw).strip()


def _replace_assignment_match(match: re.Match[str]) -> str:
    """Preserve a config key while masking the sensitive value."""
    if match.lastindex == 2:
        return f"{match.group(1)}{REDACTED}"
    return f"{match.group(1)}{REDACTED}{match.group(match.lastindex or 3)}"
