"""Request-scoped localization and appearance preferences for armactl web."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Request
from starlette.responses import Response

from armactl import i18n as armactl_i18n
from armactl.web.runtime import WebRuntimeConfig

LANGUAGE_COOKIE_NAME = "armactl_web_lang"
THEME_COOKIE_NAME = "armactl_web_theme"
DEFAULT_LANGUAGE = "en"
DEFAULT_THEME = "light"
SUPPORTED_THEMES = frozenset({"light", "dark"})
PREFERENCE_COOKIE_MAX_AGE_SECONDS = 365 * 24 * 60 * 60


@dataclass(frozen=True)
class WebPreferences:
    """Resolved web request preferences."""

    language: str
    theme: str


def supported_language_codes() -> tuple[str, ...]:
    """Return supported locale codes without changing the active global language."""
    codes = tuple(getattr(armactl_i18n, "_locale_order", ()))
    if codes:
        return codes
    locales = getattr(armactl_i18n, "_available_locales", {})
    if DEFAULT_LANGUAGE in locales:
        return (DEFAULT_LANGUAGE, *sorted(code for code in locales if code != DEFAULT_LANGUAGE))
    return tuple(sorted(locales)) or (DEFAULT_LANGUAGE,)


def language_name(language: str) -> str:
    """Return the display name for a supported language."""
    locales = getattr(armactl_i18n, "_available_locales", {})
    meta = locales.get(language, {}).get("__meta__", {})
    return str(meta.get("language") or language)


def normalize_language(value: object | None) -> str | None:
    """Return a supported language code or None for unsafe/unknown values."""
    if not isinstance(value, str):
        return None
    raw = value.strip().lower().replace("_", "-")
    if not raw:
        return None
    supported = set(supported_language_codes())
    if raw in supported:
        return raw
    base = raw.split("-", 1)[0]
    if base in supported:
        return base
    return None


def normalize_theme(value: object | None) -> str | None:
    """Return a supported theme value or None for unsafe/unknown values."""
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized in SUPPORTED_THEMES:
        return normalized
    return None


def _fallback_language() -> str:
    supported = supported_language_codes()
    if DEFAULT_LANGUAGE in supported:
        return DEFAULT_LANGUAGE
    return supported[0]


def _accept_language_candidates(header: str) -> list[str]:
    candidates: list[tuple[float, int, str]] = []
    for index, item in enumerate(header.split(",")):
        item = item.strip()
        if not item:
            continue
        language, _, params = item.partition(";")
        quality = 1.0
        for param in params.split(";"):
            key, _, value = param.strip().partition("=")
            if key.lower() != "q":
                continue
            try:
                quality = float(value)
            except ValueError:
                quality = 0.0
        if quality <= 0:
            continue
        candidates.append((quality, index, language))
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return [language for _, _, language in candidates]


def language_from_accept_language(header: str | None) -> str | None:
    """Resolve a supported language from an Accept-Language header."""
    if not header:
        return None
    for candidate in _accept_language_candidates(header):
        normalized = normalize_language(candidate)
        if normalized is not None:
            return normalized
    return None


def resolve_language(request: Request) -> str:
    """Resolve request language from web-owned preference, then Accept-Language."""
    cookie_language = normalize_language(request.cookies.get(LANGUAGE_COOKIE_NAME))
    if cookie_language is not None:
        return cookie_language

    accepted_language = language_from_accept_language(request.headers.get("accept-language"))
    if accepted_language is not None:
        return accepted_language

    return _fallback_language()


def resolve_theme(request: Request) -> str:
    """Resolve request theme from a web-owned cookie, falling back to light."""
    return normalize_theme(request.cookies.get(THEME_COOKIE_NAME)) or DEFAULT_THEME


def next_language(language: str) -> str:
    """Return the next language code for a simple toggle control."""
    supported = supported_language_codes()
    if language not in supported:
        return supported[0]
    return supported[(supported.index(language) + 1) % len(supported)]


def next_theme(theme: str) -> str:
    """Return the opposite supported theme."""
    return "dark" if theme != "dark" else "light"


def resolve_preferences(request: Request) -> WebPreferences:
    """Resolve all web UI preferences for a request."""
    return WebPreferences(language=resolve_language(request), theme=resolve_theme(request))


def _cookie_secure(config: WebRuntimeConfig) -> bool:
    return bool(config.https_required)


def set_language_cookie(response: Response, language: str, config: WebRuntimeConfig) -> None:
    """Persist a safe web-owned language cookie."""
    normalized = normalize_language(language) or _fallback_language()
    response.set_cookie(
        key=LANGUAGE_COOKIE_NAME,
        value=normalized,
        max_age=PREFERENCE_COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        secure=_cookie_secure(config),
        samesite="lax",
        path="/",
    )


def set_theme_cookie(response: Response, theme: str, config: WebRuntimeConfig) -> None:
    """Persist a safe web-owned theme cookie."""
    normalized = normalize_theme(theme) or DEFAULT_THEME
    response.set_cookie(
        key=THEME_COOKIE_NAME,
        value=normalized,
        max_age=PREFERENCE_COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        secure=_cookie_secure(config),
        samesite="lax",
        path="/",
    )


def translation_helpers(language: str) -> dict[str, Any]:
    """Return Jinja-callable translation helpers for one language."""
    return {
        "t": lambda text: armactl_i18n.translate_for_lang(language, text),
        "tr": lambda text, **kwargs: armactl_i18n.tr_for_lang(language, text, **kwargs),
    }


def web_template_context(request: Request) -> dict[str, Any]:
    """Inject request-scoped localization and theme helpers into templates."""
    preferences = resolve_preferences(request)
    language = preferences.language
    next_lang = next_language(language)
    theme = preferences.theme
    next_theme_value = next_theme(theme)
    context: dict[str, Any] = {
        "web_language": language,
        "web_language_name": language_name(language),
        "web_next_language": next_lang,
        "web_next_language_name": language_name(next_lang),
        "web_theme": theme,
        "web_next_theme": next_theme_value,
    }
    context.update(translation_helpers(language))
    return context
