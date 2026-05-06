"""Server package integrity tracking for armactl-managed installs."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

APP_ID = "1874900"
MANIFEST_NAME = ".armactl-package-manifest.json"
INSTALL_MARKER_NAME = ".armactl-installing"
MANIFEST_VERSION = 1
SERVER_BINARY_NAME = "ArmaReforgerServer"

_SKIPPED_DIR_PREFIXES = (
    "steamapps/",
)


class IntegrityError(Exception):
    """Raised when package integrity metadata cannot be created."""


@dataclass
class PackageIntegrity:
    """Result of checking an install directory against armactl metadata."""

    status: str
    binary_exists: bool = False
    manifest_exists: bool = False
    install_in_progress: bool = False
    steam_manifest_exists: bool = False
    steam_complete: bool | None = None
    checked_files: int = 0
    expected_files: int = 0
    missing_files: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """Return True when the managed package manifest verifies successfully."""
        return self.status == "ok"

    @property
    def has_install_evidence(self) -> bool:
        """Return True when this directory looks related to a server install."""
        return any(
            [
                self.binary_exists,
                self.manifest_exists,
                self.install_in_progress,
                self.steam_manifest_exists,
            ]
        )

    def summary(self, *, limit: int = 5) -> str:
        """Return a compact human-readable summary for logs and errors."""
        parts = [self.status]
        if self.missing_files:
            sample = ", ".join(self.missing_files[:limit])
            parts.append(f"missing: {sample}")
        if self.changed_files:
            sample = ", ".join(self.changed_files[:limit])
            parts.append(f"changed: {sample}")
        return "; ".join(parts)


def package_manifest_path(install_dir: Path) -> Path:
    """Return the armactl package manifest path for an install directory."""
    return install_dir / MANIFEST_NAME


def install_marker_path(install_dir: Path) -> Path:
    """Return the in-progress marker path for an install directory."""
    return install_dir / INSTALL_MARKER_NAME


def steam_appmanifest_path(install_dir: Path) -> Path:
    """Return the Steam appmanifest path used by SteamCMD for this app."""
    return install_dir / "steamapps" / f"appmanifest_{APP_ID}.acf"


def mark_install_started(install_dir: Path) -> None:
    """Create a marker that prevents interrupted installs from looking complete."""
    install_dir.mkdir(parents=True, exist_ok=True)
    marker = install_marker_path(install_dir)
    payload = {
        "app_id": APP_ID,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    marker.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def clear_install_marker(install_dir: Path) -> None:
    """Remove the in-progress marker if present."""
    marker = install_marker_path(install_dir)
    try:
        marker.unlink()
    except FileNotFoundError:
        return


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _should_skip_relative_path(relative_path: str) -> bool:
    if relative_path in {MANIFEST_NAME, INSTALL_MARKER_NAME}:
        return True
    if relative_path.startswith(".armactl-"):
        return True
    return any(relative_path.startswith(prefix) for prefix in _SKIPPED_DIR_PREFIXES)


def _iter_package_files(install_dir: Path) -> list[Path]:
    files: list[Path] = []
    if not install_dir.is_dir():
        return files

    for path in install_dir.rglob("*"):
        if not path.is_file():
            continue
        relative_path = path.relative_to(install_dir).as_posix()
        if _should_skip_relative_path(relative_path):
            continue
        files.append(path)

    return sorted(files, key=lambda p: p.relative_to(install_dir).as_posix())


def write_package_manifest(install_dir: Path) -> Path:
    """Record a full file manifest for the installed server package."""
    install_dir = Path(install_dir)
    binary = install_dir / SERVER_BINARY_NAME
    if not binary.is_file():
        raise IntegrityError(f"server binary missing at {binary}")

    files = []
    for path in _iter_package_files(install_dir):
        stat = path.stat()
        files.append(
            {
                "path": path.relative_to(install_dir).as_posix(),
                "size": stat.st_size,
                "sha256": _hash_file(path),
            }
        )

    payload = {
        "version": MANIFEST_VERSION,
        "app_id": APP_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
    }

    manifest_path = package_manifest_path(install_dir)
    tmp_path = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(manifest_path)
    return manifest_path


def _load_package_manifest(manifest_path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _read_steam_complete(install_dir: Path) -> tuple[bool, bool | None]:
    """Return (manifest_exists, complete?) based on SteamCMD's appmanifest."""
    manifest_path = steam_appmanifest_path(install_dir)
    if not manifest_path.is_file():
        return False, None

    try:
        content = manifest_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return True, None

    state_match = re.search(r'"StateFlags"\s+"(?P<value>\d+)"', content)
    if state_match and state_match.group("value") != "4":
        return True, False

    downloaded_match = re.search(r'"BytesDownloaded"\s+"(?P<value>\d+)"', content)
    needed_match = re.search(r'"BytesToDownload"\s+"(?P<value>\d+)"', content)
    if downloaded_match and needed_match:
        downloaded = int(downloaded_match.group("value"))
        needed = int(needed_match.group("value"))
        if needed > 0 and downloaded < needed:
            return True, False

    return True, True if state_match else None


