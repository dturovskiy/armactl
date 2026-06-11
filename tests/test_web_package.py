"""Tests for the initial web package skeleton."""

from __future__ import annotations

import builtins
import importlib
import sys

FORBIDDEN_IMPORT_PREFIXES = (
    "armactl.tui",
    "textual",
    "fastapi",
    "starlette",
    "uvicorn",
    "multipart",
    "argon2",
)


def _matches_prefix(module_name: str, prefixes: tuple[str, ...]) -> bool:
    return any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in prefixes
    )


def _forget_modules(*prefixes: str) -> None:
    for module_name in list(sys.modules):
        if _matches_prefix(module_name, prefixes):
            sys.modules.pop(module_name)


def test_web_package_import_is_lightweight(monkeypatch):
    _forget_modules("armactl.web", *FORBIDDEN_IMPORT_PREFIXES)

    original_import = builtins.__import__
    blocked_imports: list[str] = []

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if _matches_prefix(name, FORBIDDEN_IMPORT_PREFIXES):
            blocked_imports.append(name)
            raise AssertionError(f"armactl.web imported forbidden dependency {name!r}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    module = importlib.import_module("armactl.web")

    assert module.__name__ == "armactl.web"
    assert blocked_imports == []
    assert not any(
        _matches_prefix(module_name, FORBIDDEN_IMPORT_PREFIXES) for module_name in sys.modules
    )
