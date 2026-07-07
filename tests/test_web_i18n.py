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


def test_dashboard_live_refresh_translation_keys_exist_in_locales():
    keys = {
        "Live refresh active",
        "Live refresh paused/reconnecting.",
        "Dashboard data may be stale.",
    }

    assert sorted(keys - _locale_keys("en")) == []
    assert sorted(keys - _locale_keys("uk")) == []



def test_player_history_dynamic_translation_keys_exist_in_locales():
    keys = {
        "Authenticated",
        "Player update",
        "Faction join",
        "Disconnect",
        "Server lifecycle",
        "Kill",
        "Suicide",
        "Teamkill",
        "Other death",
        "Combat hint",
        "Player events",
        "Session evidence",
        "System",
        "Correlation only",
        "RPL identity",
        "Connection ID",
        "BE slot",
        "Backend auth",
        "Network player update",
        "Faction event",
        "RPL disconnect",
        "Network disconnect",
        "BattlEye disconnect",
        "Service lifecycle",
        "Combat event",
        "ServerAdminTools event",
        "High",
        "Medium",
        "Low",
        "Log timestamp without date",
        "Caller event time",
        "Caller observed time",
        "Unavailable",
        "Exact",
        "Derived",
        "Ambiguous",
        "Victim faction",
        "Instigator faction",
        "Damage",
        "Hit",
        "Distance",
        "Session player ID",
        "Victim session ID",
        "Instigator session ID",
        "Reference",
        "Confidence",
        "TK",
        "AI",
    }

    assert sorted(keys - _locale_keys("en")) == []
    assert sorted(keys - _locale_keys("uk")) == []


def test_player_session_dynamic_translation_keys_exist_in_locales():
    keys = {
        "All statuses",
        "Open",
        "Closed",
        "Stored open",
        "Stored closed",
        "Session not closed",
        "Session closed",
        "Unclosed sessions",
        "Closed sessions",
        "All end reasons",
        "Disconnect",
        "Server boundary",
        "Stale timeout",
        "Stale absence",
        "Scanner checkpoint",
        "Import window",
        "Unknown",
        "Disconnect evidence",
        "Lifecycle/server boundary",
        "Stale absence / stale timeout",
        "Scanner checkpoint boundary",
        "Stored log import window",
        "Unknown close reason",
        "All sources",
        "Reliable roster evidence",
        "Log evidence",
        "Other evidence",
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
        "High confidence",
        "Medium confidence",
        "Low confidence",
        "Unknown confidence",
        "Open sessions",
        "Closed sessions",
        "Inferred/stale closes",
        "Inferred/stale close evidence",
        "Unknown status",
        "Stored status",
        "Session state",
        "Evidence source",
        "Evidence",
        "First evidence",
        "Last evidence",
        "Close evidence",
        "Latest observed",
        "Latest evidence",
        "Active session jobs",
        "Session freshness jobs are queued or running.",
        "Manual session jobs",
        "Scan live sessions",
        "Observe current roster into sessions",
        "Sessionize logs",
        "Sessionize log events",
        "Process stored log evidence",
        "Session maintenance",
        "Close stale / clean old closed sessions",
        "Job",
        "Queued",
        "Running",
        "Session job",
        "Active",
        "Run session maintenance",
        "Live player session scan queued.",
        "Current roster will be scanned once in the background.",
        "Live player session scan already running.",
        "No duplicate job was created; the active scan is already queued or running.",
        "Live player session scan was not queued.",
        "Audit logging failed before the job could be queued.",
        "Player log sessionization queued.",
        "Stored player log events will be sessionized in the background.",
        "Player log sessionization already running.",
        "No duplicate job was created; the active sessionization job is already queued or running.",
        "Player log sessionization was not queued.",
        "Player session maintenance queued.",
        "Stale-close and retention maintenance will run in the background.",
        "Player session maintenance already running.",
        "No duplicate job was created; active session maintenance is already queued or running.",
        "Player session maintenance was not queued.",
        "Player session maintenance",
        "Queued player log sessionization",
        "Queued player session maintenance",
        "Queued live player session scan",
        "Sessionization complete",
        "Player session maintenance complete",
        "Live player session scan complete",
        "Player log sessionization completed.",
        "No stored player log events found.",
        "No new player session observations applied.",
        "Player session maintenance completed.",
        "No player session maintenance changes applied.",
        "Live player session scan completed.",
        "Live player session scan failed.",
        "No reliable live player session observations found.",
    }

    assert sorted(keys - _locale_keys("en")) == []
    assert sorted(keys - _locale_keys("uk")) == []
