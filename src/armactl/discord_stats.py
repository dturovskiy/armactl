"""Read-only Discord statistics publisher for community channels."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from armactl import paths
from armactl.public_stats import load_public_stats, render_discord_stats_message

DEFAULT_INTERVAL_SECONDS = 60
MIN_INTERVAL_SECONDS = 15
MAX_INTERVAL_SECONDS = 3600
CONFIG_FILE_NAME = "discord-stats.env"
DISCORD_USER_AGENT = "armactl-discord-stats/1.0"
DISCORD_TIMEOUT_SECONDS = 15.0
TRUE_VALUES = {"1", "true", "yes", "on"}


class DiscordStatsConfigError(Exception):
    """Raised when Discord statistics config is invalid or cannot be saved."""


class DiscordStatsPublishError(Exception):
    """Raised when a Discord statistics publish/update operation fails."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class DiscordStatsConfig:
    """Instance-scoped Discord statistics publisher config."""

    instance: str = paths.DEFAULT_INSTANCE_NAME
    enabled: bool = False
    webhook_url: str = ""
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS
    message_id: str = ""
    env_path: Path | None = None

    def webhook_configured(self) -> bool:
        return bool(self.webhook_url.strip())

    def masked_webhook_url(self) -> str:
        value = self.webhook_url.strip()
        if not value:
            return "not configured"
        tail = value[-6:] if len(value) > 6 else "******"
        return f"configured (...{tail})"


@dataclass(frozen=True)
class DiscordStatsPublishResult:
    """Result of sending or updating a Discord statistics message."""

    success: bool
    message: str
    message_id: str = ""
    created: bool = False
    updated: bool = False


def discord_stats_config_file(instance: str = paths.DEFAULT_INSTANCE_NAME) -> Path:
    """Return the instance-scoped Discord statistics config path."""
    return paths.bot_dir(instance) / CONFIG_FILE_NAME


def discord_stats_defaults(instance: str = paths.DEFAULT_INSTANCE_NAME) -> DiscordStatsConfig:
    """Return default disabled Discord statistics config."""
    return DiscordStatsConfig(
        instance=paths.validate_instance_name(instance),
        env_path=discord_stats_config_file(instance),
    )


def _read_env_mapping(env_path: Path) -> dict[str, str]:
    if not env_path.is_file():
        return {}
    data: dict[str, str] = {}
    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            data[key.strip()] = value.strip()
    except OSError as error:
        raise DiscordStatsConfigError(
            f"Failed to read Discord statistics config: {error}"
        ) from error
    return data


def _parse_interval(value: str, default: int = DEFAULT_INTERVAL_SECONDS) -> int:
    if not value.strip():
        return default
    try:
        interval = int(value.strip())
    except ValueError as error:
        raise DiscordStatsConfigError(
            "Discord statistics interval must be an integer number of seconds."
        ) from error
    if interval < MIN_INTERVAL_SECONDS or interval > MAX_INTERVAL_SECONDS:
        raise DiscordStatsConfigError(
            f"Discord statistics interval must be between {MIN_INTERVAL_SECONDS} "
            f"and {MAX_INTERVAL_SECONDS} seconds."
        )
    return interval


def _validate_webhook_url(value: str) -> str:
    url = value.strip()
    if not url:
        return ""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https":
        raise DiscordStatsConfigError("Discord webhook URL must use https.")
    if parsed.netloc not in {"discord.com", "discordapp.com"}:
        raise DiscordStatsConfigError("Discord webhook URL must point to discord.com.")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 4 or parts[0] != "api" or parts[1] != "webhooks":
        raise DiscordStatsConfigError("Discord webhook URL must be an /api/webhooks URL.")
    return url


def validate_discord_stats_config(config: DiscordStatsConfig) -> list[str]:
    """Return user-facing validation errors."""
    errors: list[str] = []
    try:
        paths.validate_instance_name(config.instance)
    except paths.InvalidInstanceNameError as error:
        errors.append(str(error))
    try:
        _parse_interval(str(config.interval_seconds))
    except DiscordStatsConfigError as error:
        errors.append(str(error))
    try:
        _validate_webhook_url(config.webhook_url)
    except DiscordStatsConfigError as error:
        errors.append(str(error))
    if config.enabled and not config.webhook_url.strip():
        errors.append("Discord statistics webhook URL is required when enabled.")
    return errors


def load_discord_stats_config(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    env_path: Path | None = None,
) -> DiscordStatsConfig:
    """Load instance-scoped Discord statistics config."""
    instance = paths.validate_instance_name(instance)
    resolved_env_path = env_path or discord_stats_config_file(instance)
    defaults = discord_stats_defaults(instance)
    data = _read_env_mapping(resolved_env_path)
    enabled_raw = data.get("ARMACTL_DISCORD_STATS_ENABLED", "false").strip().lower()
    return DiscordStatsConfig(
        instance=data.get("ARMACTL_INSTANCE", instance).strip() or defaults.instance,
        enabled=enabled_raw in TRUE_VALUES,
        webhook_url=data.get("ARMACTL_DISCORD_STATS_WEBHOOK_URL", "").strip(),
        interval_seconds=_parse_interval(
            data.get("ARMACTL_DISCORD_STATS_INTERVAL_SECONDS", ""),
            defaults.interval_seconds,
        ),
        message_id=data.get("ARMACTL_DISCORD_STATS_MESSAGE_ID", "").strip(),
        env_path=resolved_env_path,
    )


