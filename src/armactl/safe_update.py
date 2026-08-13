"""Transactional Arma Reforger server updates with compatibility canaries.

The updater keeps the active server package and configuration untouched while a
new Steam build is downloaded and exercised with an isolated config bundle.
Canaries read the canonical shared Workshop pool; profile switching never moves
or deletes that pool. Promotion keeps one package/config rollback generation. A
failed canary or failed production start restores and restarts the previous
generation.
"""

from __future__ import annotations

import fcntl
import json
import os
import queue
import re
import secrets
import shutil
import signal
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from armactl import a2s, installer, integrity, mod_compatibility, paths
from armactl.config_manager import ConfigError, load_config
from armactl.platform.service_adapter import ServiceAdapter, get_service_adapter
from armactl.redaction import redact_sensitive_text
from armactl.state import PortInfo, ServerState
from armactl.update_compatibility import (
    DEFAULT_VANILLA_SCENARIO,
    MAX_SERVER_NAME_LENGTH,
    MODDED_MODE,
    VANILLA_MODE,
    CompatibilityConfigError,
    CompatibilityStatus,
    configured_mods,
    create_update_baseline,
    make_vanilla_profile,
    read_compatibility_status,
)
from armactl.update_profiles import (
    DEFAULT_ACTIVE_PROFILE,
    DEFAULT_VANILLA_PROFILE,
    NamedProfile,
    UpdatePolicy,
    UpdateProfileError,
    create_profile,
    inspect_profile,
    list_profiles,
    profile_path,
    read_policy,
    read_profile_selection,
    validate_profile_name,
    write_policy,
    write_profile_selection,
)

UPDATE_DIR_NAME = "server-update"
UPDATE_METADATA_NAME = "state.json"
CANDIDATE_SERVER_NAME = "candidate-server"
CANDIDATE_PROFILE_NAME = "candidate-profile"
CANDIDATE_ADDONS_NAME = "candidate-addons"
CANDIDATE_LOGS_NAME = "canary-logs"
ROLLBACK_SERVER_NAME = "rollback-server"
ROLLBACK_PROFILE_NAME = "rollback-profile"
FAILED_SERVER_NAME = "failed-server"
FAILED_PROFILE_NAME = "failed-profile"
PARKED_MODDED_PROFILE_NAME = "parked-modded-profile"
PROFILES_DIR_NAME = "profiles"
UPDATE_POLICY_NAME = "policy.json"
MOD_COMPATIBILITY_NAME = "mod-compatibility.json"
LAST_CANARY_FAILURE_NAME = "last-canary-failure.log"

CANARY_TIMEOUT_SECONDS = 600.0
CANARY_STABILITY_SECONDS = 20.0
PRODUCTION_TIMEOUT_SECONDS = 300.0
PRODUCTION_STABILITY_SECONDS = 20.0
POLL_INTERVAL_SECONDS = 2.0
MIN_FREE_SPACE_BUFFER_BYTES = 2 * 1024**3
FREE_SPACE_FACTOR = 1.10
MAX_CANARY_TAIL_LINES = 80
FATAL_CANARY_DIAGNOSTIC_GRACE_SECONDS = 0.25
MAX_CANARY_ERROR_LINES = 8
MAX_CANARY_ERROR_LINE_LENGTH = 500

FATAL_CANARY_MARKERS = (
    'Can\'t compile "Game" script module',
    "Unable to initialize the game",
    "Addon loading failed",
    "Cannot create game",
)

_BUILD_ID_RE = re.compile(r'"buildid"\s+"(?P<build>\d+)"', re.IGNORECASE)


def _summarize_canary_failure(lines: Iterable[str]) -> str:
    """Keep the operator error useful without embedding an unbounded addon list."""
    summarized: list[str] = []
    addon_marker = "Addon loading failed"
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        marker_at = line.casefold().find(addon_marker.casefold())
        if marker_at >= 0:
            marker_end = marker_at + len(addon_marker)
            line = line[:marker_end] + " (addon list omitted; see diagnostic)"
        line = line[:MAX_CANARY_ERROR_LINE_LENGTH]
        if line not in summarized:
            summarized.append(line)
        if len(summarized) >= MAX_CANARY_ERROR_LINES:
            break
    return " | ".join(summarized)


class SafeUpdateError(RuntimeError):
    """Raised when a transactional server update cannot be completed safely."""


class CanaryRejectedError(SafeUpdateError):
    """Raised when the candidate build cannot load the configured scenario/mods."""


class UpdateAlreadyRunningError(SafeUpdateError):
    """Raised when another CLI or web worker owns the instance update lock."""


@dataclass(frozen=True)
class UpdatePaths:
    """All paths owned by one transactional update."""

    instance_root: Path
    server: Path
    profile: Path
    config_file: Path
    update_root: Path
    metadata: Path
    candidate_server: Path
    candidate_profile: Path
    candidate_addons: Path
    candidate_logs: Path
    rollback_server: Path
    rollback_profile: Path
    failed_server: Path
    failed_profile: Path
    parked_modded_profile: Path
    profiles_root: Path
    policy: Path
    mod_compatibility: Path


@dataclass(frozen=True)
class CanaryResult:
    """Successful compatibility-canary evidence."""

    ready_seconds: float
    max_players: int | None
    map_name: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolved(path: Path | str) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def resolve_update_paths(install_dir: Path, config_path: Path) -> UpdatePaths:
    """Resolve and validate the canonical package/profile update boundary."""
    server = _resolved(install_dir)
    config_file = _resolved(config_path)
    instance_root = server.parent
    profile = instance_root / "config"
    expected_config = profile / "config.json"

    if server.name != "server" or config_file != _resolved(expected_config):
        raise SafeUpdateError(
            "Safe updates require the canonical <instance>/server and "
            "<instance>/config/config.json layout."
        )
    if server.is_symlink() or profile.is_symlink():
        raise SafeUpdateError("Safe updates refuse symlinked server or profile roots.")
    if not server.is_dir() or not (server / integrity.SERVER_BINARY_NAME).is_file():
        raise SafeUpdateError("Server install directory is incomplete.")
    if not profile.is_dir() or not config_file.is_file():
        raise SafeUpdateError("Server profile or config.json is missing.")

    update_root = instance_root / UPDATE_DIR_NAME
    return UpdatePaths(
        instance_root=instance_root,
        server=server,
        profile=profile,
        config_file=config_file,
        update_root=update_root,
        metadata=update_root / UPDATE_METADATA_NAME,
        candidate_server=update_root / CANDIDATE_SERVER_NAME,
        candidate_profile=update_root / CANDIDATE_PROFILE_NAME,
        candidate_addons=update_root / CANDIDATE_ADDONS_NAME,
        candidate_logs=update_root / CANDIDATE_LOGS_NAME,
        rollback_server=update_root / ROLLBACK_SERVER_NAME,
        rollback_profile=update_root / ROLLBACK_PROFILE_NAME,
        failed_server=update_root / FAILED_SERVER_NAME,
        failed_profile=update_root / FAILED_PROFILE_NAME,
        parked_modded_profile=update_root / PARKED_MODDED_PROFILE_NAME,
        profiles_root=update_root / PROFILES_DIR_NAME,
        policy=update_root / UPDATE_POLICY_NAME,
        mod_compatibility=update_root / MOD_COMPATIBILITY_NAME,
    )


def _assert_managed_update_path(update_paths: UpdatePaths, target: Path) -> Path:
    target = Path(target)
    if target.parent != update_paths.update_root or target == update_paths.update_root:
        raise SafeUpdateError("Refusing to mutate a path outside the managed update area.")
    return target


def _remove_managed_path(update_paths: UpdatePaths, target: Path) -> None:
    target = _assert_managed_update_path(update_paths, target)
    if target.is_symlink() or target.is_file():
        target.unlink(missing_ok=True)
    elif target.exists():
        shutil.rmtree(target)


def _write_metadata(update_paths: UpdatePaths, phase: str, **fields: Any) -> None:
    update_paths.update_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = _load_metadata(update_paths)
    payload.update({
        "version": 2,
        "phase": phase,
        "updated_at": _utc_now(),
    })
    payload.update(fields)
    tmp_path = update_paths.metadata.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp_path, 0o600)
    tmp_path.replace(update_paths.metadata)


