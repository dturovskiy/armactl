"""Authentication primitives for the armactl web panel."""

from __future__ import annotations

from armactl.web.auth.models import (
    InvalidAuthInputError,
    UserAlreadyExistsError,
    UserRecord,
    WebAuthError,
)

__all__ = [
    "InvalidAuthInputError",
    "UserAlreadyExistsError",
    "UserRecord",
    "WebAuthError",
]
