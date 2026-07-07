"""Tests for the read-only web file browser foundation."""

from __future__ import annotations

import importlib
import json
import re
import warnings
from pathlib import Path

import pytest
from starlette.exceptions import StarletteDeprecationWarning

from armactl.web.auth.cookies import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME
from armactl.web.auth.setup import setup_owner_user


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


def _audit_events(data_root: Path) -> list[dict]:
    audit_path = data_root / "logs" / "web" / "audit.log"
    return [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]


def test_file_adapter_import_does_not_import_tui_textual(
    assert_import_does_not_import_modules,
):
    forbidden = ("armactl.tui", "textual")
    assert_import_does_not_import_modules("armactl.web.services.filesystem", forbidden)


def test_filesystem_legacy_facade_reexports_split_service_api():
    filesystem = importlib.import_module("armactl.web.services.filesystem")
    errors = importlib.import_module("armactl.web.services.filesystem_errors")
    listing = importlib.import_module("armactl.web.services.filesystem_listing")
    paths = importlib.import_module("armactl.web.services.filesystem_paths")
    preview = importlib.import_module("armactl.web.services.filesystem_preview")
    roots = importlib.import_module("armactl.web.services.filesystem_roots")
    transfer = importlib.import_module("armactl.web.services.filesystem_transfer")
    replacements = importlib.import_module("armactl.web.services.file_replacements")

    assert filesystem.FileBrowserError is errors.FileBrowserError
    assert filesystem.DirectoryListing is listing.DirectoryListing
    assert filesystem.FileMetadata is listing.FileMetadata
    assert filesystem.list_directory is listing.list_directory
    assert filesystem.ResolvedBrowserPath is paths.ResolvedBrowserPath
    assert filesystem.resolve_browser_path is paths.resolve_browser_path
    assert filesystem.FilePreview is preview.FilePreview
    assert filesystem.preview_text_file is preview.preview_text_file
    assert filesystem.FileRoot is roots.FileRoot
    assert filesystem.list_allowed_roots is roots.list_allowed_roots
    assert filesystem.DownloadFile is transfer.DownloadFile
    assert filesystem.upload_file is transfer.upload_file
    assert filesystem.EditableReplacement is replacements.EditableReplacement
    assert filesystem.read_editable_replacement_text is replacements.read_editable_replacement_text
    assert filesystem.replace_file_and_audit is replacements.replace_file_and_audit
    assert filesystem.replace_text_and_audit is replacements.replace_text_and_audit


def test_files_route_import_does_not_import_tui_textual(
    assert_import_does_not_import_modules,
):
    forbidden = ("armactl.tui", "textual")
    assert_import_does_not_import_modules("armactl.web.routes.files", forbidden)


