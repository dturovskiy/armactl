"""Tests for the read-only web file browser foundation."""

from __future__ import annotations

import builtins
import importlib
import re
import sys
import warnings
from pathlib import Path

import pytest
from starlette.exceptions import StarletteDeprecationWarning

from armactl.web.auth.cookies import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME
from armactl.web.auth.setup import setup_owner_user


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


def _server_root(data_root: Path) -> Path:
    path = data_root / "default" / "server"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _config_root(data_root: Path) -> Path:
    path = data_root / "default" / "config"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _login_owner(data_root: Path):
    from armactl.web.app import create_app

    password = "owner files password"
    setup_owner_user(data_root, "owner", password)
    client = _client(create_app(data_root=data_root))
    _login(client, "owner", password)
    return client


def _files_csrf_token(client, url: str = "/files/server") -> str:
    response = client.get(url, follow_redirects=False)
    assert response.status_code == 200
    return _form_token(response.text)


def test_file_adapter_import_does_not_import_tui_textual(monkeypatch):
    forbidden = ("armactl.tui", "textual")
    _forget_modules("armactl.web.services.filesystem", *forbidden)
    original_import = builtins.__import__
    blocked_imports: list[str] = []

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if _matches_prefix(name, forbidden):
            blocked_imports.append(name)
            raise AssertionError(f"filesystem adapter imported forbidden dependency {name!r}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    module = importlib.import_module("armactl.web.services.filesystem")

    assert module.__name__ == "armactl.web.services.filesystem"
    assert blocked_imports == []
    assert "armactl.tui" not in sys.modules
    assert "textual" not in sys.modules


def test_files_route_import_does_not_import_tui_textual(monkeypatch):
    forbidden = ("armactl.tui", "textual")
    _forget_modules("armactl.web.routes.files", *forbidden)
    original_import = builtins.__import__
    blocked_imports: list[str] = []

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if _matches_prefix(name, forbidden):
            blocked_imports.append(name)
            raise AssertionError(f"files route imported forbidden dependency {name!r}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    module = importlib.import_module("armactl.web.routes.files")

    assert module.__name__ == "armactl.web.routes.files"
    assert blocked_imports == []
    assert "armactl.tui" not in sys.modules
    assert "textual" not in sys.modules


def test_unauthenticated_files_redirects_to_login(tmp_path: Path):
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    response = client.get("/files", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_files_permission_denied_returns_controlled_403(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app

    password = "owner files password"
    setup_owner_user(tmp_path, "owner", password)
    app = create_app(data_root=tmp_path)
    for route in app.routes:
        if getattr(route, "path", "") == "/files":
            monkeypatch.setitem(
                route.endpoint.__globals__,
                "require_permission",
                lambda current, permission: False,
            )
            break
    client = _client(app)
    _login(client, "owner", password)

    response = client.get("/files", follow_redirects=False)

    assert response.status_code == 403
    assert response.text == "Permission denied."
    assert "Traceback" not in response.text


def test_authenticated_owner_can_open_files_page(tmp_path: Path):
    client = _login_owner(tmp_path)

    response = client.get("/files", follow_redirects=False)

    assert response.status_code == 200
    assert "File Browser" in response.text
    assert "File browser" in response.text
    assert SESSION_COOKIE_NAME not in response.text
    assert CSRF_COOKIE_NAME not in response.text


def test_unavailable_roots_render_controlled_state(tmp_path: Path):
    client = _login_owner(tmp_path)

    response = client.get("/files", follow_redirects=False)

    assert response.status_code == 200
    assert "Server files" in response.text
    assert "Root unavailable." in response.text
    assert "Traceback" not in response.text


def test_listing_safe_directory_works(tmp_path: Path):
    server = _server_root(tmp_path)
    (server / "subdir").mkdir()
    (server / "world.txt").write_text("hello", encoding="utf-8")
    client = _login_owner(tmp_path)

    response = client.get("/files/server", follow_redirects=False)

    assert response.status_code == 200
    assert "world.txt" in response.text
    assert "subdir" in response.text
    assert "Directory" in response.text
    assert "File" in response.text
    assert 'href="/files/server/preview?path=world.txt#file-preview"' in response.text
    assert 'href="/files/server/download?path=world.txt"' in response.text
    assert 'href="/files/server/download?path=subdir"' not in response.text


def test_listing_hides_preview_for_non_text_files(tmp_path: Path):
    server = _server_root(tmp_path)
    (server / "world.txt").write_text("hello", encoding="utf-8")
    (server / "binary.bin").write_bytes(b"abc\x00def")
    (server / "ArmaReforgerServer").write_bytes(b"\x7fELF\x00binary")
    client = _login_owner(tmp_path)

    response = client.get("/files/server", follow_redirects=False)

    assert response.status_code == 200
    assert 'href="/files/server/preview?path=world.txt#file-preview"' in response.text
    assert 'href="/files/server/preview?path=binary.bin#file-preview"' not in response.text
    assert (
        'href="/files/server/preview?path=ArmaReforgerServer#file-preview"'
        not in response.text
    )


def test_parent_directory_navigation_for_nested_directory(tmp_path: Path):
    server = _server_root(tmp_path)
    (server / "subdir" / "nested").mkdir(parents=True)
    client = _login_owner(tmp_path)

    response = client.get("/files/server?path=subdir/nested", follow_redirects=False)

    assert response.status_code == 200
    assert "Parent directory" in response.text
    assert 'href="/files/server?path=subdir#file-browser"' in response.text


def test_dotdot_traversal_is_rejected(tmp_path: Path):
    _server_root(tmp_path)
    client = _login_owner(tmp_path)

    response = client.get("/files/server?path=../config", follow_redirects=False)

    assert response.status_code == 400
    assert "Unsafe file path." in response.text
    assert "Traceback" not in response.text


def test_absolute_path_is_rejected(tmp_path: Path):
    _server_root(tmp_path)
    client = _login_owner(tmp_path)

    response = client.get("/files/server?path=/etc/passwd", follow_redirects=False)

    assert response.status_code == 400
    assert "Unsafe file path." in response.text
    assert "Traceback" not in response.text


def test_symlink_escape_is_rejected(tmp_path: Path):
    server = _server_root(tmp_path)
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("outside", encoding="utf-8")
    try:
        (server / "escape.txt").symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    client = _login_owner(tmp_path)

    response = client.get("/files/server/preview?path=escape.txt", follow_redirects=False)

    assert response.status_code == 400
    assert "Unsafe file path." in response.text
    assert "outside" not in response.text


def test_unknown_root_is_controlled(tmp_path: Path):
    client = _login_owner(tmp_path)

    response = client.get("/files/not-real", follow_redirects=False)

    assert response.status_code == 404
    assert "Unknown file root." in response.text
    assert "Traceback" not in response.text


def test_git_and_venv_directories_are_not_exposed(tmp_path: Path):
    server = _server_root(tmp_path)
    (server / ".git").mkdir()
    (server / ".venv").mkdir()
    (server / "visible.txt").write_text("ok", encoding="utf-8")
    client = _login_owner(tmp_path)

    response = client.get("/files/server", follow_redirects=False)

    assert response.status_code == 200
    assert "visible.txt" in response.text
    assert ".git" not in response.text
    assert ".venv" not in response.text


def test_source_tree_roots_are_not_available():
    from armactl import paths
    from armactl.web.services import filesystem

    roots = filesystem.list_allowed_roots(paths.project_root())

    assert roots
    assert all(not root.available for root in roots)


def test_preview_redacts_secrets(tmp_path: Path):
    server = _server_root(tmp_path)
    (server / "secret.txt").write_text(
        "password=hunter2\nARMACTL_WEB_SESSION_SECRET=raw-secret\n",
        encoding="utf-8",
    )
    client = _login_owner(tmp_path)

    response = client.get("/files/server/preview?path=secret.txt", follow_redirects=False)

    assert response.status_code == 200
    assert "password=***" in response.text
    assert "ARMACTL_WEB_SESSION_SECRET=***" in response.text
    assert "hunter2" not in response.text
    assert "raw-secret" not in response.text


def test_binary_preview_unavailable(tmp_path: Path):
    server = _server_root(tmp_path)
    (server / "binary.bin").write_bytes(b"abc\x00def")
    client = _login_owner(tmp_path)

    response = client.get("/files/server/preview?path=binary.bin", follow_redirects=False)

    assert response.status_code == 200
    assert "Preview unavailable" in response.text
    assert "abc" not in response.text


def test_preview_is_bounded_and_truncated(tmp_path: Path):
    from armactl.web.services import filesystem

    server = _server_root(tmp_path)
    payload = "start\n" + ("x" * filesystem.MAX_PREVIEW_BYTES) + "tail-secret"
    (server / "large.txt").write_text(payload, encoding="utf-8")
    client = _login_owner(tmp_path)

    response = client.get("/files/server/preview?path=large.txt", follow_redirects=False)

    assert response.status_code == 200
    assert "Output truncated." in response.text
    assert "start" in response.text
    assert "tail-secret" not in response.text


def test_authenticated_owner_can_download_safe_file(tmp_path: Path):
    server = _server_root(tmp_path)
    (server / "safe.txt").write_text("safe file", encoding="utf-8")
    client = _login_owner(tmp_path)

    response = client.get("/files/server/download?path=safe.txt", follow_redirects=False)

    assert response.status_code == 200
    assert response.content == b"safe file"
    disposition = response.headers["content-disposition"]
    assert "attachment" in disposition.lower()
    assert "safe.txt" in disposition


def test_unauthenticated_download_redirects_to_login(tmp_path: Path):
    _server_root(tmp_path)
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    response = client.get("/files/server/download?path=safe.txt", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_download_permission_denied_returns_controlled_403(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app

    password = "owner files password"
    setup_owner_user(tmp_path, "owner", password)
    app = create_app(data_root=tmp_path)
    for route in app.routes:
        if getattr(route, "path", "") == "/files/{root_id}/download":
            monkeypatch.setitem(
                route.endpoint.__globals__,
                "require_permission",
                lambda current, permission: False,
            )
            break
    client = _client(app)
    _login(client, "owner", password)

    response = client.get("/files/server/download?path=safe.txt", follow_redirects=False)

    assert response.status_code == 403
    assert response.text == "Permission denied."
    assert "Traceback" not in response.text


def test_download_unknown_root_is_controlled(tmp_path: Path):
    client = _login_owner(tmp_path)

    response = client.get("/files/not-real/download?path=safe.txt", follow_redirects=False)

    assert response.status_code == 404
    assert response.text == "Unknown file root."
    assert "Traceback" not in response.text


def test_download_dotdot_traversal_is_rejected(tmp_path: Path):
    _server_root(tmp_path)
    client = _login_owner(tmp_path)

    response = client.get("/files/server/download?path=../config", follow_redirects=False)

    assert response.status_code == 400
    assert response.text == "Unsafe file path."
    assert "Traceback" not in response.text


def test_download_absolute_path_is_rejected(tmp_path: Path):
    _server_root(tmp_path)
    client = _login_owner(tmp_path)

    response = client.get("/files/server/download?path=/etc/passwd", follow_redirects=False)

    assert response.status_code == 400
    assert response.text == "Unsafe file path."
    assert "Traceback" not in response.text


def test_download_symlink_escape_is_rejected(tmp_path: Path):
    server = _server_root(tmp_path)
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("outside", encoding="utf-8")
    try:
        (server / "escape.txt").symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    client = _login_owner(tmp_path)

    response = client.get("/files/server/download?path=escape.txt", follow_redirects=False)

    assert response.status_code == 400
    assert response.text == "Unsafe file path."
    assert "outside" not in response.text


def test_directory_download_is_rejected(tmp_path: Path):
    server = _server_root(tmp_path)
    (server / "subdir").mkdir()
    client = _login_owner(tmp_path)

    response = client.get("/files/server/download?path=subdir", follow_redirects=False)

    assert response.status_code == 400
    assert response.text == "Download unavailable."
    assert "Traceback" not in response.text


def test_git_and_venv_paths_are_not_downloadable(tmp_path: Path):
    server = _server_root(tmp_path)
    (server / ".git").mkdir()
    (server / ".git" / "secret.txt").write_text("git secret", encoding="utf-8")
    (server / ".venv").mkdir()
    (server / ".venv" / "secret.txt").write_text("venv secret", encoding="utf-8")
    client = _login_owner(tmp_path)

    git_response = client.get(
        "/files/server/download?path=.git/secret.txt",
        follow_redirects=False,
    )
    venv_response = client.get(
        "/files/server/download?path=.venv/secret.txt",
        follow_redirects=False,
    )

    assert git_response.status_code == 400
    assert git_response.text == "Unsafe file path."
    assert "git secret" not in git_response.text
    assert venv_response.status_code == 400
    assert venv_response.text == "Unsafe file path."
    assert "venv secret" not in venv_response.text


def test_download_content_disposition_uses_safe_basename(tmp_path: Path):
    server = _server_root(tmp_path)
    (server / "nested").mkdir()
    (server / "nested" / "report.txt").write_text("report", encoding="utf-8")
    client = _login_owner(tmp_path)

    response = client.get("/files/server/download?path=nested/report.txt", follow_redirects=False)

    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert "attachment" in disposition.lower()
    assert "report.txt" in disposition
    assert "nested" not in disposition
    assert ".." not in disposition


def test_authenticated_owner_can_upload_new_file(tmp_path: Path):
    server = _server_root(tmp_path)
    client = _login_owner(tmp_path)
    token = _files_csrf_token(client)

    response = client.post(
        "/files/server/upload",
        data={"path": "", "csrf_token": token},
        files={"upload": ("new.txt", b"uploaded content", "text/plain")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/files/server"
    assert (server / "new.txt").read_bytes() == b"uploaded content"


def test_upload_form_visible_for_owner(tmp_path: Path):
    _server_root(tmp_path)
    client = _login_owner(tmp_path)

    response = client.get("/files/server", follow_redirects=False)

    assert response.status_code == 200
    assert 'action="/files/server/upload#file-browser"' in response.text
    assert 'enctype="multipart/form-data"' in response.text
    assert 'name="upload"' in response.text


def test_upload_form_hidden_for_read_only_roots(tmp_path: Path):
    _config_root(tmp_path)
    client = _login_owner(tmp_path)

    response = client.get("/files/config", follow_redirects=False)

    assert response.status_code == 200
    assert 'action="/files/config/upload"' not in response.text
    assert 'name="upload"' not in response.text


def test_unauthenticated_upload_redirects_to_login(tmp_path: Path):
    _server_root(tmp_path)
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    response = client.post(
        "/files/server/upload",
        data={"path": "", "csrf_token": "missing"},
        files={"upload": ("new.txt", b"content", "text/plain")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_upload_permission_denied_without_files_write(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app
    from armactl.web.auth.permissions import FILES_READ

    _server_root(tmp_path)
    password = "owner files password"
    setup_owner_user(tmp_path, "owner", password)
    app = create_app(data_root=tmp_path)
    for route in app.routes:
        if getattr(route, "path", "") == "/files/{root_id}/upload":
            monkeypatch.setitem(
                route.endpoint.__globals__,
                "require_permission",
                lambda current, permission: permission == FILES_READ,
            )
            break
    client = _client(app)
    _login(client, "owner", password)
    token = _files_csrf_token(client)

    response = client.post(
        "/files/server/upload",
        data={"path": "", "csrf_token": token},
        files={"upload": ("new.txt", b"content", "text/plain")},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.text == "Permission denied."
    assert "Traceback" not in response.text


def test_upload_to_read_only_root_is_rejected(tmp_path: Path):
    config = _config_root(tmp_path)
    client = _login_owner(tmp_path)
    token = _files_csrf_token(client, "/files/config")

    response = client.post(
        "/files/config/upload",
        data={"path": "", "csrf_token": token},
        files={"upload": ("config.json", b"raw config", "application/json")},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert response.text == "Upload unavailable."
    assert not (config / "config.json").exists()


def test_upload_requires_csrf(tmp_path: Path):
    server = _server_root(tmp_path)
    client = _login_owner(tmp_path)

    response = client.post(
        "/files/server/upload",
        data={"path": "", "csrf_token": "bad-token"},
        files={"upload": ("new.txt", b"content", "text/plain")},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.text == "Invalid CSRF token."
    assert not (server / "new.txt").exists()


def test_upload_traversal_destination_is_rejected(tmp_path: Path):
    server = _server_root(tmp_path)
    client = _login_owner(tmp_path)
    token = _files_csrf_token(client)

    response = client.post(
        "/files/server/upload",
        data={"path": "../config", "csrf_token": token},
        files={"upload": ("evil.txt", b"evil", "text/plain")},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert response.text == "Unsafe file path."
    assert not (server / "evil.txt").exists()


def test_upload_symlink_destination_escape_is_rejected(tmp_path: Path):
    server = _server_root(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (server / "escape").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    client = _login_owner(tmp_path)
    token = _files_csrf_token(client)

    response = client.post(
        "/files/server/upload",
        data={"path": "escape", "csrf_token": token},
        files={"upload": ("evil.txt", b"evil", "text/plain")},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert response.text == "Unsafe file path."
    assert not (outside / "evil.txt").exists()


def test_upload_invalid_filename_is_rejected(tmp_path: Path):
    from io import BytesIO

    server = _server_root(tmp_path)
    from armactl.web.services import filesystem

    with pytest.raises(filesystem.InvalidUploadFilenameError):
        filesystem.upload_file(tmp_path, "server", "", "bad/name.txt", BytesIO(b"bad"))

    assert not (server / "name.txt").exists()


def test_upload_existing_target_is_rejected_without_overwrite(tmp_path: Path):
    server = _server_root(tmp_path)
    target = server / "new.txt"
    target.write_text("original", encoding="utf-8")
    client = _login_owner(tmp_path)
    token = _files_csrf_token(client)

    response = client.post(
        "/files/server/upload",
        data={"path": "", "csrf_token": token},
        files={"upload": ("new.txt", b"replacement", "text/plain")},
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert response.text == "File already exists."
    assert target.read_text(encoding="utf-8") == "original"


def test_upload_too_large_is_rejected_without_partial_file(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app

    server = _server_root(tmp_path)
    password = "owner files password"
    setup_owner_user(tmp_path, "owner", password)
    app = create_app(data_root=tmp_path)
    for route in app.routes:
        if getattr(route, "path", "") == "/files/{root_id}/upload":
            monkeypatch.setattr(route.endpoint.__globals__["filesystem"], "MAX_UPLOAD_BYTES", 4)
            break
    client = _client(app)
    _login(client, "owner", password)
    token = _files_csrf_token(client)

    response = client.post(
        "/files/server/upload",
        data={"path": "", "csrf_token": token},
        files={"upload": ("large.bin", b"too-large", "application/octet-stream")},
        follow_redirects=False,
    )

    assert response.status_code == 413
    assert response.text == "Upload too large."
    assert not (server / "large.bin").exists()
    assert not any(path.name.startswith(".armactl-upload-") for path in server.iterdir())


def test_upload_into_file_path_is_rejected(tmp_path: Path):
    server = _server_root(tmp_path)
    (server / "not-dir.txt").write_text("not a directory", encoding="utf-8")
    client = _login_owner(tmp_path)
    token = _files_csrf_token(client)

    response = client.post(
        "/files/server/upload",
        data={"path": "not-dir.txt", "csrf_token": token},
        files={"upload": ("new.txt", b"content", "text/plain")},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert response.text == "Upload unavailable."
    assert not (server / "not-dir.txt" / "new.txt").exists()


def test_upload_git_and_venv_destination_or_filename_rejected(tmp_path: Path):
    server = _server_root(tmp_path)
    (server / ".venv").mkdir()
    client = _login_owner(tmp_path)
    token = _files_csrf_token(client)

    dest_response = client.post(
        "/files/server/upload",
        data={"path": ".venv", "csrf_token": token},
        files={"upload": ("safe.txt", b"safe", "text/plain")},
        follow_redirects=False,
    )
    filename_response = client.post(
        "/files/server/upload",
        data={"path": "", "csrf_token": token},
        files={"upload": (".git", b"bad", "text/plain")},
        follow_redirects=False,
    )

    assert dest_response.status_code == 400
    assert dest_response.text == "Unsafe file path."
    assert not (server / ".venv" / "safe.txt").exists()
    assert filename_response.status_code == 400
    assert filename_response.text == "Invalid filename."
    assert not (server / ".git").is_file()


def test_upload_form_hidden_without_files_write(tmp_path: Path, monkeypatch):
    from armactl.web.app import create_app
    from armactl.web.auth.permissions import FILES_READ

    _server_root(tmp_path)
    password = "owner files password"
    setup_owner_user(tmp_path, "owner", password)
    app = create_app(data_root=tmp_path)
    for route in app.routes:
        if getattr(route, "path", "") == "/files/{root_id}":
            monkeypatch.setitem(
                route.endpoint.__globals__,
                "require_permission",
                lambda current, permission: permission == FILES_READ,
            )
            break
    client = _client(app)
    _login(client, "owner", password)

    response = client.get("/files/server", follow_redirects=False)

    assert response.status_code == 200
    assert 'action="/files/server/upload"' not in response.text
    assert 'name="upload"' not in response.text
