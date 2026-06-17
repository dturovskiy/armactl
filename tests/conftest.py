"""Shared pytest fixtures."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap

import pytest

from armactl.i18n import using_lang


@pytest.fixture(autouse=True)
def default_test_language():
    """Keep tests independent from the user's saved armactl UI language."""
    with using_lang("en"):
        yield


@pytest.fixture
def set_web_owner_permissions(monkeypatch):
    """Temporarily set permissions for the real web owner role."""

    from armactl.web.auth import permissions as permission_module

    def set_permissions(permissions: set[str] | frozenset[str]) -> None:
        monkeypatch.setattr(
            permission_module,
            "ROLE_PERMISSIONS",
            {permission_module.OWNER_ROLE: frozenset(permissions)},
        )

    return set_permissions


@pytest.fixture
def assert_import_does_not_import_modules():
    """Assert import safety in a subprocess so sys.modules stays isolated."""

    def check(module_name: str, forbidden: tuple[str, ...]) -> None:
        code = textwrap.dedent(
            """
            import builtins
            import importlib
            import json
            import sys

            module_name = sys.argv[1]
            forbidden = tuple(json.loads(sys.argv[2]))

            def matches(name):
                return any(
                    name == prefix or name.startswith(f"{prefix}.")
                    for prefix in forbidden
                )

            original_import = builtins.__import__
            blocked_imports = []

            def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
                if matches(name):
                    blocked_imports.append(name)
                    raise AssertionError(
                        f"{module_name} imported forbidden dependency {name!r}"
                    )
                return original_import(name, globals, locals, fromlist, level)

            builtins.__import__ = guarded_import
            module = importlib.import_module(module_name)

            assert module.__name__ == module_name
            assert blocked_imports == []
            assert not any(matches(name) for name in sys.modules)
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", code, module_name, json.dumps(list(forbidden))],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    return check
