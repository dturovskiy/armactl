"""Internationalization module for armactl TUI."""

from __future__ import annotations

import json
from pathlib import Path

from armactl import paths as P

# A centralized file to store global settings like language
SETTINGS_FILE = P.DEFAULT_DATA_ROOT / "user_settings.json"

_current_lang = "en"
_available_locales = {}
_locale_order = []

LOCALES_DIR = Path(__file__).parent / "locales"

def init_locales() -> None:
    """Scan the locales directory and load all available json files."""
    global _available_locales, _locale_order, _current_lang
    _available_locales.clear()
    _locale_order.clear()
    
    if LOCALES_DIR.exists():
        for p in LOCALES_DIR.glob("*.json"):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    meta = data.get("__meta__", {})
                    code = meta.get("code", p.stem)
                    _available_locales[code] = data
            except Exception:
                pass
                
    # fallback if completely empty
    if not _available_locales:
        _available_locales["en"] = {"__meta__": {"code": "en", "language": "English"}, "translations": {}}
        
    # Standardize order so toggle iterates predictably
    _locale_order = sorted(_available_locales.keys())
    
    # Ensure en is first if it exists
    if "en" in _locale_order:
        _locale_order.remove("en")
        _locale_order.insert(0, "en")


def load_lang() -> None:
    global _current_lang
    init_locales()
    try:
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                code = data.get("lang", "en")
                if code in _available_locales:
                    _current_lang = code
    except Exception:
        pass

def save_lang(lang: str) -> None:
    global _current_lang
    if lang in _available_locales:
        _current_lang = lang
        try:
            SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {}
            if SETTINGS_FILE.exists():
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            data["lang"] = lang
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception:
            pass

def toggle_lang() -> str:
    if not _locale_order:
        return _current_lang
    try:
        idx = _locale_order.index(_current_lang)
        next_idx = (idx + 1) % len(_locale_order)
    except ValueError:
        next_idx = 0
    save_lang(_locale_order[next_idx])
    return _locale_order[next_idx]

def get_current_lang_name() -> str:
    if _current_lang in _available_locales:
        return _available_locales[_current_lang].get("__meta__", {}).get("language", _current_lang)
    return _current_lang

def _(text: str) -> str:
    """Translate text to the currently selected language."""
    if _current_lang in _available_locales:
        translations = _available_locales[_current_lang].get("translations", {})
        return translations.get(text, text)
    return text

load_lang()
