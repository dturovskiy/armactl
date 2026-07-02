"""Tests for request-scoped web localization and theme preferences."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

from armactl import i18n as armactl_i18n
from armactl.web import i18n as web_i18n

_TEMPLATE_TRANSLATION_RE = re.compile(r"""\b(?:t|tr)\(\s*(['"])(?P<key>.*?)\1""")


def _request(*, cookies=None, headers=None):
    return SimpleNamespace(cookies=cookies or {}, headers=headers or {})


def _locale_keys(language: str) -> set[str]:
    locale_path = armactl_i18n.LOCALES_DIR / f"{language}.json"
    data = json.loads(locale_path.read_text(encoding="utf-8"))
    return set(data.get("translations", {}))


def _template_translation_keys() -> set[str]:
    templates_root = Path(__file__).parents[1] / "src" / "armactl" / "web" / "templates"
    keys: set[str] = set()
    for template_path in templates_root.glob("*.html"):
        content = template_path.read_text(encoding="utf-8")
        keys.update(match.group("key") for match in _TEMPLATE_TRANSLATION_RE.finditer(content))
    return keys


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


def test_web_template_literal_translation_keys_exist_in_locales():
    template_keys = _template_translation_keys()
    missing_en = sorted(template_keys - _locale_keys("en"))
    missing_uk = sorted(template_keys - _locale_keys("uk"))

    assert template_keys
    assert missing_en == [], missing_en
    assert missing_uk == [], missing_uk


def test_player_session_dynamic_translation_keys_exist_in_locales():
    keys = {
        "All statuses",
        "Open",
        "Closed",
        "All end reasons",
        "Disconnect",
        "Server boundary",
        "Stale timeout",
        "Stale absence",
        "Scanner checkpoint",
        "Import window",
        "Unknown",
        "All sources",
        "Backend auth",
        "Network player update",
        "RCON roster",
        "Faction event",
        "Combat event",
        "ServerAdminTools event",
        "Service lifecycle",
        "Manual import",
        "High",
        "Medium",
        "Low",
    }

    assert sorted(keys - _locale_keys("en")) == []
    assert sorted(keys - _locale_keys("uk")) == []
