"""Tests for the initial web package skeleton."""

from __future__ import annotations

FORBIDDEN_IMPORT_PREFIXES = (
    "armactl.tui",
    "textual",
    "fastapi",
    "starlette",
    "uvicorn",
    "multipart",
    "argon2",
)


def test_web_package_import_is_lightweight(assert_import_does_not_import_modules):
    assert_import_does_not_import_modules("armactl.web", FORBIDDEN_IMPORT_PREFIXES)
