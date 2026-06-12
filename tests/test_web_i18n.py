"""Tests for request-scoped web localization and theme preferences."""

from __future__ import annotations

from types import SimpleNamespace

from armactl.web import i18n as web_i18n


def _request(*, cookies=None, headers=None):
    return SimpleNamespace(cookies=cookies or {}, headers=headers or {})


def test_explicit_supported_language_resolves_from_cookie():
    request = _request(cookies={web_i18n.LANGUAGE_COOKIE_NAME: "uk"})

    assert web_i18n.resolve_language(request) == "uk"


def test_unsupported_language_falls_back_safely():
    request = _request(cookies={web_i18n.LANGUAGE_COOKIE_NAME: "zz"})

    assert web_i18n.resolve_language(request) == "en"
    assert web_i18n.normalize_language("zz") is None


def test_accept_language_selects_supported_language():
    request = _request(headers={"accept-language": "fr-CA, uk-UA;q=0.9, en;q=0.2"})

    assert web_i18n.resolve_language(request) == "uk"


def test_web_i18n_does_not_mutate_global_tui_language(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("web i18n must not mutate global TUI language")

    monkeypatch.setattr(web_i18n.armactl_i18n, "toggle_lang", fail)
    monkeypatch.setattr(web_i18n.armactl_i18n, "save_lang", fail)

    request = _request(headers={"accept-language": "uk"})
    context = web_i18n.web_template_context(request)

    assert context["web_language"] == "uk"
    assert context["t"]("Login") == "Вхід"


def test_theme_values_are_limited_to_light_or_dark():
    assert web_i18n.normalize_theme("dark") == "dark"
    assert web_i18n.normalize_theme("light") == "light"
    assert web_i18n.normalize_theme("solarized") is None
    assert web_i18n.resolve_theme(_request(cookies={web_i18n.THEME_COOKIE_NAME: "bad"})) == "light"
