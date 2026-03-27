"""State management — read/write state.json for instance tracking.

The ServerState dataclass holds everything discovery knows about an instance.
It can be serialized to / deserialized from state.json.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class PortInfo:
    """Listening ports for the server."""

    game: int | None = None
    a2s: int | None = None
    rcon: int | None = None


@dataclass
class ServerState:
    """Complete state of a server instance."""

    # --- existence flags ---
    server_installed: bool = False
    binary_exists: bool = False
    config_exists: bool = False
    service_exists: bool = False
    timer_exists: bool = False

    # --- runtime status ---
    server_running: bool = False

    # --- paths ---
    instance_root: str = ""
    install_dir: str = ""
    config_path: str = ""

    # --- systemd ---
    service_name: str = "armareforger.service"
    timer_name: str = "armareforger-restart.timer"

    # --- ports ---
    ports: PortInfo = field(default_factory=PortInfo)

    # --- metadata ---
    discovered_at: str = ""
    migrated_from: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert state to a JSON-serializable dict."""
        data = asdict(self)
        # Ensure discovered_at is always set
        if not data.get("discovered_at"):
            data["discovered_at"] = datetime.now(timezone.utc).isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ServerState:
        """Create a ServerState from a dict (e.g. parsed JSON)."""
        ports_data = data.pop("ports", {})
        if isinstance(ports_data, dict):
            ports = PortInfo(**ports_data)
        else:
            ports = PortInfo()

        return cls(ports=ports, **data)


def save_state(state: ServerState, path: Path) -> None:
    """Write state to a JSON file. Creates parent directories if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(state.to_dict(), indent=2, ensure_ascii=False) + "\n")
        tmp.replace(path)
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise


def load_state(path: Path) -> ServerState | None:
    """Read state from a JSON file. Returns None if file doesn't exist or is invalid."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
        return ServerState.from_dict(data)
    except (json.JSONDecodeError, TypeError, KeyError):
        return None