def test_unauthenticated_files_redirects_to_login(tmp_path: Path):
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    response = client.get("/files", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_files_permission_denied_returns_controlled_403(
    tmp_path: Path, set_web_owner_permissions
):
    from armactl.web.app import create_app

    password = "owner files password"
    setup_owner_user(tmp_path, "owner", password)
    set_web_owner_permissions(set())
    app = create_app(data_root=tmp_path)
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
    from armactl.web.services.filesystem_roots import list_allowed_roots

    roots = list_allowed_roots(paths.project_root())

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
    from armactl.web.services.filesystem_preview import MAX_PREVIEW_BYTES

    server = _server_root(tmp_path)
    payload = "start\n" + ("x" * MAX_PREVIEW_BYTES) + "tail-secret"
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


def test_download_permission_denied_returns_controlled_403(
    tmp_path: Path, set_web_owner_permissions
):
    from armactl.web.app import create_app

    password = "owner files password"
    setup_owner_user(tmp_path, "owner", password)
    set_web_owner_permissions(set())
    app = create_app(data_root=tmp_path)
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

    events = _audit_events(tmp_path)

    assert response.status_code == 303
    assert response.headers["location"] == "/files/server"
    assert (server / "new.txt").read_bytes() == b"uploaded content"
    assert events[-1]["username"] == "owner"
    assert events[-1]["action"] == "file.upload"
    assert events[-1]["target"] == "server:new.txt"
    assert events[-1]["message"] == "File upload published."
    assert events[-1]["details"] == {
        "phase": "outcome",
        "path": "new.txt",
        "root": "server",
        "size": "16",
    }


def test_upload_audit_failure_does_not_publish_file_and_keeps_path_guards(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import file_uploads
    from armactl.web.services.audit import AuditLogError

    server = _server_root(tmp_path)
    client = _login_owner(tmp_path)
    token = _files_csrf_token(client)

    def fail_audit(*args, **kwargs):
        raise AuditLogError("disk full")

    monkeypatch.setattr(file_uploads, "append_audit_event", fail_audit)

    response = client.post(
        "/files/server/upload",
        data={"path": "", "csrf_token": token},
        files={"upload": ("new.txt", b"uploaded content", "text/plain")},
        follow_redirects=False,
    )

    assert response.status_code == 500
    assert response.text == "File upload was not published because audit logging failed."
    assert "Traceback" not in response.text
    assert not (server / "new.txt").exists()
    assert not any(path.name.startswith(".armactl-upload-") for path in server.iterdir())

    traversal_response = client.post(
        "/files/server/upload",
        data={"path": "../config", "csrf_token": token},
        files={"upload": ("evil.txt", b"evil", "text/plain")},
        follow_redirects=False,
    )

    assert traversal_response.status_code == 400
    assert traversal_response.text == "Unsafe file path."
    assert not (server / "evil.txt").exists()

    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (server / "escape").symlink_to(outside, target_is_directory=True)
    except OSError:
        return

    symlink_response = client.post(
        "/files/server/upload",
        data={"path": "escape", "csrf_token": token},
        files={"upload": ("evil.txt", b"evil", "text/plain")},
        follow_redirects=False,
    )

    assert symlink_response.status_code == 400
    assert symlink_response.text == "Unsafe file path."
    assert not (outside / "evil.txt").exists()


def test_upload_publish_failure_after_audit_is_controlled_and_audited(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import file_uploads
    from armactl.web.services.filesystem_errors import UploadUnavailableError

    server = _server_root(tmp_path)
    client = _login_owner(tmp_path)
    token = _files_csrf_token(client)
    audit_actions: list[tuple[str, bool]] = []

    def record_audit(audit_log_path, *, action, success, **kwargs):
        audit_actions.append((action, success))

    def fail_publish(staged):
        raise UploadUnavailableError(UploadUnavailableError.public_message)

    monkeypatch.setattr(file_uploads, "append_audit_event", record_audit)
    monkeypatch.setattr(file_uploads, "publish_staged_upload", fail_publish)

    response = client.post(
        "/files/server/upload",
        data={"path": "", "csrf_token": token},
        files={"upload": ("new.txt", b"uploaded content", "text/plain")},
        follow_redirects=False,
    )

    assert response.status_code == 500
    assert response.text == "File upload was audited but publishing failed."
    assert "Traceback" not in response.text
    assert not (server / "new.txt").exists()
    assert not any(path.name.startswith(".armactl-upload-") for path in server.iterdir())
    assert audit_actions == [("file.upload", True), ("file.upload.publish-failed", False)]


def test_upload_success_outcome_audit_failure_reports_published_file(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import file_uploads
    from armactl.web.services.audit import AuditLogError

    server = _server_root(tmp_path)
    client = _login_owner(tmp_path)
    token = _files_csrf_token(client)
    audit_phases: list[tuple[str, str]] = []

    def fail_success_outcome(audit_log_path, *, action, details, **kwargs):
        phase = str((details or {}).get("phase") or "")
        audit_phases.append((action, phase))
        if action == "file.upload" and phase == "outcome":
            raise AuditLogError("disk full token=raw-upload-secret")

    monkeypatch.setattr(file_uploads, "append_audit_event", fail_success_outcome)

    response = client.post(
        "/files/server/upload",
        data={"path": "", "csrf_token": token},
        files={"upload": ("new.txt", b"uploaded content", "text/plain")},
        follow_redirects=False,
    )

    assert response.status_code == 500
    assert response.text == "File upload was published but audit logging failed."
    assert (server / "new.txt").read_bytes() == b"uploaded content"
    assert not any(path.name.startswith(".armactl-upload-") for path in server.iterdir())
    assert audit_phases == [("file.upload", "intent"), ("file.upload", "outcome")]
    assert "raw-upload-secret" not in response.text
    assert "Traceback" not in response.text


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


def test_upload_permission_denied_without_files_write(
    tmp_path: Path, set_web_owner_permissions
):
    from armactl.web.app import create_app
    from armactl.web.auth.permissions import FILES_READ

    _server_root(tmp_path)
    password = "owner files password"
    setup_owner_user(tmp_path, "owner", password)
    set_web_owner_permissions({FILES_READ})
    app = create_app(data_root=tmp_path)
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

    from armactl.web.services.filesystem_errors import InvalidUploadFilenameError
    from armactl.web.services.filesystem_transfer import upload_file

    server = _server_root(tmp_path)

    with pytest.raises(InvalidUploadFilenameError):
        upload_file(tmp_path, "server", "", "bad/name.txt", BytesIO(b"bad"))

    assert not (server / "name.txt").exists()


def test_stage_upload_does_not_publish_until_explicit_publish(tmp_path: Path):
    from io import BytesIO

    from armactl.web.services.filesystem_transfer import (
        cleanup_staged_upload,
        publish_staged_upload,
        stage_upload_file,
    )

    server = _server_root(tmp_path)

    staged = stage_upload_file(
        tmp_path,
        "server",
        "",
        "new.txt",
        BytesIO(b"uploaded content"),
    )

    assert staged.temp_path.exists()
    assert staged.temp_path.name.startswith(".armactl-upload-")
    assert not (server / "new.txt").exists()

    uploaded = publish_staged_upload(staged)

    assert uploaded.path == server / "new.txt"
    assert uploaded.path.read_bytes() == b"uploaded content"
    assert cleanup_staged_upload(staged) is True
    assert not staged.temp_path.exists()


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
    from armactl.web.services import filesystem_transfer

    server = _server_root(tmp_path)
    password = "owner files password"
    setup_owner_user(tmp_path, "owner", password)
    app = create_app(data_root=tmp_path)
    monkeypatch.setattr(filesystem_transfer, "MAX_UPLOAD_BYTES", 4)
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


def test_upload_form_hidden_without_files_write(tmp_path: Path, set_web_owner_permissions):
    from armactl.web.app import create_app
    from armactl.web.auth.permissions import FILES_READ

    _server_root(tmp_path)
    password = "owner files password"
    setup_owner_user(tmp_path, "owner", password)
    set_web_owner_permissions({FILES_READ})
    app = create_app(data_root=tmp_path)
    client = _client(app)
    _login(client, "owner", password)

    response = client.get("/files/server", follow_redirects=False)

    assert response.status_code == 200
    assert 'action="/files/server/upload"' not in response.text
    assert 'name="upload"' not in response.text


def _sample_server_config() -> dict:
    return {
        "bindAddress": "0.0.0.0",
        "bindPort": 2001,
        "publicAddress": "",
        "publicPort": 2001,
        "a2s": {"address": "0.0.0.0", "port": 17777},
        "rcon": {
            "address": "0.0.0.0",
            "port": 19999,
            "password": "raw-rcon-secret",
            "permission": "admin",
        },
        "game": {
            "name": "Original Server",
            "password": "",
            "passwordAdmin": "raw-admin-secret",
            "admins": [],
            "scenarioId": "Scenario.conf",
            "maxPlayers": 32,
            "visible": True,
            "gameProperties": {
                "serverMaxViewDistance": 1600,
                "serverMinGrassDistance": 30,
                "disableThirdPerson": True,
                "battlEye": False,
            },
        },
    }


def _write_default_config_json(config_root: Path, config: dict | None = None) -> Path:
    path = config_root / "config.json"
    path.write_text(json.dumps(config or _sample_server_config(), indent=2), encoding="utf-8")
    return path


def _replace_token(client) -> str:
    return _files_csrf_token(client, "/files/config")


def _post_replace(
    client,
    csrf_token: str,
    relative_path: str,
    content: bytes,
    *,
    filename: str = "replacement.txt",
    root_id: str = "config",
):
    return client.post(
        f"/files/{root_id}/replace",
        data={"path": relative_path, "csrf_token": csrf_token},
        files={"replacement": (filename, content, "text/plain")},
        follow_redirects=False,
    )


def _replacement_backups(data_root: Path) -> list[Path]:
    backup_root = data_root / "default" / "backups" / "file-replacements"
    return sorted(backup_root.glob("*.bak"))


def test_read_editable_replacement_text_returns_safe_dto_for_allowed_targets(
    tmp_path: Path,
):
    from armactl.web.services.file_replacements import read_editable_replacement_text

    config = _config_root(tmp_path)
    profile_path = config / "profile.cfg"
    profile_path.write_text("hostname = Test Server\n", encoding="utf-8")
    config_path = _write_default_config_json(config)

    profile = read_editable_replacement_text(tmp_path, "config", "profile.cfg")
    config_json = read_editable_replacement_text(tmp_path, "config", "config.json")

    assert profile.root_id == "config"
    assert profile.relative_path == "profile.cfg"
    assert profile.display_name == "profile.cfg"
    assert profile.breadcrumbs[-1] == "profile.cfg"
    assert profile.file_kind == "profile-file"
    assert profile.size == profile_path.stat().st_size
    assert profile.text == "hostname = Test Server\n"
    assert profile.baseline_fingerprint.startswith("sha256:")

    assert config_json.root_id == "config"
    assert config_json.relative_path == "config.json"
    assert config_json.file_kind == "config-json"
    assert config_json.size == config_path.stat().st_size
    assert "<redacted: unchanged>" in config_json.text
    assert "raw-rcon-secret" not in config_json.text
    assert "raw-admin-secret" not in config_json.text
    assert config_json.baseline_fingerprint.startswith("sha256:")

    for dto in (profile, config_json):
        safe_projection = json.dumps(dto.__dict__, ensure_ascii=False, default=str)
        assert str(tmp_path) not in safe_projection
        assert str(config) not in safe_projection


@pytest.mark.parametrize(
    "relative_path",
    [
        "../server/evil.cfg",
        "/etc/passwd",
        ".git/secret.txt",
        ".venv/secret.txt",
        "missing.cfg",
        "profile",
        "profile/OtherTool/state.json",
        "AdminServerSettings/nested/admins.json",
    ],
)
def test_read_editable_replacement_text_rejects_unsafe_or_unavailable_targets(
    tmp_path: Path,
    relative_path: str,
):
    from armactl.web.services.file_replacements import read_editable_replacement_text
    from armactl.web.services.filesystem_errors import FileBrowserError

    config = _config_root(tmp_path)
    (config / ".git").mkdir()
    (config / ".git" / "secret.txt").write_text("git", encoding="utf-8")
    (config / ".venv").mkdir()
    (config / ".venv" / "secret.txt").write_text("venv", encoding="utf-8")
    (config / "profile").mkdir()
    deep = config / "profile" / "OtherTool" / "state.json"
    deep.parent.mkdir(parents=True)
    deep.write_text('{"ok": true}\n', encoding="utf-8")
    too_deep = config / "AdminServerSettings" / "nested" / "admins.json"
    too_deep.parent.mkdir(parents=True)
    too_deep.write_text('{"admins": []}\n', encoding="utf-8")

    with pytest.raises(FileBrowserError) as error:
        read_editable_replacement_text(tmp_path, "config", relative_path)

    assert str(tmp_path) not in str(error.value)


def test_read_editable_replacement_text_rejects_symlink_target(tmp_path: Path):
    from armactl.web.services.file_replacements import read_editable_replacement_text
    from armactl.web.services.filesystem_errors import FileBrowserError

    config = _config_root(tmp_path)
    outside = tmp_path / "outside.cfg"
    outside.write_text("outside", encoding="utf-8")
    try:
        (config / "profile.cfg").symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(FileBrowserError):
        read_editable_replacement_text(tmp_path, "config", "profile.cfg")


@pytest.mark.parametrize(
    "payload",
    [b"abc\x00def", b"abc\x01def", b"\xff", b"password=hunter2\n"],
)
def test_read_editable_replacement_text_rejects_unsafe_text_payloads(
    tmp_path: Path,
    payload: bytes,
):
    from armactl.web.services.file_replacements import read_editable_replacement_text
    from armactl.web.services.filesystem_errors import ReplacementInvalidContentError

    config = _config_root(tmp_path)
    (config / "profile.cfg").write_bytes(payload)

    with pytest.raises(ReplacementInvalidContentError) as error:
        read_editable_replacement_text(tmp_path, "config", "profile.cfg")

    assert "hunter2" not in str(error.value)
    assert str(tmp_path) not in str(error.value)


def test_read_editable_replacement_text_rejects_oversize_file(tmp_path: Path):
    from armactl.web.services.file_replacements import read_editable_replacement_text
    from armactl.web.services.filesystem_errors import ReplacementTooLargeError

    config = _config_root(tmp_path)
    (config / "profile.cfg").write_text("12345", encoding="utf-8")

    with pytest.raises(ReplacementTooLargeError):
        read_editable_replacement_text(
            tmp_path,
            "config",
            "profile.cfg",
            max_bytes=4,
        )


def test_replace_text_stale_baseline_rejects_before_mutation_work(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import file_replacements
    from armactl.web.services.filesystem_errors import ReplacementStaleBaselineError

    config = _config_root(tmp_path)
    target = config / "profile.cfg"
    target.write_text("before\n", encoding="utf-8")
    editable = file_replacements.read_editable_replacement_text(
        tmp_path,
        "config",
        "profile.cfg",
    )
    target.write_text("external change\n", encoding="utf-8")
    audit_calls: list[str] = []

    def record_audit(*args, **kwargs):
        audit_calls.append("audit")

    monkeypatch.setattr(file_replacements, "append_audit_event", record_audit)
    monkeypatch.setattr(file_replacements, "create_replacement_backup", pytest.fail)
    monkeypatch.setattr(file_replacements, "publish_staged_replacement", pytest.fail)

    with pytest.raises(ReplacementStaleBaselineError) as error:
        file_replacements.replace_text_and_audit(
            tmp_path,
            "config",
            "profile.cfg",
            "operator save\n",
            expected_baseline_fingerprint=editable.baseline_fingerprint,
            audit_log_path=tmp_path / "logs" / "web" / "audit.log",
            username="owner",
            db_path=tmp_path / "web" / "web.db",
        )

    assert str(error.value) == "File changed on disk. Reload before saving."
    assert target.read_text(encoding="utf-8") == "external change\n"
    assert audit_calls == []
    assert _replacement_backups(tmp_path) == []


def test_replace_text_noop_save_has_no_backup_audit_or_pending_restart(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import file_replacements, pending_work

    config = _config_root(tmp_path)
    target = config / "profile.cfg"
    target.write_text("before\n", encoding="utf-8")
    editable = file_replacements.read_editable_replacement_text(
        tmp_path,
        "config",
        "profile.cfg",
    )

    monkeypatch.setattr(file_replacements, "append_audit_event", pytest.fail)
    monkeypatch.setattr(file_replacements, "create_replacement_backup", pytest.fail)
    monkeypatch.setattr(
        file_replacements,
        "_mark_restart_pending_for_replacement",
        pytest.fail,
    )

    result = file_replacements.replace_text_and_audit(
        tmp_path,
        "config",
        "profile.cfg",
        editable.text,
        expected_baseline_fingerprint=editable.baseline_fingerprint,
        audit_log_path=tmp_path / "logs" / "web" / "audit.log",
        username="owner",
        db_path=tmp_path / "web" / "web.db",
    )

    assert result.backup_path is None
    assert result.changed_fields == ()
    assert target.read_text(encoding="utf-8") == "before\n"
    assert _replacement_backups(tmp_path) == []
    assert not (tmp_path / "logs" / "web" / "audit.log").exists()
    assert pending_work.list_pending_work(tmp_path / "web" / "web.db") == []


def test_replace_text_non_config_json_invalid_json_rejected_before_publish(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import file_replacements
    from armactl.web.services.filesystem_errors import ReplacementInvalidContentError

    config = _config_root(tmp_path)
    target = config / "profile.json"
    target.write_text('{"ok": true}\n', encoding="utf-8")
    editable = file_replacements.read_editable_replacement_text(
        tmp_path,
        "config",
        "profile.json",
    )

    monkeypatch.setattr(file_replacements, "append_audit_event", pytest.fail)
    monkeypatch.setattr(file_replacements, "create_replacement_backup", pytest.fail)
    monkeypatch.setattr(file_replacements, "publish_staged_replacement", pytest.fail)

    with pytest.raises(ReplacementInvalidContentError) as error:
        file_replacements.replace_text_and_audit(
            tmp_path,
            "config",
            "profile.json",
            '{"ok":',
            expected_baseline_fingerprint=editable.baseline_fingerprint,
            audit_log_path=tmp_path / "logs" / "web" / "audit.log",
            username="owner",
            db_path=tmp_path / "web" / "web.db",
        )

    assert "Invalid JSON replacement" in str(error.value)
    assert target.read_text(encoding="utf-8") == '{"ok": true}\n'
    assert _replacement_backups(tmp_path) == []


def test_replace_text_config_json_reuses_secret_restore_reject_and_validation(
    tmp_path: Path,
):
    from armactl.web.services import file_replacements
    from armactl.web.services.filesystem_errors import ReplacementInvalidContentError

    config_root = _config_root(tmp_path)
    original = _sample_server_config()
    config_path = _write_default_config_json(config_root, original)
    editable = file_replacements.read_editable_replacement_text(
        tmp_path,
        "config",
        "config.json",
    )
    assert "raw-rcon-secret" not in editable.text
    assert "raw-admin-secret" not in editable.text

    secret_change = json.loads(editable.text)
    secret_change["rcon"]["password"] = "changed-secret"
    with pytest.raises(ReplacementInvalidContentError) as error:
        file_replacements.replace_text_and_audit(
            tmp_path,
            "config",
            "config.json",
            json.dumps(secret_change),
            expected_baseline_fingerprint=editable.baseline_fingerprint,
            audit_log_path=tmp_path / "logs" / "web" / "audit.log",
            username="owner",
            db_path=tmp_path / "web" / "web.db",
        )

    assert "Secret fields cannot be changed" in str(error.value)
    assert "changed-secret" not in str(error.value)
    assert json.loads(config_path.read_text(encoding="utf-8")) == original
    assert _replacement_backups(tmp_path) == []

    submitted = json.loads(editable.text)
    submitted["game"]["name"] = "Changed"
    result = file_replacements.replace_text_and_audit(
        tmp_path,
        "config",
        "config.json",
        json.dumps(submitted, indent=2),
        expected_baseline_fingerprint=editable.baseline_fingerprint,
        audit_log_path=tmp_path / "logs" / "web" / "audit.log",
        username="owner",
        db_path=tmp_path / "web" / "web.db",
    )

    updated = json.loads(config_path.read_text(encoding="utf-8"))
    assert updated["game"]["name"] == "Changed"
    assert updated["rcon"]["password"] == original["rcon"]["password"]
    assert updated["game"]["passwordAdmin"] == original["game"]["passwordAdmin"]
    assert result.changed_fields == ("game.name",)
    assert len(_replacement_backups(tmp_path)) == 1


def test_replace_text_success_reuses_backup_publish_audit_and_pending_restart(
    tmp_path: Path,
):
    from armactl.web.services import file_replacements, pending_work

    config = _config_root(tmp_path)
    target = config / "profile.cfg"
    target.write_text("before\n", encoding="utf-8")
    editable = file_replacements.read_editable_replacement_text(
        tmp_path,
        "config",
        "profile.cfg",
    )

    result = file_replacements.replace_text_and_audit(
        tmp_path,
        "config",
        "profile.cfg",
        "after\n",
        expected_baseline_fingerprint=editable.baseline_fingerprint,
        audit_log_path=tmp_path / "logs" / "web" / "audit.log",
        username="owner",
        db_path=tmp_path / "web" / "web.db",
    )

    assert target.read_text(encoding="utf-8") == "after\n"
    backups = _replacement_backups(tmp_path)
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "before\n"
    assert result.backup_path == backups[0]
    assert result.changed_fields == ("profile_file",)
    events = _audit_events(tmp_path)[-2:]
    assert [event["details"]["phase"] for event in events] == ["intent", "outcome"]
    assert [event["action"] for event in events] == ["file.replace", "file.replace"]
    assert events[0]["target"] == "config:profile.cfg"
    assert events[1]["details"]["backup_name"] == backups[0].name

    item = pending_work.get_pending_work(
        tmp_path / "web" / "web.db",
        kind=pending_work.KIND_CONFIG,
    )
    assert item is not None
    assert item.source_action == "file.replace"
    assert item.source_path == "/files/config"
    assert item.details == "profile_file"


def test_replace_form_visible_only_for_safe_config_candidates(tmp_path: Path):
    config = _config_root(tmp_path)
    (config / "profile.cfg").write_text("original", encoding="utf-8")
    (config / "logs" / "run").mkdir(parents=True)
    (config / "logs" / "run" / "console.log").write_text("log", encoding="utf-8")
    _write_default_config_json(config)
    _server_root(tmp_path).joinpath("ArmaReforgerServer").write_bytes(b"\x7fELF")
    backups = tmp_path / "default" / "backups"
    backups.mkdir(parents=True)
    (backups / "config.json.old.bak").write_text("backup", encoding="utf-8")
    client = _login_owner(tmp_path)

    config_response = client.get("/files/config", follow_redirects=False)
    logs_response = client.get("/files/config?path=logs/run", follow_redirects=False)
    server_response = client.get("/files/server", follow_redirects=False)
    backups_response = client.get("/files/backups", follow_redirects=False)

    assert config_response.status_code == 200
    assert "action=\"/files/config/replace?path=profile.cfg#file-browser\"" in config_response.text
    assert "action=\"/files/config/replace?path=config.json#file-browser\"" in config_response.text
    assert "delete" in config_response.text.lower()
    assert "action=\"/files/config/delete" not in config_response.text
    assert "name=\"delete\"" not in config_response.text
    assert "action=\"/files/config/replace?path=logs" not in logs_response.text
    assert "action=\"/files/server/replace" not in server_response.text
    assert "action=\"/files/backups/replace" not in backups_response.text


def test_replace_profile_text_file_creates_backup_audit_and_pending_restart(
    tmp_path: Path,
):
    config = _config_root(tmp_path)
    target = config / "profile.cfg"
    target.write_text("original profile\n", encoding="utf-8")
    client = _login_owner(tmp_path)
    token = _replace_token(client)

    response = _post_replace(
        client,
        token,
        "profile.cfg",
        b"updated profile\n",
        filename="profile.cfg",
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/files/config"
    assert target.read_text(encoding="utf-8") == "updated profile\n"
    backups = _replacement_backups(tmp_path)
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "original profile\n"
    assert backups[0].is_relative_to(tmp_path / "default" / "backups")

    events = _audit_events(tmp_path)[-2:]
    assert [event["details"]["phase"] for event in events] == ["intent", "outcome"]
    assert [event["action"] for event in events] == ["file.replace", "file.replace"]
    assert events[0]["target"] == "config:profile.cfg"
    assert events[1]["details"]["backup_name"] == backups[0].name
    audit_text = (tmp_path / "logs" / "web" / "audit.log").read_text(encoding="utf-8")
    assert str(target) not in audit_text
    assert str(backups[0]) not in audit_text

    from armactl.web.services.pending_work import KIND_CONFIG, get_pending_work

    item = get_pending_work(tmp_path / "web" / "web.db", kind=KIND_CONFIG)
    assert item is not None
    assert item.source_action == "file.replace"
    assert item.source_path == "/files/config"
    assert item.title == "Config/profile file changes"
    assert item.details == "profile_file"


def test_replace_nested_cm_player_stats_profile_json_file(
    tmp_path: Path,
):
    config = _config_root(tmp_path)
    stats_dir = config / "profile" / "CMPlayerStatsHUD"
    stats_dir.mkdir(parents=True)
    target = stats_dir / "0109fcf5-a861-4002-881e-8a497c59797c_playerstats.json"
    target.write_text('{"kills": 1}\n', encoding="utf-8")
    client = _login_owner(tmp_path)

    listing = client.get(
        "/files/config?path=profile%2FCMPlayerStatsHUD",
        follow_redirects=False,
    )
    token = _replace_token(client)
    response = _post_replace(
        client,
        token,
        "profile/CMPlayerStatsHUD/0109fcf5-a861-4002-881e-8a497c59797c_playerstats.json",
        b'{"kills": 2}\n',
        filename="0109fcf5-a861-4002-881e-8a497c59797c_playerstats.json",
    )

    assert listing.status_code == 200
    assert (
        'action="/files/config/replace?path='
        "profile%2FCMPlayerStatsHUD%2F0109fcf5-a861-4002-881e-8a497c59797c_playerstats.json"
        '#file-browser"'
    ) in listing.text
    assert response.status_code == 303
    assert response.headers["location"] == "/files/config?path=profile%2FCMPlayerStatsHUD"
    assert json.loads(target.read_text(encoding="utf-8")) == {"kills": 2}
    backups = _replacement_backups(tmp_path)
    assert len(backups) == 1
    assert json.loads(backups[0].read_text(encoding="utf-8")) == {"kills": 1}
    assert "profile__CMPlayerStatsHUD" in backups[0].name
    events = _audit_events(tmp_path)[-2:]
    assert events[0]["target"] == (
        "config:profile/CMPlayerStatsHUD/"
        "0109fcf5-a861-4002-881e-8a497c59797c_playerstats.json"
    )
    assert events[1]["details"]["file_kind"] == "profile-file"


def test_replace_admin_server_settings_json_file(
    tmp_path: Path,
):
    config = _config_root(tmp_path)
    settings_dir = config / "AdminServerSettings"
    settings_dir.mkdir(parents=True)
    target = settings_dir / "admins.json"
    target.write_text('{"admins": []}\n', encoding="utf-8")
    client = _login_owner(tmp_path)

    listing = client.get(
        "/files/config?path=AdminServerSettings",
        follow_redirects=False,
    )
    token = _replace_token(client)
    response = _post_replace(
        client,
        token,
        "AdminServerSettings/admins.json",
        b'{"admins": ["76561198000000001"]}\n',
        filename="admins.json",
    )

    assert listing.status_code == 200
    assert (
        'action="/files/config/replace?path=AdminServerSettings%2Fadmins.json'
        '#file-browser"'
    ) in listing.text
    assert response.status_code == 303
    assert response.headers["location"] == "/files/config?path=AdminServerSettings"
    assert json.loads(target.read_text(encoding="utf-8")) == {
        "admins": ["76561198000000001"]
    }
    backups = _replacement_backups(tmp_path)
    assert len(backups) == 1
    assert json.loads(backups[0].read_text(encoding="utf-8")) == {"admins": []}


@pytest.mark.parametrize(
    "relative_path",
    [
        "profile/OtherTool/state.json",
        "profile/CMPlayerStatsHUD/nested/state.json",
        "AdminServerSettings/nested/admins.json",
    ],
)
def test_replace_rejects_unknown_or_too_deep_nested_config_paths(
    tmp_path: Path,
    relative_path: str,
):
    config = _config_root(tmp_path)
    target = config.joinpath(*relative_path.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{"before": true}\n', encoding="utf-8")
    client = _login_owner(tmp_path)
    token = _replace_token(client)

    response = _post_replace(client, token, relative_path, b'{"after": true}\n')

    assert response.status_code == 400
    assert response.text == "File replacement unavailable."
    assert json.loads(target.read_text(encoding="utf-8")) == {"before": True}
    assert _replacement_backups(tmp_path) == []


def test_replace_config_json_reuses_secret_protection_and_preserves_original(
    tmp_path: Path,
):
    config_root = _config_root(tmp_path)
    original = _sample_server_config()
    config_path = _write_default_config_json(config_root, original)
    changed = json.loads(json.dumps(original))
    changed["game"]["name"] = "Changed"
    changed["rcon"]["password"] = "changed-secret"
    client = _login_owner(tmp_path)
    token = _replace_token(client)

    response = _post_replace(
        client,
        token,
        "config.json",
        json.dumps(changed).encode("utf-8"),
        filename="config.json",
    )

    assert response.status_code == 400
    assert "Secret fields cannot be changed in the web config editor." in response.text
    assert "changed-secret" not in response.text
    assert json.loads(config_path.read_text(encoding="utf-8")) == original
    assert _replacement_backups(tmp_path) == []


def test_replace_config_json_allows_valid_non_secret_change_and_tracks_pending(
    tmp_path: Path,
):
    config_root = _config_root(tmp_path)
    original = _sample_server_config()
    config_path = _write_default_config_json(config_root, original)
    changed = json.loads(json.dumps(original))
    changed["game"]["name"] = "Changed"
    client = _login_owner(tmp_path)
    token = _replace_token(client)

    response = _post_replace(
        client,
        token,
        "config.json",
        json.dumps(changed).encode("utf-8"),
        filename="config.json",
    )

    assert response.status_code == 303
    assert json.loads(config_path.read_text(encoding="utf-8"))["game"]["name"] == "Changed"
    backups = _replacement_backups(tmp_path)
    assert len(backups) == 1
    assert json.loads(backups[0].read_text(encoding="utf-8")) == original
    events = _audit_events(tmp_path)[-2:]
    assert events[1]["details"]["file_kind"] == "config-json"
    assert events[1]["details"]["changed_fields"] == ["game.name"]


def test_replace_rejects_read_only_roots_and_server_binaries(tmp_path: Path):
    server = _server_root(tmp_path)
    server_binary = server / "ArmaReforgerServer"
    server_binary.write_bytes(b"\x7fELF")
    backups = tmp_path / "default" / "backups"
    backups.mkdir(parents=True)
    backup_file = backups / "config.json.1.bak"
    backup_file.write_text("backup", encoding="utf-8")
    logs = tmp_path / "logs" / "instances" / "default"
    logs.mkdir(parents=True)
    log_file = logs / "web.log"
    log_file.write_text("log", encoding="utf-8")
    _config_root(tmp_path)
    client = _login_owner(tmp_path)
    token = _replace_token(client)

    server_response = _post_replace(
        client,
        token,
        "ArmaReforgerServer",
        b"text",
        root_id="server",
    )
    backups_response = _post_replace(
        client,
        token,
        "config.json.1.bak",
        b"text",
        root_id="backups",
    )
    logs_response = _post_replace(client, token, "web.log", b"text", root_id="logs")

    assert server_response.status_code == 400
    assert backups_response.status_code == 400
    assert logs_response.status_code == 400
    assert server_binary.read_bytes() == b"\x7fELF"
    assert backup_file.read_text(encoding="utf-8") == "backup"
    assert log_file.read_text(encoding="utf-8") == "log"


@pytest.mark.parametrize(
    "relative_path",
    ["../server/evil.cfg", "/etc/passwd", ".git/secret.txt", ".venv/secret.txt"],
)
def test_replace_rejects_traversal_absolute_git_and_venv_paths(
    tmp_path: Path,
    relative_path: str,
):
    config = _config_root(tmp_path)
    target = config / "profile.cfg"
    target.write_text("original", encoding="utf-8")
    client = _login_owner(tmp_path)
    token = _replace_token(client)

    response = _post_replace(client, token, relative_path, b"updated")

    assert response.status_code == 400
    assert "Traceback" not in response.text
    assert target.read_text(encoding="utf-8") == "original"
    assert _replacement_backups(tmp_path) == []


def test_replace_rejects_symlink_target(tmp_path: Path):
    config = _config_root(tmp_path)
    outside = tmp_path / "outside.cfg"
    outside.write_text("outside", encoding="utf-8")
    try:
        (config / "profile.cfg").symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    client = _login_owner(tmp_path)
    token = _replace_token(client)

    response = _post_replace(client, token, "profile.cfg", b"updated")

    assert response.status_code == 400
    assert response.text in {"File replacement unavailable.", "Unsafe file path."}
    assert outside.read_text(encoding="utf-8") == "outside"
    assert _replacement_backups(tmp_path) == []


def test_replace_invalid_json_binary_and_oversize_preserve_original(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import file_replacements

    config = _config_root(tmp_path)
    json_target = config / "profile.json"
    json_target.write_text("{\"ok\": true}\n", encoding="utf-8")
    binary_target = config / "profile.cfg"
    binary_target.write_text("original cfg", encoding="utf-8")
    large_target = config / "profile.ini"
    large_target.write_text("small", encoding="utf-8")
    client = _login_owner(tmp_path)
    token = _replace_token(client)

    json_response = _post_replace(client, token, "profile.json", b"{\"ok\":")
    binary_response = _post_replace(client, token, "profile.cfg", b"abc\x00def")
    monkeypatch.setattr(file_replacements, "MAX_REPLACEMENT_BYTES", 4)
    large_response = _post_replace(client, token, "profile.ini", b"12345")

    assert json_response.status_code == 400
    assert "Invalid JSON replacement" in json_response.text
    assert binary_response.status_code == 400
    assert binary_response.text == "Replacement file must be UTF-8 text."
    assert large_response.status_code == 413
    assert large_response.text == "Replacement file too large."
    assert json_target.read_text(encoding="utf-8") == "{\"ok\": true}\n"
    assert binary_target.read_text(encoding="utf-8") == "original cfg"
    assert large_target.read_text(encoding="utf-8") == "small"
    assert _replacement_backups(tmp_path) == []
    assert not any(path.name.startswith(".armactl-replace-") for path in config.iterdir())


def test_replace_audits_intent_before_mutation_and_outcome_after(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import file_replacements

    config = _config_root(tmp_path)
    target = config / "profile.cfg"
    target.write_text("before", encoding="utf-8")
    client = _login_owner(tmp_path)
    token = _replace_token(client)
    snapshots: list[tuple[str, str]] = []

    def record_audit(audit_log_path, *, details, **kwargs):
        snapshots.append((str(details["phase"]), target.read_text(encoding="utf-8")))

    monkeypatch.setattr(file_replacements, "append_audit_event", record_audit)

    response = _post_replace(client, token, "profile.cfg", b"after")

    assert response.status_code == 303
    assert snapshots == [("intent", "before"), ("outcome", "after")]


def test_replace_pending_restart_fallback_after_successful_publish(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import pending_work

    config = _config_root(tmp_path)
    target = config / "profile.cfg"
    target.write_text("before", encoding="utf-8")
    client = _login_owner(tmp_path)
    token = _replace_token(client)

    def fail_primary_pending(*args, **kwargs):
        raise RuntimeError("web.db locked token=raw-pending-secret")

    monkeypatch.setattr(pending_work, "mark_restart_pending", fail_primary_pending)

    response = _post_replace(client, token, "profile.cfg", b"after")

    assert response.status_code == 500
    assert response.text == (
        "File replacement was published but restart tracking used fallback storage."
    )
    assert "raw-pending-secret" not in response.text
    assert target.read_text(encoding="utf-8") == "after"
    fallback = pending_work.get_fallback_pending_work(
        tmp_path / "web" / "web.db",
        kind=pending_work.KIND_CONFIG,
    )
    assert fallback is not None
    assert fallback.source_action == "file.replace"
    sidecar_text = pending_work.fallback_pending_work_path(
        tmp_path / "web" / "web.db"
    ).read_text(encoding="utf-8")
    assert "raw-pending-secret" not in sidecar_text


def test_replace_outcome_audit_failure_reports_published_without_raw_secret(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web.services import file_replacements
    from armactl.web.services.audit import AuditLogError

    config = _config_root(tmp_path)
    target = config / "profile.cfg"
    target.write_text("before", encoding="utf-8")
    client = _login_owner(tmp_path)
    token = _replace_token(client)

    def fail_outcome(audit_log_path, *, details, **kwargs):
        if details["phase"] == "outcome":
            raise AuditLogError("disk full token=raw-audit-secret")

    monkeypatch.setattr(file_replacements, "append_audit_event", fail_outcome)

    response = _post_replace(
        client,
        token,
        "profile.cfg",
        b"after password=raw-content-secret",
    )

    assert response.status_code == 500
    assert response.text == "File replacement was published but audit logging failed."
    assert target.read_text(encoding="utf-8") == "after password=raw-content-secret"
    assert "raw-audit-secret" not in response.text
    assert "raw-content-secret" not in response.text


def test_replace_auth_csrf_and_permission_guards(
    tmp_path: Path,
    set_web_owner_permissions,
):
    from armactl.web.app import create_app
    from armactl.web.auth.permissions import FILES_READ

    config = _config_root(tmp_path)
    target = config / "profile.cfg"
    target.write_text("before", encoding="utf-8")

    unauthenticated = _client(create_app(data_root=tmp_path))
    unauth_response = _post_replace(unauthenticated, "missing", "profile.cfg", b"after")

    client = _login_owner(tmp_path)
    csrf_response = _post_replace(client, "bad-token", "profile.cfg", b"after")

    password = "read only files password"
    other_root = tmp_path / "readonly"
    other_config = _config_root(other_root)
    other_config.joinpath("profile.cfg").write_text("before", encoding="utf-8")
    setup_owner_user(other_root, "owner", password)
    set_web_owner_permissions({FILES_READ})
    read_only_client = _client(create_app(data_root=other_root))
    _login(read_only_client, "owner", password)
    read_only_token = _files_csrf_token(read_only_client, "/files/config")
    permission_response = _post_replace(
        read_only_client,
        read_only_token,
        "profile.cfg",
        b"after",
    )

    assert unauth_response.status_code == 303
    assert unauth_response.headers["location"] == "/login"
    assert csrf_response.status_code == 403
    assert csrf_response.text == "Invalid CSRF token."
    assert permission_response.status_code == 403
    assert permission_response.text == "Permission denied."
    assert target.read_text(encoding="utf-8") == "before"
    assert other_config.joinpath("profile.cfg").read_text(encoding="utf-8") == "before"
