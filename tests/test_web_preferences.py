"""Route and asset tests for web language/theme preferences."""

from __future__ import annotations

from pathlib import Path

from web_route_helpers import _client, _login

from armactl.web.auth.setup import setup_owner_user
from armactl.web.i18n import LANGUAGE_COOKIE_NAME, THEME_COOKIE_NAME


def test_login_template_has_language_and_theme_controls(tmp_path: Path):
    from armactl.web.app import create_app

    setup_owner_user(tmp_path, "owner", "owner password")
    client = _client(create_app(data_root=tmp_path))

    response = client.get("/login")

    assert response.status_code == 200
    assert 'action="/preferences/language"' in response.text
    assert 'action="/preferences/theme"' in response.text
    assert 'data-preference-form="language"' in response.text
    assert 'data-preference-form="theme"' in response.text
    assert 'class="language-menu"' in response.text
    assert 'class="icon-control language-summary"' in response.text
    assert 'class="language-option language-option-active"' in response.text
    assert 'class="icon-control theme-toggle-button"' in response.text
    assert "data-theme-label" in response.text
    assert 'class="control-svg language-icon"' in response.text
    assert "data-theme-icon-dark" in response.text
    assert "data-theme-icon-light" in response.text
    assert "control-image" not in response.text
    assert "control-chevron" not in response.text
    assert "◎" not in response.text
    assert "☾" not in response.text
    assert "☀" not in response.text
    assert "/static/js/preferences.js" in response.text
    assert "/static/img/armactl_dashboard.png?v=" in response.text
    assert "/static/js/csrf.js?v=" in response.text
    assert "/static/css/app.css?v=" in response.text
    assert "/static/js/preferences.js?v=" in response.text
    assert 'data-theme="light"' in response.text
    assert "English" in response.text
    assert "Українська" in response.text
    assert 'aria-label="Theme: dark"' in response.text


def test_preferences_js_asset_is_served(tmp_path: Path):
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    response = client.get("/static/js/preferences.js")

    assert response.status_code == 200
    assert "text/javascript" in response.headers["content-type"]
    assert "document.documentElement.dataset.theme" in response.text
    assert "pageshow" in response.text
    assert "event.persisted" in response.text
    assert "window.location.reload()" in response.text
    assert "armactl_web_session" not in response.text
    assert "csrf_token" not in response.text


def test_csrf_refresh_js_asset_is_served(tmp_path: Path):
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    response = client.get("/static/js/csrf.js")

    assert response.status_code == 200
    assert "text/javascript" in response.headers["content-type"]
    assert "/auth/csrf-token" in response.text
    assert 'input[name="csrf_token"]' in response.text
    assert "form.dataset.preferenceForm" in response.text
    assert 'pathname === "/login"' in response.text
    assert "HTMLFormElement.prototype.submit.call(form)" in response.text
    assert "data-csrf-submit-clone" in response.text
    assert "armactl_web_session" not in response.text
    assert "armactl_web_csrf" not in response.text


def test_preferences_js_persists_requested_theme_before_flipping_next_value():
    script = Path("src/armactl/web/static/js/preferences.js").read_text(encoding="utf-8")

    persist_index = script.index("const persist = postPreference(form);")
    flip_index = script.index("updateThemeButton(form, oppositeTheme(requestedTheme));")

    assert persist_index < flip_index


def test_login_renders_ukrainian_from_language_preference(tmp_path: Path):
    from armactl.web.app import create_app

    setup_owner_user(tmp_path, "owner", "owner password")
    client = _client(create_app(data_root=tmp_path))
    preference_response = client.post(
        "/preferences/language",
        data={"language": "uk", "next": "/login"},
        follow_redirects=False,
    )

    response = client.get("/login")

    assert preference_response.status_code == 303
    assert preference_response.cookies.get(LANGUAGE_COOKIE_NAME) == "uk"
    assert '<html lang="uk" data-theme="light">' in response.text
    assert "Увійти" in response.text
    assert "Ім&#39;я користувача" in response.text
    assert 'class="language-menu"' in response.text
    assert "Українська" in response.text
    assert 'data-theme-label-prefix="Тема"' in response.text


