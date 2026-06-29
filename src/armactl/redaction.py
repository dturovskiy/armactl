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
_DISCORD_WEBHOOK_URL_RE = re.compile(
    r"https://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9._~+-]+",
    re.IGNORECASE,
)
_BOT_TOKEN_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{10,}\b")
_IPV4_ADDRESS_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?\b")
_BRACKETED_IPV6_ADDRESS_RE = re.compile(r"\[[0-9A-Fa-f:.]{2,}\](?::\d{1,5})?")
_POSIX_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![\w.:@/~-])/(?:home|root|tmp|var|opt|srv|etc|mnt|run|usr)"
    r"(?:/[^\s,;'\"<>]+)+"
)
_HOME_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![\w.:@/~-])~/(?:[^\s,;'\"<>]+/)+[^\s,;'\"<>]+"
)
_WINDOWS_ABSOLUTE_PATH_RE = re.compile(
    r'(?i)\b[A-Z]:\\(?:[^\s,;\'"<>|]+\\)*[^\s,;\'"<>|]+'
)
_UNC_ABSOLUTE_PATH_RE = re.compile(
    r'\\\\[^\s,;\'"<>|]+(?:\\[^\s,;\'"<>|]+)+'
)


def redact_sensitive_text(value: object | None) -> str:
    """Redact obvious secrets from arbitrary text."""
    text = "" if value is None else str(value)
    for pattern in _ASSIGNMENT_PATTERNS:
        text = pattern.sub(_replace_assignment_match, text)
    text = _DISCORD_WEBHOOK_URL_RE.sub(REDACTED, text)
    text = _BOT_TOKEN_RE.sub(REDACTED, text)
    text = _UNC_ABSOLUTE_PATH_RE.sub(REDACTED, text)
    text = _WINDOWS_ABSOLUTE_PATH_RE.sub(REDACTED, text)
    text = _HOME_ABSOLUTE_PATH_RE.sub(REDACTED, text)
    text = _POSIX_ABSOLUTE_PATH_RE.sub(REDACTED, text)
    text = _IPV4_ADDRESS_RE.sub(REDACTED, text)
    return _BRACKETED_IPV6_ADDRESS_RE.sub(REDACTED, text)


def safe_subprocess_error(stderr: str | None, stdout: str | None = None) -> str:
    """Return a redacted subprocess error string."""
    raw = (stderr or "").strip() or (stdout or "").strip()
    return redact_sensitive_text(raw).strip()


def _replace_assignment_match(match: re.Match[str]) -> str:
    """Preserve a config key while masking the sensitive value."""
    if match.lastindex == 2:
        return f"{match.group(1)}{REDACTED}"
    return f"{match.group(1)}{REDACTED}{match.group(match.lastindex or 3)}"
