"""Small auth data structures and controlled exceptions."""

from __future__ import annotations

from dataclasses import dataclass, field


class WebAuthError(ValueError):
    """Raised when web auth input or storage operations cannot be completed."""


class InvalidAuthInputError(WebAuthError):
    """Raised when caller-provided auth input is invalid."""


class UserAlreadyExistsError(WebAuthError):
    """Raised when creating a user that already exists."""


@dataclass(frozen=True)
class UserRecord:
    """Auth user row without any plaintext password."""

    id: int
    username: str
    password_hash: str = field(repr=False)
    role: str
    is_active: bool
    created_at: str
    updated_at: str
