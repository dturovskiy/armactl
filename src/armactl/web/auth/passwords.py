"""Password hashing helpers for web auth."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from armactl.web.auth.models import InvalidAuthInputError

_PASSWORD_HASHER = PasswordHasher()


def _require_password(password: str) -> str:
    if not isinstance(password, str) or not password:
        raise InvalidAuthInputError("Password cannot be empty.")
    return password


def hash_password(password: str) -> str:
    """Hash a plaintext password with Argon2."""
    return _PASSWORD_HASHER.hash(_require_password(password))


def verify_password(password: str, password_hash: str) -> bool:
    """Return whether a plaintext password matches an Argon2 hash."""
    if not isinstance(password, str) or not password:
        return False
    if not isinstance(password_hash, str) or not password_hash:
        return False

    try:
        return _PASSWORD_HASHER.verify(password_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False
