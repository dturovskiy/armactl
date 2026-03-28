"""Helpers for reading and writing the optional Telegram bot `.env` config."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from armactl import paths
from armactl.i18n import _, tr

CHAT_ID_RE = re.compile(r"^-?\d+$")
TRUE_VALUES = {"1", "true", "yes", "on"}


class BotConfigError(Exception):
    """Raised when the Telegram bot configuration cannot be parsed or saved."""


@dataclass
class BotConfig:
    """Normalized Telegram bot configuration stored in `bot/.env`."""

    instance: str = paths.DEFAULT_INSTANCE_NAME
    enabled: bool = False
    token: str = ""
    admin_chat_ids: list[str] = field(default_factory=list)
    language: str = "uk"
    env_path: Path | None = None

    def admin_chat_ids_text(self) -> str:
        """Render the admin allowlist as a comma-separated string."""
        return ", ".join(self.admin_chat_ids)

    def masked_token(self) -> str:
        """Return a safe preview of the configured bot token."""
        value = self.token.strip()
        if not value:
            return _("Missing")
        if len(value) <= 8:
            return "*" * len(value)
        return f"{value[:4]}...{value[-4:]}"


def bot_config_defaults(instance: str = paths.DEFAULT_INSTANCE_NAME) -> BotConfig:
    """Return the default optional Telegram bot configuration."""
    return BotConfig(
        instance=instance,
        enabled=False,
        token="",
        admin_chat_ids=[],
        language="uk",
        env_path=paths.bot_env_file(instance),
    )


def _parse_env_mapping(text: str) -> dict[str, str]:
    """Parse a simple `.env` file into a dictionary."""
    parsed: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        parsed[key.strip()] = value.strip().strip("\"'")
    return parsed


def _read_env_mapping(env_path: Path) -> dict[str, str]:
    """Read a `.env` file into a dictionary, returning `{}` when it is absent."""
    if not env_path.exists():
        return {}

    try:
        return _parse_env_mapping(env_path.read_text(encoding="utf-8"))
    except OSError as e:
        raise BotConfigError(tr("Failed to read bot config file: {error}", error=e)) from e


def _parse_chat_ids(raw_value: str) -> list[str]:
    """Parse a comma/space/newline-separated list of Telegram chat IDs."""
    values = [value.strip() for value in re.split(r"[\s,;]+", raw_value) if value.strip()]
    invalid = [value for value in values if not CHAT_ID_RE.fullmatch(value)]
    if invalid:
        raise BotConfigError(_("Admin Chat IDs must contain only numeric Telegram chat IDs."))
    return values


def parse_admin_chat_ids(raw_value: str) -> list[str]:
    """Parse public input into a normalized list of Telegram chat IDs."""
    return _parse_chat_ids(raw_value)


def validate_bot_config(config: BotConfig) -> list[str]:
    """Return user-facing validation errors for a Telegram bot configuration."""
    errors: list[str] = []

    if not config.language.strip():
        errors.append(_("Bot language cannot be empty."))

    try:
        _parse_chat_ids(config.admin_chat_ids_text())
    except BotConfigError as e:
        errors.append(str(e))

    if config.enabled:
        if not config.token.strip():
            errors.append(_("Bot token is required when Telegram bot is enabled."))
        if not config.admin_chat_ids:
            errors.append(_("At least one admin Chat ID is required when Telegram bot is enabled."))

    return errors


def load_bot_config(instance: str = paths.DEFAULT_INSTANCE_NAME) -> BotConfig:
    """Load the instance-scoped Telegram bot `.env` file."""
    env_path = paths.bot_env_file(instance)
    data = _read_env_mapping(env_path)
    default_config = bot_config_defaults(instance)

    admin_chat_ids = _parse_chat_ids(data.get("ARMACTL_BOT_ADMIN_CHAT_IDS", ""))
    enabled_raw = data.get("ARMACTL_BOT_ENABLED", "false").strip().lower()

    return BotConfig(
        instance=data.get("ARMACTL_INSTANCE", instance) or default_config.instance,
        enabled=enabled_raw in TRUE_VALUES,
        token=data.get("ARMACTL_BOT_TOKEN", default_config.token).strip(),
        admin_chat_ids=admin_chat_ids,
        language=data.get("ARMACTL_BOT_LANGUAGE", default_config.language).strip()
        or default_config.language,
        env_path=env_path,
    )


def render_bot_config(config: BotConfig) -> str:
    """Render a normalized `.env` payload for the Telegram bot."""
    lines = [
        "# armactl Telegram bot configuration",
        "# This file is the source of truth for both TUI and manual editing.",
        f"ARMACTL_BOT_ENABLED={'true' if config.enabled else 'false'}",
        f"ARMACTL_BOT_TOKEN={config.token.strip()}",
        f"ARMACTL_BOT_ADMIN_CHAT_IDS={config.admin_chat_ids_text()}",
        f"ARMACTL_BOT_LANGUAGE={config.language.strip()}",
        f"ARMACTL_INSTANCE={config.instance.strip()}",
    ]
    return "\n".join(lines) + "\n"


def save_bot_config(config: BotConfig) -> Path:
    """Validate and persist the instance-scoped Telegram bot `.env` file."""
    errors = validate_bot_config(config)
    if errors:
        raise BotConfigError(errors[0])

    env_path = config.env_path or paths.bot_env_file(config.instance)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = env_path.with_suffix(".env.tmp")

    try:
        tmp_path.write_text(render_bot_config(config), encoding="utf-8")
        os.replace(tmp_path, env_path)
    except OSError as e:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise BotConfigError(tr("Failed to save bot config file: {error}", error=e)) from e

    return env_path


def ensure_bot_config(instance: str = paths.DEFAULT_INSTANCE_NAME) -> Path:
    """Create the default bot config file if it does not exist yet."""
    env_path = paths.bot_env_file(instance)
    if env_path.exists():
        return env_path

    save_bot_config(bot_config_defaults(instance))
    return env_path
