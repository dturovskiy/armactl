"""Tests for web auth storage and password helpers."""

from __future__ import annotations

import builtins
import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

from armactl.web.auth.models import InvalidAuthInputError, UserAlreadyExistsError
from armactl.web.auth.passwords import hash_password, verify_password
from armactl.web.auth.setup import setup_owner_user
from armactl.web.auth.users import (
    create_owner_user,
    get_user_by_username,
    verify_user_password,
)
from armactl.web.runtime import ensure_web_db

FORBIDDEN_IMPORT_PREFIXES = (
    "armactl.tui",
    "textual",
    "fastapi",
    "starlette",
    "uvicorn",
    "armactl.web.routes",
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


def _sqlite_tables(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            """
        ).fetchall()
    return {row[0] for row in rows}


def test_hash_password_creates_non_plaintext_hash():
    password = "correct horse battery staple"

    password_hash = hash_password(password)

    assert password_hash != password
    assert password_hash.startswith("$argon2")


def test_verify_password_returns_true_for_correct_password_and_false_for_wrong():
    password_hash = hash_password("safely random enough for test")

    assert verify_password("safely random enough for test", password_hash) is True
    assert verify_password("wrong password", password_hash) is False


def test_verify_password_returns_false_for_malformed_hash():
    assert verify_password("password", "not-an-argon2-hash") is False


def test_empty_password_is_rejected():
    with pytest.raises(InvalidAuthInputError, match="Password cannot be empty"):
        hash_password("")


def test_ensure_web_db_creates_auth_users_table_without_session_or_csrf_tables(
    tmp_path: Path,
):
    db_path = tmp_path / "web" / "web.db"

    ensure_web_db(db_path)

    tables = _sqlite_tables(db_path)
    assert "web_users" in tables
    assert tables.isdisjoint(
        {
            "auth",
            "csrf",
            "csrf_tokens",
            "sessions",
            "users",
            "web_auth",
            "web_csrf_tokens",
            "web_sessions",
        }
    )


def test_create_owner_user_stores_owner_without_plaintext_password(tmp_path: Path):
    db_path = tmp_path / "web" / "web.db"
    password = "strong owner password"

    user = create_owner_user(db_path, "Owner", password)

    assert user.id > 0
    assert user.username == "owner"
    assert user.role == "owner"
    assert user.is_active is True
    assert user.password_hash != password
    assert password not in repr(user)
    assert user.password_hash not in repr(user)

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT username, password_hash, role, is_active, created_at, updated_at
            FROM web_users
            WHERE id = ?
            """,
            (user.id,),
        ).fetchone()

    assert row is not None
    assert password not in row
    assert row[0] == "owner"
    assert row[1] == user.password_hash
    assert row[2] == "owner"
    assert row[3] == 1
    assert row[4]
    assert row[5]


def test_duplicate_username_is_rejected_with_controlled_exception(tmp_path: Path):
    db_path = tmp_path / "web" / "web.db"

    create_owner_user(db_path, "Admin", "first password")

    with pytest.raises(UserAlreadyExistsError, match="already exists"):
        create_owner_user(db_path, " admin ", "second password")


def test_username_normalization_trims_and_casefolds(tmp_path: Path):
    db_path = tmp_path / "web" / "web.db"
    created = create_owner_user(db_path, "  Admin  ", "owner password")

    found = get_user_by_username(db_path, "ADMIN")

    assert found is not None
    assert found.id == created.id
    assert found.username == "admin"


def test_user_helpers_reject_empty_username_and_password(tmp_path: Path):
    db_path = tmp_path / "web" / "web.db"

    with pytest.raises(InvalidAuthInputError, match="Username cannot be empty"):
        create_owner_user(db_path, " ", "owner password")
    with pytest.raises(InvalidAuthInputError, match="Password cannot be empty"):
        create_owner_user(db_path, "owner", "")
    with pytest.raises(InvalidAuthInputError, match="Username cannot be empty"):
        get_user_by_username(db_path, "")
    with pytest.raises(InvalidAuthInputError, match="Password cannot be empty"):
        verify_user_password(db_path, "owner", "")


def test_setup_owner_user_creates_runtime_and_owner(tmp_path: Path):
    password = "owner setup password"

    result = setup_owner_user(tmp_path, " Owner ", password)

    assert result.config.env_path == tmp_path / "web" / "web.env"
    assert result.config.db_path == tmp_path / "web" / "web.db"
    assert result.config.env_path.exists()
    assert result.config.db_path.exists()
    assert result.user.username == "owner"
    assert result.user.role == "owner"
    assert result.user.password_hash != password
    assert verify_user_password(result.config.db_path, "OWNER", password) is True


def test_setup_owner_user_rejects_second_owner(tmp_path: Path):
    setup_owner_user(tmp_path, "owner", "first owner password")

    with pytest.raises(UserAlreadyExistsError, match="owner user already exists"):
        setup_owner_user(tmp_path, "second-owner", "second owner password")


def test_setup_owner_user_rejects_empty_username_and_password(tmp_path: Path):
    with pytest.raises(InvalidAuthInputError, match="Username cannot be empty"):
        setup_owner_user(tmp_path, " ", "owner password")
    with pytest.raises(InvalidAuthInputError, match="Password cannot be empty"):
        setup_owner_user(tmp_path, "owner", "")


def test_verify_user_password_returns_true_false_for_active_user(tmp_path: Path):
    db_path = tmp_path / "web" / "web.db"
    create_owner_user(db_path, "Owner", "owner password")

    assert verify_user_password(db_path, " owner ", "owner password") is True
    assert verify_user_password(db_path, "owner", "wrong password") is False
    assert verify_user_password(db_path, "missing", "owner password") is False


def test_disabled_user_does_not_pass_password_verification(tmp_path: Path):
    db_path = tmp_path / "web" / "web.db"
    user = create_owner_user(db_path, "Owner", "owner password")

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE web_users
            SET is_active = 0
            WHERE id = ?
            """,
            (user.id,),
        )

    assert verify_user_password(db_path, "owner", "owner password") is False


def test_auth_package_import_does_not_import_tui_routes_or_asgi(monkeypatch):
    _forget_modules("armactl.web.auth", "argon2", *FORBIDDEN_IMPORT_PREFIXES)
    original_import = builtins.__import__
    blocked_imports: list[str] = []

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if _matches_prefix(name, FORBIDDEN_IMPORT_PREFIXES):
            blocked_imports.append(name)
            raise AssertionError(f"web auth imported forbidden dependency {name!r}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    module = importlib.import_module("armactl.web.auth")

    assert module.UserRecord.__name__ == "UserRecord"
    assert "armactl.web.auth.passwords" not in sys.modules
    assert "armactl.web.auth.setup" not in sys.modules
    assert "armactl.web.auth.users" not in sys.modules
    assert "argon2" not in sys.modules
    assert blocked_imports == []
    assert not any(
        _matches_prefix(module_name, FORBIDDEN_IMPORT_PREFIXES) for module_name in sys.modules
    )
