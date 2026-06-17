"""Tests for read-only web log and report views."""

from __future__ import annotations

import builtins
import importlib
import re
import sys
import warnings
from pathlib import Path
from types import SimpleNamespace

from starlette.exceptions import StarletteDeprecationWarning

from armactl.web.auth.cookies import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME
from armactl.web.auth.setup import setup_owner_user
from armactl.web.i18n import LANGUAGE_COOKIE_NAME


def _matches_prefix(module_name: str, prefixes: tuple[str, ...]) -> bool:
    return any(
        module_name == prefix or module_name.startswith(f"{prefix}.") for prefix in prefixes
    )


def _forget_modules(*prefixes: str) -> None:
    for module_name in list(sys.modules):
        if _matches_prefix(module_name, prefixes):
            sys.modules.pop(module_name)


def _client(app, base_url: str = "http://testserver"):
    with warnings.catch_warnings():
        warnings.simplefilter("error", StarletteDeprecationWarning)
        from fastapi.testclient import TestClient

        return TestClient(app, base_url=base_url)


def _form_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def _login(client, username: str, password: str):
    form_response = client.get("/login")
    csrf_token = _form_token(form_response.text)
    return client.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": csrf_token},
        follow_redirects=False,
    )


def _set_cookie(client, name: str, value: str) -> None:
    client.cookies.set(name, value, path="/")


def _audit_path(data_root: Path) -> Path:
    return data_root / "logs" / "web" / "audit.log"


