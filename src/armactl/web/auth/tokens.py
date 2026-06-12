"""Token generation and digest helpers for web auth."""

from __future__ import annotations

import hashlib
import hmac
import secrets

TOKEN_BYTES = 32


def generate_token() -> str:
    """Generate a high-entropy URL-safe bearer token."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def digest_token(token: str) -> str | None:
    """Return a stable digest for a token, or None for invalid input."""
    if not isinstance(token, str) or not token:
        return None
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_digest_matches(stored_digest: str, candidate_digest: str) -> bool:
    """Compare token digests in constant time."""
    if not isinstance(stored_digest, str) or not isinstance(candidate_digest, str):
        return False
    return hmac.compare_digest(stored_digest, candidate_digest)