def _load_metadata(update_paths: UpdatePaths) -> dict[str, Any]:
    try:
        value = json.loads(update_paths.metadata.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _acquire_update_lock(instance_root: Path) -> int:
    lock_path = instance_root / ".server-update.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(descriptor)
        raise UpdateAlreadyRunningError(
            "Another server update or rollback is already running for this instance."
        ) from exc
    return descriptor


def _release_update_lock(descriptor: int) -> None:
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def read_build_id(install_dir: Path) -> str:
    """Read a safe numeric Steam build ID from a local appmanifest."""
    try:
        text = integrity.steam_appmanifest_path(install_dir).read_text(
            encoding="utf-8", errors="ignore"
        )
    except OSError:
        return ""
    match = _BUILD_ID_RE.search(text)
    return match.group("build") if match else ""


def _allocated_tree_bytes(root: Path) -> int:
    total = 0
    for dir_path, dir_names, file_names in os.walk(root, followlinks=False):
        current = Path(dir_path)
        dir_names[:] = [
            name for name in dir_names if not (current / name).is_symlink()
        ]
        for name in file_names:
            try:
                stat = (current / name).lstat()
            except OSError:
                continue
            total += max(stat.st_blocks * 512, stat.st_size)
    return total


def ensure_staging_capacity(update_paths: UpdatePaths) -> tuple[int, int]:
    """Fail closed unless one isolated server+profile generation fits on disk."""
    source_profile = (
        update_paths.parked_modded_profile
        if update_paths.parked_modded_profile.is_dir()
        else update_paths.profile
    )
    required_payload = _allocated_tree_bytes(update_paths.server) + _allocated_tree_bytes(
        source_profile
    )
    required = int(required_payload * FREE_SPACE_FACTOR) + MIN_FREE_SPACE_BUFFER_BYTES
    free = shutil.disk_usage(update_paths.instance_root).free
    if free < required:
        raise SafeUpdateError(
            "Not enough free disk space for an isolated update canary: "
            f"need at least {required // 1024**3} GiB, have {free // 1024**3} GiB."
        )
    return required, free


def _copy_profile_bundle(
    update_paths: UpdatePaths,
    source: Path,
    destination: Path,
) -> None:
    """Store only scenario/mod selection; runtime settings remain canonical."""
    del update_paths
    if destination.exists() or destination.is_symlink():
        raise SafeUpdateError(f"Refusing to overwrite profile bundle: {destination}")
    try:
        selection = read_profile_selection(source)
        write_profile_selection(destination, selection)
    except UpdateProfileError as exc:
        raise SafeUpdateError(str(exc)) from exc


def _render_profile_candidate(
    update_paths: UpdatePaths,
    source: Path,
    destination: Path,
) -> None:
    """Build a full candidate config from current settings plus target selection."""
    if destination.exists() or destination.is_symlink():
        raise SafeUpdateError(f"Refusing to overwrite profile candidate: {destination}")
    try:
        active = load_config(update_paths.config_file)
        selection = read_profile_selection(source)
    except (ConfigError, UpdateProfileError) as exc:
        raise SafeUpdateError(f"Could not prepare profile candidate: {exc}") from exc
    active_game = active.get("game")
    if not isinstance(active_game, dict):
        raise SafeUpdateError("Active config is missing the game object.")
    active_game["scenarioId"] = selection["game"]["scenarioId"]
    active_game["mods"] = deepcopy(selection["game"]["mods"])
    destination.mkdir(parents=True, mode=0o700)
    candidate = destination / "config.json"
    candidate.write_text(
        json.dumps(active, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.chmod(candidate, 0o600)


def _activate_profile_bundle(update_paths: UpdatePaths, source: Path) -> None:
    """Atomically apply only scenario/mods and preserve all other active settings."""
    try:
        active = load_config(update_paths.config_file)
        selection = read_profile_selection(source)
    except (ConfigError, UpdateProfileError) as exc:
        raise SafeUpdateError(f"Profile bundle is invalid: {exc}") from exc
    game = active.get("game")
    if not isinstance(game, dict):
        raise SafeUpdateError("Active config is missing the game object.")
    game["scenarioId"] = selection["game"]["scenarioId"]
    game["mods"] = deepcopy(selection["game"]["mods"])
    temporary = update_paths.config_file.with_suffix(".json.profile-switch.tmp")
    temporary.write_text(
        json.dumps(active, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(update_paths.config_file)


def _prepare_canary_config(config_path: Path) -> None:
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        raise SafeUpdateError(f"Candidate config is invalid: {exc}") from exc
    game = config.get("game")
    if not isinstance(game, dict):
        raise SafeUpdateError("Candidate config is missing the game object.")
    # Reforger's server schema permits at most 32 non-space characters.
    game["password"] = secrets.token_urlsafe(24)
    name = str(game.get("name") or "Arma Reforger")
    game["name"] = f"{name} [armactl update canary]"[:MAX_SERVER_NAME_LENGTH]
    tmp_path = config_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.chmod(tmp_path, 0o600)
    tmp_path.replace(config_path)


def prepare_candidate(update_paths: UpdatePaths, *, source_profile: Path | None = None) -> None:
    """Create an isolated config bundle and empty candidate server directory."""
    source_profile = source_profile or update_paths.profile
    update_paths.update_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    for target in (
        update_paths.candidate_server,
        update_paths.candidate_profile,
        update_paths.candidate_addons,
        update_paths.candidate_logs,
        update_paths.failed_server,
        update_paths.failed_profile,
    ):
        _remove_managed_path(update_paths, target)

    _render_profile_candidate(
        update_paths,
        source_profile,
        update_paths.candidate_profile,
    )
    update_paths.candidate_server.mkdir(mode=0o700)
    update_paths.candidate_logs.mkdir(mode=0o700)
    _prepare_canary_config(update_paths.candidate_profile / "config.json")


def cleanup_candidate_artifacts(update_paths: UpdatePaths) -> None:
    """Remove only disposable artifacts from a prior incomplete staging run."""
    for target in (
        update_paths.candidate_server,
        update_paths.candidate_profile,
        update_paths.candidate_addons,
        update_paths.candidate_logs,
        update_paths.failed_server,
        update_paths.failed_profile,
    ):
        _remove_managed_path(update_paths, target)


def prepare_vanilla_candidate(
    update_paths: UpdatePaths,
    *,
    source_profile: Path | None = None,
) -> int:
    """Replace candidate runtime state with an addon-free official scenario."""
    source_profile = source_profile or update_paths.profile
    for target in (
        update_paths.candidate_profile,
        update_paths.candidate_addons,
        update_paths.candidate_logs,
    ):
        _remove_managed_path(update_paths, target)
    update_paths.candidate_logs.mkdir(parents=True, mode=0o700)
    try:
        source_disabled_count = len(
            read_profile_selection(source_profile)["game"]["mods"]
        )
        active_disabled_count = make_vanilla_profile(
            update_paths.profile,
            update_paths.candidate_profile,
        )
    except (CompatibilityConfigError, UpdateProfileError) as exc:
        raise SafeUpdateError(str(exc)) from exc
    _prepare_canary_config(update_paths.candidate_profile / "config.json")
    return max(source_disabled_count, active_disabled_count)


def reset_candidate_profile_after_canary(
    update_paths: UpdatePaths,
    *,
    source_profile: Path | None = None,
    mode: str = MODDED_MODE,
) -> None:
    """Discard canary runtime writes and rebuild the config-only candidate bundle."""
    source_profile = source_profile or update_paths.profile
    if mode == VANILLA_MODE:
        _remove_managed_path(update_paths, update_paths.candidate_profile)
        try:
            make_vanilla_profile(source_profile, update_paths.candidate_profile)
        except CompatibilityConfigError as exc:
            raise SafeUpdateError(str(exc)) from exc
        return
    if mode != MODDED_MODE:
        raise SafeUpdateError(f"Unknown candidate profile mode: {mode}")
    _remove_managed_path(update_paths, update_paths.candidate_profile)
    _render_profile_candidate(
        update_paths,
        source_profile,
        update_paths.candidate_profile,
    )


def _candidate_command(
    update_paths: UpdatePaths,
    *,
    server_dir: Path | None = None,
) -> list[str]:
    server_dir = server_dir or update_paths.candidate_server
    return [
        str(server_dir / integrity.SERVER_BINARY_NAME),
        "-config",
        str(update_paths.candidate_profile / "config.json"),
        "-profile",
        str(update_paths.candidate_profile),
        "-addonsDir",
        str(update_paths.profile / "addons"),
        "-addonDownloadDir",
        str(update_paths.candidate_profile),
        "-logsDir",
        str(update_paths.candidate_logs),
        "-backendFreshSession",
        "-addonsVerify",
        "-noThrow",
        "-logStats",
        "10000",
        "-maxFPS",
        "60",
    ]


def _terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGINT)
        process.wait(timeout=20)
        return
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)
        return
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return


def _candidate_player_status(config_path: Path) -> a2s.PlayerStatus:
    return a2s.query_player_status(
        "update-canary",
        timeout=0.75,
        state=ServerState(
            server_running=True,
            config_exists=True,
            config_path=str(config_path),
            ports=PortInfo(),
        ),
    )


def _write_canary_failure_diagnostic(
    update_paths: UpdatePaths,
    lines: Iterable[str],
) -> Path:
    """Persist one bounded redacted console tail outside disposable canary paths."""
    diagnostic = update_paths.update_root / LAST_CANARY_FAILURE_NAME
    update_paths.update_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    safe_lines = [redact_sensitive_text(line)[:4000] for line in lines if line]
    text = "\n".join(safe_lines[-MAX_CANARY_TAIL_LINES:])[-64 * 1024 :]
    temporary = diagnostic.with_suffix(".log.tmp")
    temporary.write_text(text + ("\n" if text else ""), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(diagnostic)
    return diagnostic


def run_compatibility_canary(
    update_paths: UpdatePaths,
    *,
    server_dir: Path | None = None,
    timeout_seconds: float = CANARY_TIMEOUT_SECONDS,
    stability_seconds: float = CANARY_STABILITY_SECONDS,
    poll_interval_seconds: float = POLL_INTERVAL_SECONDS,
    popen: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
    status_probe: Callable[[Path], a2s.PlayerStatus] = _candidate_player_status,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> CanaryResult:
    """Boot the candidate until A2S is stable or a fatal mod error appears."""
    server_dir = server_dir or update_paths.candidate_server
    command = _candidate_command(update_paths, server_dir=server_dir)
    try:
        process = popen(
            command,
            cwd=server_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            bufsize=1,
            start_new_session=True,
        )
    except OSError as exc:
        raise CanaryRejectedError(
            f"Candidate server could not start: {redact_sensitive_text(exc)}"
        ) from exc

    output_queue: queue.Queue[str] = queue.Queue()
    output_tail: deque[str] = deque(maxlen=MAX_CANARY_TAIL_LINES)

    def read_output() -> None:
        if process.stdout is None:
            return
        for raw_line in process.stdout:
            output_queue.put(redact_sensitive_text(raw_line.rstrip()))

    reader = threading.Thread(target=read_output, name="armactl-update-canary-output", daemon=True)
    reader.start()
    started_at = monotonic()
    ready_since: float | None = None
    last_status: a2s.PlayerStatus | None = None
    fatal_line = ""
    fatal_seen_at: float | None = None
    fatal_context: list[str] = []

    try:
        while monotonic() - started_at < timeout_seconds:
            while True:
                try:
                    line = output_queue.get_nowait()
                except queue.Empty:
                    break
                if line:
                    output_tail.append(line)
                    if fatal_line:
                        fatal_context.append(line)
                if not fatal_line and any(
                    marker.casefold() in line.casefold()
                    for marker in FATAL_CANARY_MARKERS
                ):
                    fatal_line = line
                    fatal_seen_at = monotonic()
                    fatal_context = [line]

            return_code = process.poll()
            if fatal_line and (
                return_code is not None
                or (
                    fatal_seen_at is not None
                    and monotonic() - fatal_seen_at
                    >= FATAL_CANARY_DIAGNOSTIC_GRACE_SECONDS
                )
            ):
                diagnostic = _write_canary_failure_diagnostic(
                    update_paths,
                    output_tail,
                )
                tail = _summarize_canary_failure(fatal_context)
                raise CanaryRejectedError(
                    "Candidate rejected by scenario/mod compilation: "
                    + (tail or fatal_line)
                    + f"; redacted diagnostic: {diagnostic}"
                )
            if return_code is not None:
                tail = " | ".join(list(output_tail)[-5:])
                diagnostic = _write_canary_failure_diagnostic(
                    update_paths,
                    output_tail,
                )
                detail = f"; last output: {tail}" if tail else ""
                raise CanaryRejectedError(
                    f"Candidate server exited before readiness (code {return_code}){detail}; "
                    f"redacted diagnostic: {diagnostic}"
                )

            if fatal_line:
                sleep(poll_interval_seconds)
                continue

            last_status = status_probe(update_paths.candidate_profile / "config.json")
            now = monotonic()
            if last_status.available:
                if ready_since is None:
                    ready_since = now
                if now - ready_since >= stability_seconds:
                    return CanaryResult(
                        ready_seconds=now - started_at,
                        max_players=last_status.max_players,
                        map_name=last_status.map_name,
                    )
            else:
                ready_since = None
            sleep(poll_interval_seconds)

        error = last_status.error if last_status is not None else "A2S never became ready"
        diagnostic = _write_canary_failure_diagnostic(update_paths, output_tail)
        raise CanaryRejectedError(
            "Candidate did not reach stable A2S readiness before timeout: "
            + redact_sensitive_text(error)
            + f"; redacted diagnostic: {diagnostic}"
        )
    finally:
        _terminate_process(process)
        reader.join(timeout=1.0)


def _clear_previous_rollback(update_paths: UpdatePaths) -> None:
    for target in (update_paths.rollback_server, update_paths.rollback_profile):
        _remove_managed_path(update_paths, target)


def retire_previous_rollback(update_paths: UpdatePaths) -> bool:
    """Retire one old package/config rollback before staging its replacement."""
    server_exists = update_paths.rollback_server.exists()
    profile_exists = update_paths.rollback_profile.exists()
    if not server_exists and not profile_exists:
        return False
    _clear_previous_rollback(update_paths)
    return True


def promote_candidate(
    update_paths: UpdatePaths,
    *,
    old_build: str,
    new_build: str,
    previous_mode: str = MODDED_MODE,
    target_mode: str = MODDED_MODE,
    previous_profile: str = DEFAULT_ACTIVE_PROFILE,
    target_profile: str = DEFAULT_ACTIVE_PROFILE,
) -> None:
    """Swap the verified candidate into the canonical paths."""
    if not (update_paths.candidate_server / integrity.SERVER_BINARY_NAME).is_file():
        raise SafeUpdateError("Candidate server package is incomplete.")
    if not (update_paths.candidate_profile / "config.json").is_file():
        raise SafeUpdateError("Candidate server profile is incomplete.")

    if previous_mode not in {MODDED_MODE, VANILLA_MODE}:
        raise SafeUpdateError(f"Unknown previous compatibility mode: {previous_mode}")
    if target_mode not in {MODDED_MODE, VANILLA_MODE}:
        raise SafeUpdateError(f"Unknown target compatibility mode: {target_mode}")
    parks_modded = previous_mode == MODDED_MODE and target_mode == VANILLA_MODE
    if parks_modded and update_paths.parked_modded_profile.exists():
        raise SafeUpdateError(
            "A parked modded profile already exists; refusing to overwrite it."
        )

    _clear_previous_rollback(update_paths)
    rollback_kind = "server+parked-modded" if parks_modded else "server+profile"
    _write_metadata(
        update_paths,
        "promoting",
        old_build=old_build,
        new_build=new_build,
        previous_mode=previous_mode,
        target_mode=target_mode,
        previous_profile=previous_profile,
        target_profile=target_profile,
        rollback_kind=rollback_kind,
    )
    try:
        update_paths.server.replace(update_paths.rollback_server)
        update_paths.candidate_server.replace(update_paths.server)
        if parks_modded:
            _copy_profile_bundle(
                update_paths,
                update_paths.profile,
                update_paths.parked_modded_profile,
            )
        else:
            _copy_profile_bundle(
                update_paths,
                update_paths.profile,
                update_paths.rollback_profile,
            )
        _activate_profile_bundle(update_paths, update_paths.candidate_profile)
    except Exception:
        recover_interrupted_promotion(update_paths)
        raise
    _write_metadata(
        update_paths,
        "promoted",
        old_build=old_build,
        new_build=new_build,
        active_mode=target_mode,
        previous_mode=previous_mode,
        target_mode=target_mode,
        previous_profile=previous_profile,
        target_profile=target_profile,
        rollback_kind=rollback_kind,
    )


def _replace_with_rollback(canonical: Path, rollback: Path, failed: Path) -> None:
    if canonical.exists() or canonical.is_symlink():
        canonical.replace(failed)
    rollback.replace(canonical)


def _restore_rollback_paths(update_paths: UpdatePaths) -> tuple[str, str]:
    """Restore canonical paths using the transaction recorded in metadata."""
    metadata = _load_metadata(update_paths)
    previous_mode = str(metadata.get("previous_mode") or MODDED_MODE)
    target_mode = str(metadata.get("target_mode") or MODDED_MODE)
    rollback_kind = str(metadata.get("rollback_kind") or "server+profile")
    for failed in (update_paths.failed_server, update_paths.failed_profile):
        _remove_managed_path(update_paths, failed)

    if rollback_kind.startswith("server+"):
        if not update_paths.rollback_server.exists():
            raise SafeUpdateError("The retained rollback server package is missing.")
        _replace_with_rollback(
            update_paths.server,
            update_paths.rollback_server,
            update_paths.failed_server,
        )

    if rollback_kind.endswith("+parked-modded"):
        if not update_paths.parked_modded_profile.exists():
            if update_paths.profile.exists():
                return previous_mode, target_mode
            raise SafeUpdateError("The parked modded profile required for rollback is missing.")
        _copy_profile_bundle(update_paths, update_paths.profile, update_paths.failed_profile)
        _activate_profile_bundle(update_paths, update_paths.parked_modded_profile)
        _remove_managed_path(update_paths, update_paths.parked_modded_profile)
    else:
        if not update_paths.rollback_profile.exists():
            if update_paths.profile.exists():
                return previous_mode, target_mode
            raise SafeUpdateError("The retained rollback profile is missing.")
        if previous_mode == VANILLA_MODE and target_mode == MODDED_MODE:
            if update_paths.parked_modded_profile.exists():
                _copy_profile_bundle(
                    update_paths,
                    update_paths.profile,
                    update_paths.failed_profile,
                )
                _activate_profile_bundle(update_paths, update_paths.rollback_profile)
            else:
                _copy_profile_bundle(
                    update_paths,
                    update_paths.profile,
                    update_paths.parked_modded_profile,
                )
                _activate_profile_bundle(update_paths, update_paths.rollback_profile)
        else:
            _copy_profile_bundle(update_paths, update_paths.profile, update_paths.failed_profile)
            _activate_profile_bundle(update_paths, update_paths.rollback_profile)
        _remove_managed_path(update_paths, update_paths.rollback_profile)
    return previous_mode, target_mode


def rollback_promoted_candidate(
    update_paths: UpdatePaths,
    *,
    old_build: str,
    failed_build: str,
    discard_failed: bool = True,
) -> None:
    """Restore the old package/profile and discard the failed generation."""
    _write_metadata(
        update_paths,
        "rolling-back",
        old_build=old_build,
        failed_build=failed_build,
    )
    previous_mode, _target_mode = _restore_rollback_paths(update_paths)
    if discard_failed:
        _remove_managed_path(update_paths, update_paths.failed_server)
        _remove_managed_path(update_paths, update_paths.failed_profile)
    _write_metadata(
        update_paths,
        "rolled-back",
        active_build=old_build,
        failed_build=failed_build,
        active_mode=previous_mode,
        rollback_available=False,
    )


def recover_interrupted_promotion(update_paths: UpdatePaths) -> bool:
    """Recover a crash during a prior two-directory promotion, if possible."""
    metadata = _load_metadata(update_paths)
    if metadata.get("phase") not in {
        "promoting",
        "promoted",
        "switching-profile",
        "profile-switched",
        "rolling-back",
    }:
        return False

    rollback_kind = str(metadata.get("rollback_kind") or "server+profile")
    has_rollback = (
        update_paths.rollback_server.exists()
        if rollback_kind.startswith("server+")
        else (
            update_paths.rollback_profile.exists()
            or update_paths.parked_modded_profile.exists()
        )
    )
    if not has_rollback:
        previous_mode = str(metadata.get("previous_mode") or MODDED_MODE)
        _write_metadata(
            update_paths,
            "recovered",
            active_build=read_build_id(update_paths.server),
            active_mode=previous_mode,
            rollback_available=False,
        )
        return True
    previous_mode, _target_mode = _restore_rollback_paths(update_paths)
    for failed in (update_paths.failed_server, update_paths.failed_profile):
        _remove_managed_path(update_paths, failed)
    _write_metadata(
        update_paths,
        "recovered",
        active_build=read_build_id(update_paths.server),
        active_mode=previous_mode,
        rollback_available=False,
    )
    return True


def _wait_for_ready(
    adapter: ServiceAdapter,
    service_name: str,
    config_path: Path,
    *,
    timeout_seconds: float = PRODUCTION_TIMEOUT_SECONDS,
    stability_seconds: float = PRODUCTION_STABILITY_SECONDS,
    poll_interval_seconds: float = POLL_INTERVAL_SECONDS,
) -> tuple[bool, str]:
    started_at = time.monotonic()
    ready_since: float | None = None
    last_error = "A2S did not become ready"
    while time.monotonic() - started_at < timeout_seconds:
        status = adapter.get_service_status(service_name)
        if not bool(status.get("active")):
            ready_since = None
            last_error = (
                f"service is {status.get('active_state', 'unknown')}/"
                f"{status.get('sub_state', 'unknown')}"
            )
        else:
            player_status = _candidate_player_status(config_path)
            if player_status.available:
                now = time.monotonic()
                if ready_since is None:
                    ready_since = now
                if now - ready_since >= stability_seconds:
                    return True, "A2S readiness remained stable"
            else:
                ready_since = None
                last_error = player_status.error or "A2S did not respond"
        time.sleep(poll_interval_seconds)
    return False, redact_sensitive_text(last_error)


def _start_and_wait(
    adapter: ServiceAdapter,
    service_name: str,
    config_path: Path,
    readiness_checker: Callable[[ServiceAdapter, str, Path], tuple[bool, str]],
) -> tuple[bool, str]:
    start_result = adapter.start_service(service_name)
    if not start_result.success:
        return False, redact_sensitive_text(start_result.message)
    return readiness_checker(adapter, service_name, config_path)


def _restart_previous_after_rejection(
    adapter: ServiceAdapter,
    service_name: str,
    config_path: Path,
    readiness_checker: Callable[[ServiceAdapter, str, Path], tuple[bool, str]],
) -> str:
    ready, detail = _start_and_wait(
        adapter,
        service_name,
        config_path,
        readiness_checker,
    )
    return "previous build restarted" if ready else f"previous build restart failed: {detail}"


def get_compatibility_status(
    install_dir: Path,
    config_path: Path,
) -> CompatibilityStatus:
    """Return read-only compatibility and recovery state for an instance."""
    update_paths = resolve_update_paths(install_dir, config_path)
    metadata = _load_metadata(update_paths)
    rollback_kind = str(metadata.get("rollback_kind") or "")
    rollback_available = (
        update_paths.rollback_server.exists()
        or update_paths.rollback_profile.exists()
        or (
            rollback_kind.endswith("+parked-modded")
            and update_paths.parked_modded_profile.exists()
        )
    )
    return read_compatibility_status(
        update_paths.metadata,
        update_paths.parked_modded_profile,
        active_build=read_build_id(update_paths.server),
        rollback_available=rollback_available,
    )


def get_update_policy(install_dir: Path, config_path: Path) -> UpdatePolicy:
    update_paths = resolve_update_paths(install_dir, config_path)
    return read_policy(update_paths.policy)


def set_automatic_vanilla_fallback(
    install_dir: Path,
    config_path: Path,
    *,
    enabled: bool,
) -> UpdatePolicy:
    """Persist whether update-server may automatically activate vanilla."""
    update_paths = resolve_update_paths(install_dir, config_path)
    lock_descriptor = _acquire_update_lock(update_paths.instance_root)
    try:
        return write_policy(
            update_paths.policy,
            automatic_vanilla_fallback=bool(enabled),
        )
    finally:
        _release_update_lock(lock_descriptor)


def _active_profile_name(update_paths: UpdatePaths) -> str:
    metadata = _load_metadata(update_paths)
    try:
        return validate_profile_name(
            str(metadata.get("active_profile") or DEFAULT_ACTIVE_PROFILE)
        )
    except UpdateProfileError:
        return DEFAULT_ACTIVE_PROFILE


def get_named_profiles(install_dir: Path, config_path: Path) -> list[NamedProfile]:
    update_paths = resolve_update_paths(install_dir, config_path)
    return list_profiles(
        update_paths.profiles_root,
        update_paths.profile,
        active_name=_active_profile_name(update_paths),
    )


def get_parked_modded_profile(
    install_dir: Path,
    config_path: Path,
) -> NamedProfile | None:
    """Return the config-only modded fallback bundle, when one is parked."""
    update_paths = resolve_update_paths(install_dir, config_path)
    if not (update_paths.parked_modded_profile / "config.json").is_file():
        return None
    metadata = _load_metadata(update_paths)
    try:
        name = validate_profile_name(
            str(metadata.get("parked_profile_name") or DEFAULT_ACTIVE_PROFILE)
        )
        return inspect_profile(
            name,
            update_paths.parked_modded_profile,
            active=False,
        )
    except UpdateProfileError as exc:
        raise SafeUpdateError(str(exc)) from exc


def create_named_profile(
    install_dir: Path,
    config_path: Path,
    *,
    name: str,
    vanilla: bool = False,
) -> NamedProfile:
    """Create an inactive named profile from current settings or safe vanilla."""
    update_paths = resolve_update_paths(install_dir, config_path)
    lock_descriptor = _acquire_update_lock(update_paths.instance_root)
    try:
        safe_name = validate_profile_name(name)
        if safe_name == _active_profile_name(update_paths):
            raise SafeUpdateError(f"Profile is already active: {safe_name}")
        return create_profile(
            update_paths.profiles_root,
            update_paths.profile,
            name=safe_name,
            vanilla=vanilla,
        )
    except UpdateProfileError as exc:
        raise SafeUpdateError(str(exc)) from exc
    finally:
        _release_update_lock(lock_descriptor)


def rename_active_profile(
    install_dir: Path,
    config_path: Path,
    *,
    name: str,
) -> NamedProfile:
    """Assign an operator name to the active profile without copying its data."""
    update_paths = resolve_update_paths(install_dir, config_path)
    lock_descriptor = _acquire_update_lock(update_paths.instance_root)
    try:
        try:
            safe_name = validate_profile_name(name)
        except UpdateProfileError as exc:
            raise SafeUpdateError(str(exc)) from exc
        current_name = _active_profile_name(update_paths)
        if safe_name == current_name:
            return inspect_profile(safe_name, update_paths.profile, active=True)
        destination = profile_path(update_paths.profiles_root, safe_name)
        if destination.exists() or destination.is_symlink():
            raise SafeUpdateError(f"An inactive profile already uses the name {safe_name}.")
        status = get_compatibility_status(install_dir, config_path)
        if status.parked_modded_available:
            raise SafeUpdateError(
                "Cannot rename the active vanilla fallback while a modded profile is parked."
            )
        details = inspect_profile(safe_name, update_paths.profile, active=True)
        _write_metadata(
            update_paths,
            "profile-renamed",
            active_build=read_build_id(update_paths.server),
            active_mode=details.mode,
            active_profile=safe_name,
            previous_profile_name=current_name,
        )
        return details
    finally:
        _release_update_lock(lock_descriptor)


def _modded_source_profile(
    update_paths: UpdatePaths,
    status: CompatibilityStatus,
) -> Path:
    source = (
        update_paths.parked_modded_profile
        if status.active_mode == VANILLA_MODE
        else update_paths.profile
    )
    if not source.is_dir() or not (source / "config.json").is_file():
        raise SafeUpdateError(
            "The preserved modded profile is unavailable; refusing a destructive update."
        )
    return source


def _record_mod_canary(
    update_paths: UpdatePaths,
    profile_path: Path,
    *,
    build_id: str,
    profile_name: str,
    compatible: bool,
    reason: str = "",
) -> str:
    try:
        mod_compatibility.record_profile_canary(
            update_paths.mod_compatibility,
            profile_path,
            build_id=build_id,
            profile_name=profile_name,
            compatible=compatible,
            reason=reason,
            addons_path=update_paths.profile / "addons",
        )
    except Exception as exc:
        return "Could not persist per-mod compatibility evidence: " + redact_sensitive_text(exc)
    return ""


def _stream_safe_server_update(
    install_dir: Path,
    config_path: Path,
    service_name: str,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    update_stream: Callable[..., Iterable[str]] = installer.stream_server_update,
    canary_runner: Callable[[UpdatePaths], CanaryResult] = run_compatibility_canary,
    adapter: ServiceAdapter | None = None,
    readiness_checker: Callable[
        [ServiceAdapter, str, Path], tuple[bool, str]
    ] = _wait_for_ready,
) -> Iterator[str]:
    """Update with a modded canary and an automatic official-vanilla fallback."""
    adapter = adapter or get_service_adapter()
    update_paths = resolve_update_paths(install_dir, config_path)
    recovered = recover_interrupted_promotion(update_paths)
    if recovered:
        yield "Recovered an interrupted previous update before continuing."

    old_build = read_build_id(update_paths.server)
    status = get_compatibility_status(install_dir, config_path)
    previous_mode = status.active_mode
    active_profile_name = _active_profile_name(update_paths)
    modded_source = _modded_source_profile(update_paths, status)
    metadata = _load_metadata(update_paths)
    modded_profile_name = (
        str(metadata.get("parked_profile_name") or DEFAULT_ACTIVE_PROFILE)
        if previous_mode == VANILLA_MODE
        else active_profile_name
    )
    cleanup_candidate_artifacts(update_paths)

    try:
        baseline = create_update_baseline(
            update_paths.instance_root,
            update_paths.profile,
            parked_modded_profile=(
                update_paths.parked_modded_profile
                if status.parked_modded_available
                else None
            ),
            active_build=old_build,
        )
    except Exception as exc:
        raise SafeUpdateError(f"Could not create the pre-update baseline: {exc}") from exc
    yield f"Saved the pre-update configuration baseline at {baseline}."

    if retire_previous_rollback(update_paths):
        yield "Retired the previous rollback slot; the active generation remains unchanged."
    required, free = ensure_staging_capacity(update_paths)
    yield (
        "Reserved isolated update capacity "
        f"(required={required // 1024**2} MiB, free={free // 1024**2} MiB)."
    )
    _write_metadata(
        update_paths,
        "staging",
        old_build=old_build,
        active_build=old_build,
        active_mode=previous_mode,
        baseline_path=str(baseline),
        rollback_available=False,
        rollback_kind="",
    )

    promoted = False
    target_mode = MODDED_MODE
    disabled_mod_count = 0
    modded_failure = ""
    try:
        yield (
            "Preparing an isolated config bundle; the shared Workshop addon files "
            "remain untouched."
        )
        prepare_candidate(update_paths, source_profile=modded_source)
        integrity.mark_install_started(update_paths.candidate_server)
        yield "Downloading the candidate Steam build without changing the active package."
        yield from update_stream(update_paths.candidate_server, instance=instance)
        integrity.write_package_manifest(update_paths.candidate_server)
        integrity.clear_install_marker(update_paths.candidate_server)
        candidate_integrity = integrity.check_package_integrity(
            update_paths.candidate_server,
            verify_hashes=False,
        )
        if not candidate_integrity.complete:
            raise SafeUpdateError(
                "Candidate package integrity check failed: " + candidate_integrity.summary()
            )
        new_build = read_build_id(update_paths.candidate_server)
        if not new_build:
            raise SafeUpdateError("Candidate Steam build ID is unavailable.")

        _write_metadata(
            update_paths,
            "canary",
            old_build=old_build,
            new_build=new_build,
        )
        yield f"Starting isolated scenario/mod compatibility canary for build {new_build}."
        try:
            canary = canary_runner(update_paths)
        except CanaryRejectedError as exc:
            modded_failure = redact_sensitive_text(exc)[:500]
            record_warning = _record_mod_canary(
                update_paths,
                update_paths.candidate_profile,
                build_id=new_build,
                profile_name=modded_profile_name,
                compatible=False,
                reason=modded_failure,
            )
            if record_warning:
                yield record_warning
            policy = read_policy(update_paths.policy)
            if not policy.automatic_vanilla_fallback:
                raise CanaryRejectedError(
                    f"{modded_failure}; automatic vanilla fallback is disabled"
                ) from exc
            target_mode = VANILLA_MODE
            yield (
                "The full modded stack is incompatible with the new build; "
                "it will remain preserved and disabled as one dependency-safe unit."
            )
            disabled_mod_count = prepare_vanilla_candidate(
                update_paths,
                source_profile=update_paths.profile,
            )
            disabled_mod_count = max(
                disabled_mod_count,
                len(configured_mods(modded_source)),
            )
            _write_metadata(
                update_paths,
                "vanilla-canary",
                old_build=old_build,
                new_build=new_build,
                active_mode=previous_mode,
                target_mode=VANILLA_MODE,
                fallback_scenario=DEFAULT_VANILLA_SCENARIO,
                disabled_mod_count=disabled_mod_count,
                last_modded_failure=modded_failure,
            )
            yield (
                "Starting addon-free official Conflict Everon canary for "
                f"build {new_build}."
            )
            try:
                canary = canary_runner(update_paths)
            except CanaryRejectedError as vanilla_exc:
                record_warning = _record_mod_canary(
                    update_paths,
                    update_paths.candidate_profile,
                    build_id=new_build,
                    profile_name=DEFAULT_VANILLA_PROFILE,
                    compatible=False,
                    reason=redact_sensitive_text(vanilla_exc),
                )
                if record_warning:
                    yield record_warning
                raise
        if target_mode == MODDED_MODE:
            record_warning = _record_mod_canary(
                update_paths,
                update_paths.candidate_profile,
                build_id=new_build,
                profile_name=modded_profile_name,
                compatible=True,
            )
            if record_warning:
                yield record_warning
        else:
            record_warning = _record_mod_canary(
                update_paths,
                update_paths.candidate_profile,
                build_id=new_build,
                profile_name=DEFAULT_VANILLA_PROFILE,
                compatible=True,
            )
            if record_warning:
                yield record_warning
        yield (
            f"{target_mode.capitalize()} compatibility canary reached stable A2S "
            f"readiness after {canary.ready_seconds:.1f} seconds."
        )
        reset_candidate_profile_after_canary(
            update_paths,
            source_profile=(
                modded_source if target_mode == MODDED_MODE else update_paths.profile
            ),
            mode=target_mode,
        )

        if target_mode == VANILLA_MODE:
            yield (
                "Promoting the new build in vanilla compatibility mode and parking "
                "the modded config bundle."
            )
        else:
            yield "Promoting the verified modded build and retaining one rollback slot."
        promote_candidate(
            update_paths,
            old_build=old_build,
            new_build=new_build,
            previous_mode=previous_mode,
            target_mode=target_mode,
            previous_profile=active_profile_name,
            target_profile=(
                DEFAULT_VANILLA_PROFILE
                if target_mode == VANILLA_MODE
                else modded_profile_name
            ),
        )
        promoted = True
        yield "Starting the promoted build with the production service."
        ready, detail = _start_and_wait(
            adapter,
            service_name,
            update_paths.config_file,
            readiness_checker,
        )
        if not ready:
            raise SafeUpdateError(f"Promoted build failed production readiness: {detail}")

        if previous_mode == VANILLA_MODE and target_mode == MODDED_MODE:
            _remove_managed_path(update_paths, update_paths.parked_modded_profile)
        _remove_managed_path(update_paths, update_paths.candidate_logs)
        _write_metadata(
            update_paths,
            "committed",
            old_build=old_build,
            active_build=new_build,
            active_mode=target_mode,
            previous_mode=previous_mode,
            target_mode=target_mode,
            rollback_available=True,
            fallback_scenario=DEFAULT_VANILLA_SCENARIO,
            disabled_mod_count=(disabled_mod_count if target_mode == VANILLA_MODE else 0),
            last_modded_failure=modded_failure,
            active_profile=(
                DEFAULT_VANILLA_PROFILE
                if target_mode == VANILLA_MODE
                else modded_profile_name
            ),
            parked_profile_name=(
                active_profile_name
                if target_mode == VANILLA_MODE
                else ""
            ),
        )
        if target_mode == VANILLA_MODE:
            yield (
                f"Build {new_build} is active and stable in vanilla mode; "
                f"{disabled_mod_count} configured mods and the custom scenario remain "
                "disabled in the parked config bundle; shared Workshop files remain "
                "untouched."
            )
        else:
            yield (
                f"Build {new_build} is active and stable with the complete modded stack; "
                f"build {old_build or 'unknown'} is retained for rollback."
            )
    except Exception as exc:
        reason = redact_sensitive_text(exc) or "update failed"
        if promoted:
            adapter.stop_service(service_name)
            failed_build = read_build_id(update_paths.server)
            rollback_promoted_candidate(
                update_paths,
                old_build=old_build,
                failed_build=failed_build,
            )
            recovery = _restart_previous_after_rejection(
                adapter,
                service_name,
                update_paths.config_file,
                readiness_checker,
            )
            raise SafeUpdateError(
                f"Update rejected and rolled back ({reason}); {recovery}."
            ) from exc

        cleanup_candidate_artifacts(update_paths)
        recovery = _restart_previous_after_rejection(
            adapter,
            service_name,
            update_paths.config_file,
            readiness_checker,
        )
        _write_metadata(
            update_paths,
            "rejected",
            active_build=old_build,
            active_mode=previous_mode,
            reason=reason[:500],
            last_modded_failure=modded_failure,
            rollback_available=False,
        )
        raise SafeUpdateError(
            f"Candidate update rejected before promotion ({reason}); {recovery}."
        ) from exc


def stream_safe_server_update(
    install_dir: Path,
    config_path: Path,
    service_name: str,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    update_stream: Callable[..., Iterable[str]] = installer.stream_server_update,
    canary_runner: Callable[[UpdatePaths], CanaryResult] = run_compatibility_canary,
    adapter: ServiceAdapter | None = None,
    readiness_checker: Callable[
        [ServiceAdapter, str, Path], tuple[bool, str]
    ] = _wait_for_ready,
) -> Iterator[str]:
    """Serialize and run one transactional update for an instance."""
    update_paths = resolve_update_paths(install_dir, config_path)
    lock_descriptor = _acquire_update_lock(update_paths.instance_root)
    try:
        yield from _stream_safe_server_update(
            install_dir,
            config_path,
            service_name,
            instance=instance,
            update_stream=update_stream,
            canary_runner=canary_runner,
            adapter=adapter,
            readiness_checker=readiness_checker,
        )
    finally:
        _release_update_lock(lock_descriptor)


def _run_current_build_canary(update_paths: UpdatePaths) -> CanaryResult:
    return run_compatibility_canary(update_paths, server_dir=update_paths.server)


def _verify_profile_source(
    update_paths: UpdatePaths,
    *,
    source_profile: Path,
    profile_name: str,
    current_canary_runner: Callable[[UpdatePaths], CanaryResult],
) -> Iterator[str]:
    """Run a current-build canary without activating or parking any profile."""
    cleanup_candidate_artifacts(update_paths)
    active_build = read_build_id(update_paths.server)
    try:
        prepare_candidate(update_paths, source_profile=source_profile)
    except Exception as exc:
        cleanup_candidate_artifacts(update_paths)
        raise SafeUpdateError(
            f"Could not prepare profile {profile_name} for testing: {exc}"
        ) from exc
    yield (
        f"Testing profile {profile_name} against active build "
        f"{active_build or 'unknown'} without activating it."
    )
    try:
        canary = current_canary_runner(update_paths)
    except CanaryRejectedError as exc:
        reason = redact_sensitive_text(exc)
        record_warning = _record_mod_canary(
            update_paths,
            update_paths.candidate_profile,
            build_id=active_build,
            profile_name=profile_name,
            compatible=False,
            reason=reason,
        )
        if record_warning:
            yield record_warning
        raise SafeUpdateError(
            f"Profile {profile_name} is incompatible with active build "
            f"{active_build or 'unknown'} ({reason}). The active profile was not changed."
        ) from exc
    finally:
        cleanup_candidate_artifacts(update_paths)

    record_warning = _record_mod_canary(
        update_paths,
        source_profile,
        build_id=active_build,
        profile_name=profile_name,
        compatible=True,
    )
    if record_warning:
        yield record_warning
    yield (
        f"Profile {profile_name} is compatible with active build "
        f"{active_build or 'unknown'}; it was not activated."
    )
    yield f"Canary was stable after {canary.ready_seconds:.1f} seconds."


def verify_named_profile(
    install_dir: Path,
    config_path: Path,
    *,
    name: str,
    current_canary_runner: Callable[
        [UpdatePaths], CanaryResult
    ] = _run_current_build_canary,
) -> Iterator[str]:
    """Serialize a test-only canary for one active or stored named profile."""
    update_paths = resolve_update_paths(install_dir, config_path)
    lock_descriptor = _acquire_update_lock(update_paths.instance_root)
    try:
        try:
            profile_name = validate_profile_name(name)
        except UpdateProfileError as exc:
            raise SafeUpdateError(str(exc)) from exc
        active_name = _active_profile_name(update_paths)
        source_profile = (
            update_paths.profile
            if profile_name == active_name
            else profile_path(update_paths.profiles_root, profile_name)
        )
        if not source_profile.is_dir() or source_profile.is_symlink():
            raise SafeUpdateError(f"Stored profile does not exist: {profile_name}")
        try:
            inspect_profile(
                profile_name,
                source_profile,
                active=profile_name == active_name,
            )
        except UpdateProfileError as exc:
            raise SafeUpdateError(str(exc)) from exc
        yield from _verify_profile_source(
            update_paths,
            source_profile=source_profile,
            profile_name=profile_name,
            current_canary_runner=current_canary_runner,
        )
    finally:
        _release_update_lock(lock_descriptor)


def verify_parked_modded_profile(
    install_dir: Path,
    config_path: Path,
    *,
    current_canary_runner: Callable[
        [UpdatePaths], CanaryResult
    ] = _run_current_build_canary,
) -> Iterator[str]:
    """Serialize a test-only canary for the fallback-parked modded profile."""
    update_paths = resolve_update_paths(install_dir, config_path)
    lock_descriptor = _acquire_update_lock(update_paths.instance_root)
    try:
        parked = get_parked_modded_profile(install_dir, config_path)
        if parked is None:
            raise SafeUpdateError("No parked modded profile is available for testing.")
        yield from _verify_profile_source(
            update_paths,
            source_profile=update_paths.parked_modded_profile,
            profile_name=parked.name,
            current_canary_runner=current_canary_runner,
        )
    finally:
        _release_update_lock(lock_descriptor)


def _promote_profile_candidate(
    update_paths: UpdatePaths,
    *,
    previous_mode: str,
    target_mode: str,
) -> None:
    if not (update_paths.candidate_profile / "config.json").is_file():
        raise SafeUpdateError("Candidate server profile is incomplete.")
    parks_modded = previous_mode == MODDED_MODE and target_mode == VANILLA_MODE
    if parks_modded and update_paths.parked_modded_profile.exists():
        raise SafeUpdateError(
            "A parked modded profile already exists; refusing to overwrite it."
        )
    _remove_managed_path(update_paths, update_paths.rollback_profile)
    rollback_kind = "profile+parked-modded" if parks_modded else "profile+profile"
    _write_metadata(
        update_paths,
        "switching-profile",
        previous_mode=previous_mode,
        target_mode=target_mode,
        rollback_kind=rollback_kind,
    )
    try:
        if parks_modded:
            _copy_profile_bundle(
                update_paths,
                update_paths.profile,
                update_paths.parked_modded_profile,
            )
        else:
            _copy_profile_bundle(
                update_paths,
                update_paths.profile,
                update_paths.rollback_profile,
            )
        _activate_profile_bundle(update_paths, update_paths.candidate_profile)
    except Exception:
        recover_interrupted_promotion(update_paths)
        raise
    _write_metadata(
        update_paths,
        "profile-switched",
        active_mode=target_mode,
        previous_mode=previous_mode,
        target_mode=target_mode,
        rollback_kind=rollback_kind,
    )


def _restore_failed_switch(
    update_paths: UpdatePaths,
    *,
    adapter: ServiceAdapter,
    service_name: str,
    readiness_checker: Callable[[ServiceAdapter, str, Path], tuple[bool, str]],
) -> str:
    adapter.stop_service(service_name)
    previous_mode, _target_mode = _restore_rollback_paths(update_paths)
    for failed in (update_paths.failed_server, update_paths.failed_profile):
        _remove_managed_path(update_paths, failed)
    ready, detail = _start_and_wait(
        adapter,
        service_name,
        update_paths.config_file,
        readiness_checker,
    )
    _write_metadata(
        update_paths,
        "profile-switch-rolled-back",
        active_build=read_build_id(update_paths.server),
        active_mode=previous_mode,
        rollback_available=False,
    )
    return "previous profile restarted" if ready else f"previous profile failed: {detail}"


def _activate_vanilla(
    install_dir: Path,
    config_path: Path,
    service_name: str,
    *,
    current_canary_runner: Callable[[UpdatePaths], CanaryResult] = _run_current_build_canary,
    adapter: ServiceAdapter | None = None,
    readiness_checker: Callable[
        [ServiceAdapter, str, Path], tuple[bool, str]
    ] = _wait_for_ready,
) -> Iterator[str]:
    """Canary and activate vanilla on the already-installed server build."""
    adapter = adapter or get_service_adapter()
    update_paths = resolve_update_paths(install_dir, config_path)
    if recover_interrupted_promotion(update_paths):
        yield "Recovered an interrupted profile switch before continuing."
    status = get_compatibility_status(install_dir, config_path)
    if status.active_mode == VANILLA_MODE:
        yield (
            "Vanilla compatibility mode is already active; the modded config bundle "
            f"remains at {update_paths.parked_modded_profile}."
        )
        return

    cleanup_candidate_artifacts(update_paths)
    active_build = read_build_id(update_paths.server)
    previous_profile_name = _active_profile_name(update_paths)
    try:
        baseline = create_update_baseline(
            update_paths.instance_root,
            update_paths.profile,
            active_build=active_build,
        )
        disabled_count = prepare_vanilla_candidate(
            update_paths,
            source_profile=update_paths.profile,
        )
    except Exception as exc:
        cleanup_candidate_artifacts(update_paths)
        raise SafeUpdateError(f"Could not prepare vanilla compatibility mode: {exc}") from exc
    yield f"Saved the pre-switch configuration baseline at {baseline}."
    yield (
        "Testing the installed build with official Conflict Everon and no Workshop mods."
    )
    try:
        canary = current_canary_runner(update_paths)
        yield f"Vanilla canary was stable after {canary.ready_seconds:.1f} seconds."
        record_warning = _record_mod_canary(
            update_paths,
            update_paths.candidate_profile,
            build_id=active_build,
            profile_name=DEFAULT_VANILLA_PROFILE,
            compatible=True,
        )
        if record_warning:
            yield record_warning
        reset_candidate_profile_after_canary(
            update_paths,
            source_profile=update_paths.profile,
            mode=VANILLA_MODE,
        )
        _write_metadata(
            update_paths,
            "vanilla-canary-passed",
            active_build=active_build,
            active_mode=MODDED_MODE,
            baseline_path=str(baseline),
            fallback_scenario=DEFAULT_VANILLA_SCENARIO,
            disabled_mod_count=disabled_count,
            active_profile=DEFAULT_VANILLA_PROFILE,
            parked_profile_name=previous_profile_name,
            previous_profile=previous_profile_name,
            target_profile=DEFAULT_VANILLA_PROFILE,
        )
        _promote_profile_candidate(
            update_paths,
            previous_mode=MODDED_MODE,
            target_mode=VANILLA_MODE,
        )
        ready, detail = _start_and_wait(
            adapter,
            service_name,
            update_paths.config_file,
            readiness_checker,
        )
        if not ready:
            recovery = _restore_failed_switch(
                update_paths,
                adapter=adapter,
                service_name=service_name,
                readiness_checker=readiness_checker,
            )
            raise SafeUpdateError(
                f"Vanilla production start failed ({detail}); {recovery}."
            )
        cleanup_candidate_artifacts(update_paths)
        _write_metadata(
            update_paths,
            "vanilla-active",
            active_build=active_build,
            active_mode=VANILLA_MODE,
            previous_mode=MODDED_MODE,
            target_mode=VANILLA_MODE,
            rollback_kind="profile+parked-modded",
            rollback_available=True,
            baseline_path=str(baseline),
            fallback_scenario=DEFAULT_VANILLA_SCENARIO,
            disabled_mod_count=disabled_count,
        )
        yield (
            f"Vanilla mode is active. {disabled_count} configured mods and the custom "
            "scenario selection are parked as config only; Workshop files remain "
            "untouched in config/addons."
        )
    except CanaryRejectedError as exc:
        record_warning = _record_mod_canary(
            update_paths,
            update_paths.candidate_profile,
            build_id=active_build,
            profile_name=DEFAULT_VANILLA_PROFILE,
            compatible=False,
            reason=redact_sensitive_text(exc),
        )
        if record_warning:
            yield record_warning
        cleanup_candidate_artifacts(update_paths)
        _write_metadata(
            update_paths,
            "vanilla-rejected",
            active_build=active_build,
            active_mode=MODDED_MODE,
            baseline_path=str(baseline),
            reason=redact_sensitive_text(exc)[:500],
        )
        raise SafeUpdateError(
            "The installed build also failed the official vanilla canary; "
            "the modded profile was not changed."
        ) from exc


def activate_vanilla(
    install_dir: Path,
    config_path: Path,
    service_name: str,
    *,
    current_canary_runner: Callable[[UpdatePaths], CanaryResult] = _run_current_build_canary,
    adapter: ServiceAdapter | None = None,
    readiness_checker: Callable[
        [ServiceAdapter, str, Path], tuple[bool, str]
    ] = _wait_for_ready,
) -> Iterator[str]:
    """Serialize an explicit switch to vanilla compatibility mode."""
    update_paths = resolve_update_paths(install_dir, config_path)
    lock_descriptor = _acquire_update_lock(update_paths.instance_root)
    try:
        yield from _activate_vanilla(
            install_dir,
            config_path,
            service_name,
            current_canary_runner=current_canary_runner,
            adapter=adapter,
            readiness_checker=readiness_checker,
        )
    finally:
        _release_update_lock(lock_descriptor)


def _retry_modded(
    install_dir: Path,
    config_path: Path,
    service_name: str,
    *,
    current_canary_runner: Callable[[UpdatePaths], CanaryResult] = _run_current_build_canary,
    adapter: ServiceAdapter | None = None,
    readiness_checker: Callable[
        [ServiceAdapter, str, Path], tuple[bool, str]
    ] = _wait_for_ready,
) -> Iterator[str]:
    """Retest and reactivate the parked modded config against shared addons."""
    adapter = adapter or get_service_adapter()
    update_paths = resolve_update_paths(install_dir, config_path)
    if recover_interrupted_promotion(update_paths):
        yield "Recovered an interrupted profile switch before continuing."
    status = get_compatibility_status(install_dir, config_path)
    if status.active_mode != VANILLA_MODE or not status.parked_modded_available:
        raise SafeUpdateError("No parked modded profile is available for retry.")

    cleanup_candidate_artifacts(update_paths)
    active_build = read_build_id(update_paths.server)
    metadata = _load_metadata(update_paths)
    restored_profile_name = str(
        metadata.get("parked_profile_name") or DEFAULT_ACTIVE_PROFILE
    )
    try:
        restored_profile_name = validate_profile_name(restored_profile_name)
    except UpdateProfileError:
        restored_profile_name = DEFAULT_ACTIVE_PROFILE
    try:
        baseline = create_update_baseline(
            update_paths.instance_root,
            update_paths.profile,
            parked_modded_profile=update_paths.parked_modded_profile,
            active_build=active_build,
        )
        prepare_candidate(
            update_paths,
            source_profile=update_paths.parked_modded_profile,
        )
    except Exception as exc:
        cleanup_candidate_artifacts(update_paths)
        raise SafeUpdateError(f"Could not prepare the modded retry: {exc}") from exc
    yield f"Saved the pre-retry configuration baseline at {baseline}."
    yield "Testing the parked scenario/mod selection against the shared addon pool."
    try:
        canary = current_canary_runner(update_paths)
    except CanaryRejectedError as exc:
        reason = redact_sensitive_text(exc)[:500]
        record_warning = _record_mod_canary(
            update_paths,
            update_paths.candidate_profile,
            build_id=active_build,
            profile_name=restored_profile_name,
            compatible=False,
            reason=reason,
        )
        if record_warning:
            yield record_warning
        cleanup_candidate_artifacts(update_paths)
        recovery = _restart_previous_after_rejection(
            adapter,
            service_name,
            update_paths.config_file,
            readiness_checker,
        )
        _write_metadata(
            update_paths,
            "modded-retry-rejected",
            active_build=active_build,
            active_mode=VANILLA_MODE,
            baseline_path=str(baseline),
            last_modded_failure=reason,
        )
        raise SafeUpdateError(
            f"The modded stack is still incompatible ({reason}); {recovery}."
        ) from exc

    yield f"Modded canary was stable after {canary.ready_seconds:.1f} seconds."
    record_warning = _record_mod_canary(
        update_paths,
        update_paths.candidate_profile,
        build_id=active_build,
        profile_name=restored_profile_name,
        compatible=True,
    )
    if record_warning:
        yield record_warning
    reset_candidate_profile_after_canary(
        update_paths,
        source_profile=update_paths.parked_modded_profile,
        mode=MODDED_MODE,
    )
    _promote_profile_candidate(
        update_paths,
        previous_mode=VANILLA_MODE,
        target_mode=MODDED_MODE,
    )
    ready, detail = _start_and_wait(
        adapter,
        service_name,
        update_paths.config_file,
        readiness_checker,
    )
    if not ready:
        recovery = _restore_failed_switch(
            update_paths,
            adapter=adapter,
            service_name=service_name,
            readiness_checker=readiness_checker,
        )
        raise SafeUpdateError(f"Modded production start failed ({detail}); {recovery}.")

    _remove_managed_path(update_paths, update_paths.parked_modded_profile)
    cleanup_candidate_artifacts(update_paths)
    _write_metadata(
        update_paths,
        "modded-active",
        active_build=active_build,
        active_mode=MODDED_MODE,
        previous_mode=VANILLA_MODE,
        target_mode=MODDED_MODE,
        rollback_kind="profile+profile",
        rollback_available=True,
        baseline_path=str(baseline),
        disabled_mod_count=0,
        last_modded_failure="",
        active_profile=restored_profile_name,
        parked_profile_name="",
        previous_profile=DEFAULT_VANILLA_PROFILE,
        target_profile=restored_profile_name,
    )
    yield "The modded scenario and shared addons are active and stable again."


def retry_modded(
    install_dir: Path,
    config_path: Path,
    service_name: str,
    *,
    current_canary_runner: Callable[[UpdatePaths], CanaryResult] = _run_current_build_canary,
    adapter: ServiceAdapter | None = None,
    readiness_checker: Callable[
        [ServiceAdapter, str, Path], tuple[bool, str]
    ] = _wait_for_ready,
) -> Iterator[str]:
    """Serialize an explicit retry of the preserved modded stack."""
    update_paths = resolve_update_paths(install_dir, config_path)
    lock_descriptor = _acquire_update_lock(update_paths.instance_root)
    try:
        yield from _retry_modded(
            install_dir,
            config_path,
            service_name,
            current_canary_runner=current_canary_runner,
            adapter=adapter,
            readiness_checker=readiness_checker,
        )
    finally:
        _release_update_lock(lock_descriptor)


def _recover_interrupted_named_switch(update_paths: UpdatePaths) -> bool:
    metadata = _load_metadata(update_paths)
    if metadata.get("phase") not in {"named-switching", "named-switched"}:
        return False
    try:
        previous_name = validate_profile_name(str(metadata.get("previous_profile") or ""))
        validate_profile_name(str(metadata.get("target_profile") or ""))
    except UpdateProfileError as exc:
        raise SafeUpdateError(
            "Named profile switch metadata is invalid; manual recovery is required."
        ) from exc
    previous_store = profile_path(update_paths.profiles_root, previous_name)

    if previous_store.exists():
        _activate_profile_bundle(update_paths, previous_store)
        for child in previous_store.iterdir():
            child.unlink()
        previous_store.rmdir()
    mode = inspect_profile(previous_name, update_paths.profile, active=True).mode
    _write_metadata(
        update_paths,
        "named-switch-recovered",
        active_build=read_build_id(update_paths.server),
        active_mode=mode,
        active_profile=previous_name,
        rollback_available=False,
    )
    return True


def _switch_named_profile(
    install_dir: Path,
    config_path: Path,
    service_name: str,
    *,
    name: str,
    current_canary_runner: Callable[[UpdatePaths], CanaryResult] = _run_current_build_canary,
    adapter: ServiceAdapter | None = None,
    readiness_checker: Callable[
        [ServiceAdapter, str, Path], tuple[bool, str]
    ] = _wait_for_ready,
) -> Iterator[str]:
    """Canary and atomically activate one stored named profile."""
    adapter = adapter or get_service_adapter()
    update_paths = resolve_update_paths(install_dir, config_path)
    if recover_interrupted_promotion(update_paths):
        yield "Recovered an interrupted update transaction before continuing."
    if _recover_interrupted_named_switch(update_paths):
        yield "Recovered an interrupted named-profile switch before continuing."

    try:
        target_name = validate_profile_name(name)
    except UpdateProfileError as exc:
        raise SafeUpdateError(str(exc)) from exc
    previous_name = _active_profile_name(update_paths)
    if target_name == previous_name:
        yield f"Profile {target_name} is already active."
        return
    status = get_compatibility_status(install_dir, config_path)
    if status.parked_modded_available:
        raise SafeUpdateError(
            "A vanilla fallback transaction has a parked modded profile. Run "
            "update retry-modded or update rollback before switching named profiles."
        )
    target_store = profile_path(update_paths.profiles_root, target_name)
    previous_store = profile_path(update_paths.profiles_root, previous_name)
    if not target_store.is_dir() or target_store.is_symlink():
        raise SafeUpdateError(f"Stored profile does not exist: {target_name}")
    if previous_store.exists() or previous_store.is_symlink():
        raise SafeUpdateError(
            f"Inactive store already contains the active profile name {previous_name}; "
            "refusing to overwrite it."
        )
    try:
        target_details = inspect_profile(target_name, target_store, active=False)
    except UpdateProfileError as exc:
        raise SafeUpdateError(str(exc)) from exc

    cleanup_candidate_artifacts(update_paths)
    active_build = read_build_id(update_paths.server)
    try:
        baseline = create_update_baseline(
            update_paths.instance_root,
            update_paths.profile,
            parked_modded_profile=(
                update_paths.parked_modded_profile
                if status.parked_modded_available
                else None
            ),
            active_build=active_build,
        )
        prepare_candidate(update_paths, source_profile=target_store)
    except Exception as exc:
        cleanup_candidate_artifacts(update_paths)
        raise SafeUpdateError(f"Could not prepare profile {target_name}: {exc}") from exc
    yield f"Saved the pre-switch configuration baseline at {baseline}."
    yield f"Testing profile {target_name} against active build {active_build or 'unknown'}."
    try:
        canary = current_canary_runner(update_paths)
    except CanaryRejectedError as exc:
        reason = redact_sensitive_text(exc)
        record_warning = _record_mod_canary(
            update_paths,
            update_paths.candidate_profile,
            build_id=active_build,
            profile_name=target_name,
            compatible=False,
            reason=reason,
        )
        if record_warning:
            yield record_warning
        cleanup_candidate_artifacts(update_paths)
        recovery = _restart_previous_after_rejection(
            adapter,
            service_name,
            update_paths.config_file,
            readiness_checker,
        )
        raise SafeUpdateError(
            f"Profile {target_name} failed its compatibility canary "
            f"({reason}); {recovery}."
        ) from exc
    yield f"Profile canary was stable after {canary.ready_seconds:.1f} seconds."
    record_warning = _record_mod_canary(
        update_paths,
        update_paths.candidate_profile,
        build_id=active_build,
        profile_name=target_name,
        compatible=True,
    )
    if record_warning:
        yield record_warning
    reset_candidate_profile_after_canary(
        update_paths,
        source_profile=target_store,
        mode=target_details.mode,
    )

    _write_metadata(
        update_paths,
        "named-switching",
        active_build=active_build,
        active_mode=inspect_profile(previous_name, update_paths.profile, active=True).mode,
        active_profile=previous_name,
        previous_profile=previous_name,
        target_profile=target_name,
        baseline_path=str(baseline),
    )
    try:
        _copy_profile_bundle(update_paths, update_paths.profile, previous_store)
        _activate_profile_bundle(update_paths, update_paths.candidate_profile)
    except Exception:
        _recover_interrupted_named_switch(update_paths)
        raise
    _write_metadata(
        update_paths,
        "named-switched",
        active_build=active_build,
        active_mode=target_details.mode,
        active_profile=target_name,
        previous_profile=previous_name,
        target_profile=target_name,
        baseline_path=str(baseline),
    )

    ready, detail = _start_and_wait(
        adapter,
        service_name,
        update_paths.config_file,
        readiness_checker,
    )
    if not ready:
        adapter.stop_service(service_name)
        _recover_interrupted_named_switch(update_paths)
        recovery = _restart_previous_after_rejection(
            adapter,
            service_name,
            update_paths.config_file,
            readiness_checker,
        )
        raise SafeUpdateError(
            f"Profile {target_name} failed production readiness ({detail}); {recovery}."
        )

    for child in target_store.iterdir():
        child.unlink()
    target_store.rmdir()
    cleanup_candidate_artifacts(update_paths)
    _write_metadata(
        update_paths,
        "named-active",
        active_build=active_build,
        active_mode=target_details.mode,
        active_profile=target_name,
        previous_profile=previous_name,
        target_profile=target_name,
        baseline_path=str(baseline),
        rollback_available=False,
    )
    yield (
        f"Profile {target_name} is active and stable; prior profile {previous_name} "
        f"is stored at {previous_store}."
    )


def switch_named_profile(
    install_dir: Path,
    config_path: Path,
    service_name: str,
    *,
    name: str,
    current_canary_runner: Callable[[UpdatePaths], CanaryResult] = _run_current_build_canary,
    adapter: ServiceAdapter | None = None,
    readiness_checker: Callable[
        [ServiceAdapter, str, Path], tuple[bool, str]
    ] = _wait_for_ready,
) -> Iterator[str]:
    """Serialize a manual named-profile canary and switch."""
    update_paths = resolve_update_paths(install_dir, config_path)
    lock_descriptor = _acquire_update_lock(update_paths.instance_root)
    try:
        yield from _switch_named_profile(
            install_dir,
            config_path,
            service_name,
            name=name,
            current_canary_runner=current_canary_runner,
            adapter=adapter,
            readiness_checker=readiness_checker,
        )
    finally:
        _release_update_lock(lock_descriptor)


def _rollback_last_update(
    install_dir: Path,
    config_path: Path,
    service_name: str,
    *,
    adapter: ServiceAdapter | None = None,
    readiness_checker: Callable[
        [ServiceAdapter, str, Path], tuple[bool, str]
    ] = _wait_for_ready,
) -> Iterator[str]:
    """Manually restore the retained rollback generation and verify its start."""
    adapter = adapter or get_service_adapter()
    update_paths = resolve_update_paths(install_dir, config_path)
    metadata = _load_metadata(update_paths)
    rollback_kind = str(metadata.get("rollback_kind") or "server+profile")
    needs_server = rollback_kind.startswith("server+")
    needs_parked = rollback_kind.endswith("+parked-modded")
    server_available = update_paths.rollback_server.exists()
    profile_available = (
        update_paths.parked_modded_profile.exists()
        if needs_parked
        else update_paths.rollback_profile.exists()
    )
    if (needs_server and not server_available) or not profile_available:
        raise SafeUpdateError("No complete retained rollback generation is available.")

    stop_result = adapter.stop_service(service_name)
    if not stop_result.success:
        raise SafeUpdateError(
            "Could not stop the active server before rollback: "
            + redact_sensitive_text(stop_result.message)
        )
    current_build = read_build_id(update_paths.server)
    rollback_build = (
        read_build_id(update_paths.rollback_server) if needs_server else current_build
    )
    previous_mode = str(metadata.get("previous_mode") or MODDED_MODE)
    target_mode = str(metadata.get("target_mode") or MODDED_MODE)
    previous_profile_name = str(metadata.get("previous_profile") or DEFAULT_ACTIVE_PROFILE)
    target_profile_name = str(metadata.get("target_profile") or DEFAULT_ACTIVE_PROFILE)
    yield (
        f"Restoring retained {previous_mode} generation on build "
        f"{rollback_build or 'unknown'}."
    )
    rollback_promoted_candidate(
        update_paths,
        old_build=rollback_build,
        failed_build=current_build,
        discard_failed=False,
    )
    ready, detail = _start_and_wait(
        adapter,
        service_name,
        update_paths.config_file,
        readiness_checker,
    )
    if not ready:
        adapter.stop_service(service_name)
        if needs_server and update_paths.failed_server.exists():
            update_paths.server.replace(update_paths.rollback_server)
            update_paths.failed_server.replace(update_paths.server)
        rollback_destination = (
            update_paths.parked_modded_profile
            if needs_parked
            else update_paths.rollback_profile
        )
        _remove_managed_path(update_paths, rollback_destination)
        _copy_profile_bundle(update_paths, update_paths.profile, rollback_destination)
        _activate_profile_bundle(update_paths, update_paths.failed_profile)
        _remove_managed_path(update_paths, update_paths.failed_profile)
        current_ready, current_detail = _start_and_wait(
            adapter,
            service_name,
            update_paths.config_file,
            readiness_checker,
        )
        _write_metadata(
            update_paths,
            "manual-rollback-rejected",
            active_build=current_build,
            active_mode=target_mode,
            active_profile=target_profile_name,
            parked_profile_name=(
                previous_profile_name
                if target_mode == VANILLA_MODE and previous_mode == MODDED_MODE
                else ""
            ),
            rollback_build=rollback_build,
        )
        recovery = (
            "the prior active generation was restored"
            if current_ready
            else f"the prior active generation also failed readiness: {current_detail}"
        )
        raise SafeUpdateError(
            f"Retained rollback did not become ready ({detail}); {recovery}."
        )
    _remove_managed_path(update_paths, update_paths.failed_server)
    _remove_managed_path(update_paths, update_paths.failed_profile)
    _write_metadata(
        update_paths,
        "manual-rollback-committed",
        active_build=rollback_build,
        active_mode=previous_mode,
        active_profile=previous_profile_name,
        parked_profile_name=(
            target_profile_name
            if previous_mode == VANILLA_MODE and target_mode == MODDED_MODE
            else ""
        ),
        replaced_build=current_build,
        rollback_available=False,
    )
    yield (
        f"Rollback build {rollback_build or 'unknown'} is active and stable in "
        f"{previous_mode} mode."
    )


def rollback_last_update(
    install_dir: Path,
    config_path: Path,
    service_name: str,
    *,
    adapter: ServiceAdapter | None = None,
    readiness_checker: Callable[
        [ServiceAdapter, str, Path], tuple[bool, str]
    ] = _wait_for_ready,
) -> Iterator[str]:
    """Serialize and run an explicit rollback for an instance."""
    update_paths = resolve_update_paths(install_dir, config_path)
    lock_descriptor = _acquire_update_lock(update_paths.instance_root)
    try:
        yield from _rollback_last_update(
            install_dir,
            config_path,
            service_name,
            adapter=adapter,
            readiness_checker=readiness_checker,
        )
    finally:
        _release_update_lock(lock_descriptor)
