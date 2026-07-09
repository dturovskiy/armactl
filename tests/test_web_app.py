"""App-level wiring tests for the FastAPI web app."""

from __future__ import annotations

import os
from pathlib import Path

from web_route_helpers import _client


def test_create_app_import_does_not_import_tui_or_textual(
    assert_import_does_not_import_modules,
):
    forbidden = ("armactl.tui", "textual")
    assert_import_does_not_import_modules("armactl.web.app", forbidden)


def test_healthz_returns_ok():
    from armactl.web.app import create_app

    client = _client(create_app())

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_unauthenticated_dashboard_redirects_to_login(tmp_path: Path):
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    response = client.get("/dashboard", follow_redirects=False)
    root_response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert root_response.status_code == 303
    assert root_response.headers["location"] == "/login"


def test_template_and_static_paths_are_package_local():
    from armactl.web.app import STATIC_DIR, TEMPLATES_DIR, create_app

    assert (TEMPLATES_DIR / "dashboard.html").is_file()
    assert (TEMPLATES_DIR / "dashboard_error.html").is_file()
    assert (TEMPLATES_DIR / "login.html").is_file()
    assert (TEMPLATES_DIR / "service_result.html").is_file()
    assert (STATIC_DIR / "css" / "app.css").is_file()
    assert (STATIC_DIR / "img" / "armactl_dashboard.png").is_file()

    client = _client(create_app())
    response = client.get("/static/css/app.css")

    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]
    assert ".summary-band" in response.text
    assert ".status-pill" in response.text
    assert ".key-value-list" in response.text
    assert ".notice-panel" in response.text

    logo_response = client.get("/static/img/armactl_dashboard.png")

    assert logo_response.status_code == 200
    assert "image/png" in logo_response.headers["content-type"]
    assert ".notice-restart" in response.text
    assert ".auth-panel" in response.text
    assert "[data-theme=\"dark\"]" in response.text


def test_static_asset_version_tracks_all_css_and_js_assets(
    tmp_path: Path,
    monkeypatch,
):
    from armactl.web import app as web_app

    static_dir = tmp_path / "static"
    css_dir = static_dir / "css"
    js_dir = static_dir / "js"
    img_dir = static_dir / "img"
    css_dir.mkdir(parents=True)
    js_dir.mkdir(parents=True)
    img_dir.mkdir(parents=True)
    app_css = css_dir / "app.css"
    current_players_js = js_dir / "players_current_poll.js"
    nested_js = js_dir / "nested" / "tool.js"
    ignored_image = img_dir / "logo.png"
    nested_js.parent.mkdir()
    app_css.write_text("body {}", encoding="utf-8")
    current_players_js.write_text("window.currentPlayers = true;", encoding="utf-8")
    nested_js.write_text("window.nested = true;", encoding="utf-8")
    ignored_image.write_bytes(b"png")
    os.utime(app_css, ns=(1_000, 1_000))
    os.utime(current_players_js, ns=(2_000, 2_000))
    os.utime(nested_js, ns=(3_000, 3_000))
    os.utime(ignored_image, ns=(9_000, 9_000))
    monkeypatch.setattr(web_app, "STATIC_DIR", static_dir)

    assert web_app._static_asset_version() == "3000"