def check_package_integrity(
    install_dir: Path,
    *,
    verify_hashes: bool = False,
    report_limit: int = 25,
) -> PackageIntegrity:
    """Check whether the server package is complete.

    Discovery uses size/presence checks for speed. Install and repair can pass
    ``verify_hashes=True`` when a full byte-level check is worth the extra I/O.
    """
    install_dir = Path(install_dir)
    binary_exists = (install_dir / SERVER_BINARY_NAME).is_file()
    manifest_path = package_manifest_path(install_dir)
    marker_exists = install_marker_path(install_dir).is_file()
    steam_manifest_exists, steam_complete = _read_steam_complete(install_dir)

    base = PackageIntegrity(
        status="empty",
        binary_exists=binary_exists,
        manifest_exists=manifest_path.is_file(),
        install_in_progress=marker_exists,
        steam_manifest_exists=steam_manifest_exists,
        steam_complete=steam_complete,
    )

    if marker_exists:
        base.status = "installing"
        return base

    if not manifest_path.is_file():
        if steam_manifest_exists and steam_complete is False:
            base.status = "steam_incomplete"
            return base
        if binary_exists:
            base.status = "untracked"
            return base
        return base

    data = _load_package_manifest(manifest_path)
    if not data:
        base.status = "manifest_invalid"
        return base

    raw_files = data.get("files", [])
    if data.get("version") != MANIFEST_VERSION or data.get("app_id") != APP_ID:
        base.status = "manifest_invalid"
        return base
    if not isinstance(raw_files, list):
        base.status = "manifest_invalid"
        return base

    missing_files: list[str] = []
    changed_files: list[str] = []
    checked_files = 0

    for item in raw_files:
        if not isinstance(item, dict):
            changed_files.append("<invalid-manifest-entry>")
            continue

        relative_path = item.get("path")
        if not isinstance(relative_path, str) or not relative_path:
            changed_files.append("<invalid-manifest-entry>")
            continue
        relative_parts = Path(relative_path).parts
        if Path(relative_path).is_absolute() or ".." in relative_parts:
            changed_files.append("<invalid-manifest-entry>")
            continue

        path = install_dir / relative_path
        if not path.is_file():
            if len(missing_files) < report_limit:
                missing_files.append(relative_path)
            continue

        checked_files += 1
        expected_size = item.get("size")
        try:
            actual_size = path.stat().st_size
        except OSError:
            if len(missing_files) < report_limit:
                missing_files.append(relative_path)
            continue

        changed = isinstance(expected_size, int) and actual_size != expected_size
        if not changed and verify_hashes:
            expected_hash = item.get("sha256")
            changed = isinstance(expected_hash, str) and _hash_file(path) != expected_hash

        if changed and len(changed_files) < report_limit:
            changed_files.append(relative_path)

    if missing_files:
        status = "missing_files"
    elif changed_files:
        status = "changed_files"
    elif steam_manifest_exists and steam_complete is False:
        status = "steam_incomplete"
    elif not binary_exists:
        status = "missing_binary"
    else:
        status = "ok"

    return PackageIntegrity(
        status=status,
        binary_exists=binary_exists,
        manifest_exists=True,
        install_in_progress=False,
        steam_manifest_exists=steam_manifest_exists,
        steam_complete=steam_complete,
        checked_files=checked_files,
        expected_files=len(raw_files),
        missing_files=missing_files,
        changed_files=changed_files,
    )