def render_discord_stats_config(config: DiscordStatsConfig) -> str:
    """Render normalized Discord statistics `.env` payload."""
    return "\n".join(
        [
            "# armactl Discord read-only statistics configuration",
            "# The webhook URL is a secret. Keep this file private.",
            f"ARMACTL_DISCORD_STATS_ENABLED={'true' if config.enabled else 'false'}",
            f"ARMACTL_DISCORD_STATS_WEBHOOK_URL={config.webhook_url.strip()}",
            f"ARMACTL_DISCORD_STATS_INTERVAL_SECONDS={config.interval_seconds}",
            f"ARMACTL_DISCORD_STATS_MESSAGE_ID={config.message_id.strip()}",
            f"ARMACTL_INSTANCE={config.instance.strip()}",
        ]
    ) + "\n"


def save_discord_stats_config(config: DiscordStatsConfig) -> Path:
    """Validate and persist the instance-scoped Discord statistics config."""
    errors = validate_discord_stats_config(config)
    if errors:
        raise DiscordStatsConfigError(errors[0])

    env_path = config.env_path or discord_stats_config_file(config.instance)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = env_path.with_suffix(".env.tmp")
    try:
        tmp_path.unlink(missing_ok=True)
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(render_discord_stats_config(config))
        os.replace(tmp_path, env_path)
        env_path.chmod(0o600)
    except OSError as error:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise DiscordStatsConfigError(
            f"Failed to save Discord statistics config: {error}"
        ) from error
    return env_path


def ensure_discord_stats_config(instance: str = paths.DEFAULT_INSTANCE_NAME) -> Path:
    """Create default Discord statistics config if missing."""
    env_path = discord_stats_config_file(instance)
    if env_path.exists():
        return env_path
    save_discord_stats_config(discord_stats_defaults(instance))
    return env_path


def _with_wait(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = [(key, value) for key, value in query if key != "wait"]
    query.append(("wait", "true"))
    return urllib.parse.urlunsplit(
        parsed._replace(query=urllib.parse.urlencode(query))
    )


def _message_url(webhook_url: str, message_id: str) -> str:
    parsed = urllib.parse.urlsplit(webhook_url)
    path = parsed.path.rstrip("/") + f"/messages/{urllib.parse.quote(message_id)}"
    return urllib.parse.urlunsplit(parsed._replace(path=path, query=""))


def _send_json_request(
    method: str,
    url: str,
    payload: dict[str, Any],
    *,
    timeout: float = DISCORD_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Content-Type": "application/json",
            "User-Agent": DISCORD_USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8").strip()
    except urllib.error.HTTPError as error:
        raise DiscordStatsPublishError(
            f"Discord webhook request failed with HTTP {error.code}.",
            status_code=error.code,
        ) from error
    except urllib.error.URLError as error:
        raise DiscordStatsPublishError("Discord webhook request failed.") from error
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise DiscordStatsPublishError("Discord webhook returned invalid JSON.") from error
    return parsed if isinstance(parsed, dict) else {}


def _payload(content: str) -> dict[str, Any]:
    return {
        "content": content,
        "allowed_mentions": {"parse": []},
    }


def publish_discord_stats(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    config: DiscordStatsConfig | None = None,
) -> DiscordStatsPublishResult:
    """Publish or update one read-only Discord statistics message."""
    resolved_config = config or load_discord_stats_config(instance)
    if not resolved_config.enabled:
        raise DiscordStatsPublishError("Discord statistics publisher is disabled.")
    webhook_url = _validate_webhook_url(resolved_config.webhook_url)
    if not webhook_url:
        raise DiscordStatsPublishError("Discord statistics webhook URL is not configured.")

    content = render_discord_stats_message(load_public_stats(resolved_config.instance))
    payload = _payload(content)

    if resolved_config.message_id:
        try:
            response = _send_json_request(
                "PATCH",
                _message_url(webhook_url, resolved_config.message_id),
                payload,
            )
            message_id = str(response.get("id") or resolved_config.message_id)
            return DiscordStatsPublishResult(
                True,
                "Discord statistics message updated.",
                message_id=message_id,
                updated=True,
            )
        except DiscordStatsPublishError as error:
            if error.status_code != 404:
                raise
            # Fall through and create a fresh message if the stored message was deleted.

    response = _send_json_request("POST", _with_wait(webhook_url), payload)
    message_id = str(response.get("id") or "").strip()
    if not message_id:
        raise DiscordStatsPublishError("Discord webhook did not return a message ID.")
    save_discord_stats_config(
        DiscordStatsConfig(
            instance=resolved_config.instance,
            enabled=resolved_config.enabled,
            webhook_url=resolved_config.webhook_url,
            interval_seconds=resolved_config.interval_seconds,
            message_id=message_id,
            env_path=resolved_config.env_path,
        )
    )
    return DiscordStatsPublishResult(
        True,
        "Discord statistics message created.",
        message_id=message_id,
        created=True,
    )


def run_discord_stats_publisher(
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    *,
    once: bool = False,
) -> None:
    """Run the read-only Discord statistics publisher loop."""
    while True:
        config = load_discord_stats_config(instance)
        result = publish_discord_stats(instance, config=config)
        print(result.message, flush=True)
        if once:
            return
        time.sleep(config.interval_seconds)
