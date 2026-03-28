"""Compact config and mod summaries for status views."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from armactl.config_manager import ConfigError, load_config


@dataclass
class ConfigSummary:
    """Compact, non-secret config details for diagnostics views."""

    available: bool
    server_name: str | None = None
    scenario_id: str | None = None
    max_players: int | None = None
    bind_port: int | None = None
    a2s_port: int | None = None
    rcon_port: int | None = None
    visible: bool | None = None
    battleye: bool | None = None


@dataclass
class ModSummaryEntry:
    """Single mod preview item."""

    mod_id: str
    name: str = ""

    @property
    def label(self) -> str:
        """Human-friendly mod label without localization concerns."""
        if self.name and self.mod_id:
            return f"{self.name} ({self.mod_id})"
        return self.name or self.mod_id


@dataclass
class ModsSummary:
    """Compact mod diagnostics for status views."""

    available: bool
    count: int | None = None
    preview: list[ModSummaryEntry] = field(default_factory=list)
    remaining_count: int = 0


def summarize_config(config: dict[str, Any]) -> ConfigSummary:
    """Extract a concise status summary from a full config object."""
    game = config.get("game", {}) if isinstance(config.get("game"), dict) else {}
    a2s = config.get("a2s", {}) if isinstance(config.get("a2s"), dict) else {}
    rcon = config.get("rcon", {}) if isinstance(config.get("rcon"), dict) else {}
    properties = (
        game.get("gameProperties", {})
        if isinstance(game.get("gameProperties"), dict)
        else {}
    )

    return ConfigSummary(
        available=True,
        server_name=str(game.get("name", "")).strip() or None,
        scenario_id=str(game.get("scenarioId", "")).strip() or None,
        max_players=(
            game.get("maxPlayers")
            if isinstance(game.get("maxPlayers"), int)
            else None
        ),
        bind_port=(
            config.get("bindPort")
            if isinstance(config.get("bindPort"), int)
            else None
        ),
        a2s_port=(
            a2s.get("port")
            if isinstance(a2s.get("port"), int)
            else None
        ),
        rcon_port=(
            rcon.get("port")
            if isinstance(rcon.get("port"), int)
            else None
        ),
        visible=game.get("visible") if isinstance(game.get("visible"), bool) else None,
        battleye=(
            properties.get("battlEye")
            if isinstance(properties.get("battlEye"), bool)
            else None
        ),
    )


def summarize_mods(config: dict[str, Any], preview_limit: int = 3) -> ModsSummary:
    """Extract compact mod summary data from config."""
    game = config.get("game", {}) if isinstance(config.get("game"), dict) else {}
    raw_mods = game.get("mods", [])
    if not isinstance(raw_mods, list):
        return ModsSummary(available=True, count=0)

    preview: list[ModSummaryEntry] = []
    for raw_mod in raw_mods[:preview_limit]:
        if not isinstance(raw_mod, dict):
            continue
        mod_id = str(raw_mod.get("modId", "")).strip()
        name = str(raw_mod.get("name", "")).strip()
        if mod_id or name:
            preview.append(ModSummaryEntry(mod_id=mod_id, name=name))

    count = len(raw_mods)
    return ModsSummary(
        available=True,
        count=count,
        preview=preview,
        remaining_count=max(count - len(preview), 0),
    )


def load_status_summaries(
    config_path: Path | str,
    preview_limit: int = 3,
) -> tuple[ConfigSummary, ModsSummary]:
    """Load config once and derive both config and mod summaries."""
    try:
        config = load_config(config_path)
    except ConfigError:
        return ConfigSummary(False), ModsSummary(False)

    return summarize_config(config), summarize_mods(config, preview_limit=preview_limit)