def test_invalid_language_and_theme_preferences_are_normalized(tmp_path: Path):
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    language_response = client.post(
        "/preferences/language",
        data={"language": "not-a-language", "next": "/login"},
        follow_redirects=False,
    )
    theme_response = client.post(
        "/preferences/theme",
        data={"theme": "solarized", "next": "/login"},
        follow_redirects=False,
    )

    assert language_response.status_code == 303
    assert language_response.cookies.get(LANGUAGE_COOKIE_NAME) == "en"
    assert theme_response.status_code == 303
    assert theme_response.cookies.get(THEME_COOKIE_NAME) == "light"


def test_theme_preference_async_sets_cookie_without_redirect(tmp_path: Path):
    from armactl.web.app import create_app

    client = _client(create_app(data_root=tmp_path))

    response = client.post(
        "/preferences/theme",
        data={"theme": "dark", "next": "/dashboard"},
        headers={"X-Requested-With": "fetch", "Accept": "application/json"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert response.json() == {"theme": "dark"}
    assert response.cookies.get(THEME_COOKIE_NAME) == "dark"
    assert "location" not in response.headers


def test_accept_language_localizes_login_without_cookie(tmp_path: Path):
    from armactl.web.app import create_app

    setup_owner_user(tmp_path, "owner", "owner password")
    client = _client(create_app(data_root=tmp_path))

    response = client.get("/login", headers={"accept-language": "uk-UA, en;q=0.2"})

    assert response.status_code == 200
    assert '<html lang="uk" data-theme="light">' in response.text
    assert "Увійти" in response.text


def test_authenticated_theme_preference_requires_valid_csrf(
    tmp_path: Path,
):
    from armactl.web.app import create_app

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.post(
        "/preferences/theme",
        data={"theme": "dark", "csrf_token": "wrong-token", "next": "/dashboard"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.text == "Invalid CSRF token."
    assert response.cookies.get(THEME_COOKIE_NAME) is None


def test_authenticated_csrf_refresh_endpoint_returns_form_token(tmp_path: Path):
    from armactl.web.app import create_app

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    client = _client(create_app(data_root=tmp_path))

    anonymous_response = client.get("/auth/csrf-token")

    assert anonymous_response.status_code == 401
    assert anonymous_response.json() == {"error": "authentication_required"}
    assert anonymous_response.headers["cache-control"] == "no-store, max-age=0"

    _login(client, "owner", password)
    token_response = client.get("/auth/csrf-token")

    assert token_response.status_code == 200
    assert token_response.headers["cache-control"] == "no-store, max-age=0"
    token = token_response.json()["csrf_token"]
    assert isinstance(token, str)
    assert token

    preference_response = client.post(
        "/preferences/theme",
        data={"theme": "dark", "csrf_token": token, "next": "/dashboard"},
        follow_redirects=False,
    )

    assert preference_response.status_code == 303
    assert preference_response.cookies.get(THEME_COOKIE_NAME) == "dark"


def test_authenticated_theme_preference_async_accepts_stale_csrf_for_cookie_only_update(
    tmp_path: Path,
):
    from armactl.web.app import create_app

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.post(
        "/preferences/theme",
        data={"theme": "dark", "csrf_token": "stale-token", "next": "/dashboard"},
        headers={"X-Requested-With": "fetch", "Accept": "application/json"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert response.json() == {"theme": "dark"}
    assert response.cookies.get(THEME_COOKIE_NAME) == "dark"


def test_authenticated_language_preference_requires_valid_csrf(tmp_path: Path):
    from armactl.web.app import create_app

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.post(
        "/preferences/language",
        data={"language": "uk", "csrf_token": "wrong-token", "next": "/dashboard"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.text == "Invalid CSRF token."
    assert response.cookies.get(LANGUAGE_COOKIE_NAME) is None


def test_authenticated_language_preference_async_accepts_stale_csrf_for_cookie_only_update(
    tmp_path: Path,
):
    from armactl.web.app import create_app

    password = "owner dashboard password"
    setup_owner_user(tmp_path, "owner", password)
    client = _client(create_app(data_root=tmp_path))
    _login(client, "owner", password)

    response = client.post(
        "/preferences/language",
        data={"language": "uk", "csrf_token": "stale-token", "next": "/dashboard"},
        headers={"X-Requested-With": "fetch", "Accept": "application/json"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert response.json() == {"language": "uk"}
    assert response.cookies.get(LANGUAGE_COOKIE_NAME) == "uk"
