"""Internationalization module for armactl TUI."""

from __future__ import annotations

import json
from pathlib import Path

from armactl import paths as P

# A centralized file to store global settings like language
SETTINGS_FILE = P.base_dir / "user_settings.json"

_current_lang = "en"

UK_STRINGS = {
    # Main Menu
    "Quit": "Вийти",
    "Exit": "Вийти",
    "Install New Server": "Встановити новий сервер",
    "Repair Installation": "Відновлення (Repair)",
    "Manage Existing Server >>": "Керувати встановленим сервером >>",
    "Change Language (UA)": "Змінити мову (EN)",
    
    # Manage Screen
    "Restart": "Рестарт",
    "Start": "Запустити",
    "Stop": "Зупинити",
    "Edit Configuration": "Редагувати конфігурацію",
    "Maintenance / Cleanup": "Очищення (Maintenance)",
    "View Live Logs": "Живі логи (Journalctl)",
    "Status Details": "Повний статус (JSON)",
    "Check Ports": "Перевірити порти",
    "Back to Main Menu": "У головне меню",
    "Refresh Status": "Оновити статус",
    "Back": "Назад",
    "Loading status...": "Завантаження статусу...",
    
    # Config Editor
    "Server Name:": "Назва сервера:",
    "Scenario ID:": "ID сценарію (Scenario):",
    "Max Players:": "Макс. гравців:",
    "Game Port (UDP):": "Порт гри (UDP):",
    "A2S Port (UDP):": "A2S Порт (UDP):",
    "RCON Port (TCP/UDP):": "RCON Порт (TCP/UDP):",
    "Game Password (for players):": "Пароль для гравців:",
    "Leave empty for open public server": "Залиште пустим для публічного сервера",
    "Admin Password:": "Пароль адміна:",
    "RCON Password:": "Пароль RCON:",
    "Save Config": "Зберегти",
    "Save & Restart": "Зберегти і Перезапустити",
    "Cancel": "Скасувати",
    "Back without saving": "Назад без збереження",
    
    # Cleanup
    "Maintenance & Cleanup: ": "Очищення та обслуговування: ",
    "Clean Junk Files": "Очистити сміття",
    "Clean Now": "Очистити зараз",
    
    # Confirm
    "Yes": "Так",
    "No": "Ні",
}

def load_lang() -> None:
    global _current_lang
    try:
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                _current_lang = data.get("lang", "en")
    except Exception:
        pass

def save_lang(lang: str) -> None:
    global _current_lang
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
    new_lang = "uk" if _current_lang == "en" else "en"
    save_lang(new_lang)
    return new_lang

def _(text: str) -> str:
    """Translate text to the currently selected language."""
    if _current_lang == "uk":
        return UK_STRINGS.get(text, text)
    return text

load_lang()
