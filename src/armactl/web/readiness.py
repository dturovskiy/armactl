"""Read-only readiness checks for the running web process."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from armactl import paths
from armactl.web.runtime.db import WEB_SCHEMA_VERSION
from armactl.web.runtime.paths import resolved_data_root, web_db_file
from armactl.web.services.player_registry import (
    PLAYER_REGISTRY_SCHEMA_VERSION,
    player_registry_db_path,
)

READINESS_STATUS_READY = "ready"
READINESS_STATUS_NOT_CONFIGURED = "not_configured"
READINESS_STATUS_MISSING = "missing"
READINESS_STATUS_SCHEMA_UNAVAILABLE = "schema_unavailable"
READINESS_STATUS_SCHEMA_INVALID = "schema_invalid"
READINESS_STATUS_SCHEMA_NEWER = "schema_newer"


@dataclass(frozen=True)
class SchemaReadinessCheck:
    """One bounded schema compatibility result."""

    name: str
    ready: bool
    status: str
    current_version: int | None = None
    supported_version: int | None = None

    def to_public_dict(self) -> dict[str, bool | str]:
        """Return only safe status fields for the unauthenticated endpoint."""
        return {"ok": self.ready, "status": self.status}


@dataclass(frozen=True)
class WebReadinessReport:
    """Readiness of schema-backed routes in the current process."""

    checks: tuple[SchemaReadinessCheck, ...]

    @property
    def ready(self) -> bool:
        return all(check.ready for check in self.checks)

    def to_public_dict(self) -> dict[str, object]:
        return {
            "ok": self.ready,
            "checks": {check.name: check.to_public_dict() for check in self.checks},
        }


def check_web_readiness(data_root: Path | None = None) -> WebReadinessReport:
    """Inspect existing DB schema metadata without creating or migrating files."""
    root = resolved_data_root(data_root)
    return WebReadinessReport(
        checks=(
            _check_schema_compatibility(
                name="web_db",
                db_path=web_db_file(root),
                metadata_table="web_schema_meta",
                supported_version=int(WEB_SCHEMA_VERSION),
                required=True,
            ),
            _check_schema_compatibility(
                name="players_db",
                db_path=player_registry_db_path(
                    paths.DEFAULT_INSTANCE_NAME,
                    data_root=root,
                ),
                metadata_table="player_registry_schema_meta",
                supported_version=int(PLAYER_REGISTRY_SCHEMA_VERSION),
                required=False,
            ),
        )
    )


def _check_schema_compatibility(
    *,
    name: str,
    db_path: Path,
    metadata_table: str,
    supported_version: int,
    required: bool,
) -> SchemaReadinessCheck:
    if not db_path.is_file():
        return SchemaReadinessCheck(
            name=name,
            ready=not required,
            status=(READINESS_STATUS_MISSING if required else READINESS_STATUS_NOT_CONFIGURED),
            supported_version=supported_version,
        )

    uri_path = quote(db_path.resolve().as_posix(), safe=":/")
    try:
        with sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True) as connection:
            connection.execute("PRAGMA query_only = ON")
            row = connection.execute(
                f"SELECT value FROM {metadata_table} WHERE key = ?",
                ("schema_version",),
            ).fetchone()
    except sqlite3.Error:
        return SchemaReadinessCheck(
            name=name,
            ready=False,
            status=READINESS_STATUS_SCHEMA_UNAVAILABLE,
            supported_version=supported_version,
        )

    if row is None:
        return SchemaReadinessCheck(
            name=name,
            ready=False,
            status=READINESS_STATUS_SCHEMA_INVALID,
            supported_version=supported_version,
        )
    try:
        current_version = int(str(row[0]))
    except (TypeError, ValueError):
        return SchemaReadinessCheck(
            name=name,
            ready=False,
            status=READINESS_STATUS_SCHEMA_INVALID,
            supported_version=supported_version,
        )
    if current_version < 0 or current_version > supported_version:
        return SchemaReadinessCheck(
            name=name,
            ready=False,
            status=(
                READINESS_STATUS_SCHEMA_INVALID
                if current_version < 0
                else READINESS_STATUS_SCHEMA_NEWER
            ),
            current_version=current_version,
            supported_version=supported_version,
        )
    return SchemaReadinessCheck(
        name=name,
        ready=True,
        status=READINESS_STATUS_READY,
        current_version=current_version,
        supported_version=supported_version,
    )
