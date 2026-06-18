"""App-level wiring tests for the FastAPI web app."""

from __future__ import annotations

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