def _write_audit(data_root: Path, text: str) -> Path:
    path = _audit_path(data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_log_view_import_does_not_import_tui_textual(monkeypatch):
    forbidden = ("armactl.tui", "textual")
    _forget_modules("armactl.web.services.log_views", *forbidden)
    original_import = builtins.__import__
    blocked_imports: list[str] = []

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if _matches_prefix(name, forbidden):
            blocked_imports.append(name)
            raise AssertionError(f"log view imported forbidden dependency {name!r}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    module = importlib.import_module("armactl.web.services.log_views")

    assert module.__name__ == "armactl.web.services.log_views"
    assert blocked_imports == []
    assert "armactl.tui" not in sys.modules
    assert "textual" not in sys.modules


def test_unauthenticated_logs_redirect_to_login(tmp_path: Path):
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    response = client.get("/logs", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_authenticated_owner_can_open_audit_logs(tmp_path: Path):
    from armactl.web.app import create_app

    password = "owner logs password"
    setup_owner_user(tmp_path, "owner", password)
    _write_audit(tmp_path, "first audit line\nsecond audit line\n")
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/logs", follow_redirects=False)

    assert response.status_code == 200
    assert "Logs" in response.text
    assert "Audit log" in response.text
    assert "second audit line" in response.text
    assert 'href="/report?lines=120"' in response.text
    assert 'action="/logout"' in response.text
    assert SESSION_COOKIE_NAME not in response.text
    assert CSRF_COOKIE_NAME not in response.text


def test_logs_permission_denied_returns_controlled_403(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app
    from armactl.web.routes import logs as logs_route

    password = "owner logs password"
    setup_owner_user(tmp_path, "owner", password)
    monkeypatch.setattr(
        logs_route,
        "require_permission",
        lambda current, permission: False,
    )
    app = create_app(data_root=tmp_path)
    client = _client(app)
    _login(client, "owner", password)

    response = client.get("/logs", follow_redirects=False)

    assert response.status_code == 403
    assert response.text == "Permission denied."
    assert "Traceback" not in response.text


def test_unknown_log_source_is_controlled(tmp_path: Path):
    from armactl.web.app import create_app

    password = "owner logs password"
    setup_owner_user(tmp_path, "owner", password)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/logs/not-real", follow_redirects=False)

    assert response.status_code == 404
    assert "Unknown log source" in response.text
    assert "Traceback" not in response.text


def test_line_count_is_clamped_for_journal_source(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app
    from armactl.web.services import log_views

    calls: list[tuple[str, int]] = []

    def fake_get_logs_text(service_name: str, lines: int) -> str:
        calls.append((service_name, lines))
        return "journal ok"

    monkeypatch.setattr(
        log_views.discovery,
        "discover",
        lambda instance, save=False: SimpleNamespace(service_name="custom.service"),
    )
    monkeypatch.setattr(log_views.logs, "get_logs_text", fake_get_logs_text)
    password = "owner logs password"
    setup_owner_user(tmp_path, "owner", password)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/logs/server-journal?lines=9999", follow_redirects=False)

    assert response.status_code == 200
    assert calls == [("custom.service", 500)]
    assert 'value="500"' in response.text
    assert "journal ok" in response.text


def test_audit_source_reads_only_configured_audit_log_path(tmp_path: Path):
    from armactl.web.app import create_app

    password = "owner logs password"
    setup_owner_user(tmp_path, "owner", password)
    _write_audit(tmp_path, "configured audit line\n")
    decoy = tmp_path / "web" / "audit.log"
    decoy.parent.mkdir(parents=True, exist_ok=True)
    decoy.write_text("wrong audit path secret\n", encoding="utf-8")
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/logs/audit", follow_redirects=False)

    assert response.status_code == 200
    assert "configured audit line" in response.text
    assert "wrong audit path secret" not in response.text


def test_log_output_is_redacted_and_bounded(tmp_path: Path):
    from armactl.web.app import create_app

    password = "owner logs password"
    setup_owner_user(tmp_path, "owner", password)
    noisy_lines = [f"line {index} {'x' * 320}" for index in range(520)]
    noisy_lines.extend(
        [
            "password=hunter2",
            "ARMACTL_WEB_SESSION_SECRET=raw-session-secret",
            "password_hash=$argon2id$v=19$m=65536,t=3,p=4$abcdef$ghijkl",
        ]
    )
    _write_audit(tmp_path, "\n".join(noisy_lines) + "\n")
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/logs/audit?lines=500", follow_redirects=False)

    assert response.status_code == 200
    assert "Output truncated." in response.text
    assert "hunter2" not in response.text
    assert "raw-session-secret" not in response.text
    assert "$argon2id$" not in response.text
    assert "password=***" in response.text
    assert "ARMACTL_WEB_SESSION_SECRET=***" in response.text
    assert "password_hash=***" in response.text


def test_large_audit_log_uses_bounded_tail_and_marks_truncated(tmp_path: Path):
    from armactl.web.app import create_app
    from armactl.web.services import log_views

    password = "owner logs password"
    setup_owner_user(tmp_path, "owner", password)
    old_payload = "old audit secret should not render\n" * (
        log_views.MAX_RENDER_BYTES // 8
    )
    _write_audit(tmp_path, f"{old_payload}latest audit line\n")
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/logs/audit?lines=1", follow_redirects=False)

    assert response.status_code == 200
    assert "Output truncated." in response.text
    assert "latest audit line" in response.text
    assert "old audit secret should not render" not in response.text


def test_report_preview_calls_existing_report_builder(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app
    from armactl.web.services import log_views

    calls: list[tuple[str, int, bool]] = []

    def fake_build_report(instance: str, *, lines: int, include_journal: bool) -> str:
        calls.append((instance, lines, include_journal))
        return "diagnostic report\npassword=hunter2\n"

    monkeypatch.setattr(log_views.report, "build_report", fake_build_report)
    password = "owner logs password"
    setup_owner_user(tmp_path, "owner", password)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.get("/report?lines=250", follow_redirects=False)

    assert response.status_code == 200
    assert calls == [("default", 250, False)]
    assert "Diagnostic report" in response.text
    assert "diagnostic report" in response.text
    assert "hunter2" not in response.text
    assert "password=***" in response.text


def test_logs_page_renders_ukrainian_labels(tmp_path: Path):
    from armactl.web.app import create_app

    password = "owner logs password"
    setup_owner_user(tmp_path, "owner", password)
    _write_audit(tmp_path, "рядок аудиту\n")
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)
    _set_cookie(client, LANGUAGE_COOKIE_NAME, "uk")

    response = client.get("/logs", follow_redirects=False)

    assert response.status_code == 200
    assert '<html lang="uk"' in response.text
    assert "Логи" in response.text
    assert "Аудит-лог" in response.text
    assert "Рядки" in response.text
    assert "Оновити" in response.text
