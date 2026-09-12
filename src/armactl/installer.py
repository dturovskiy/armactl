"""Installer module for armactl.

Handles OS verification, steamcmd installation, server download,
and initial configuration generation.
"""

from __future__ import annotations

import os
import queue
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Iterator
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from armactl import paths
from armactl.bot_config import ensure_bot_config
from armactl.discovery import discover
from armactl.i18n import _, tr
from armactl.integrity import (
    APP_ID,
    IntegrityError,
    check_package_integrity,
    clear_install_marker,
    mark_install_started,
    write_package_manifest,
)
from armactl.redaction import redact_sensitive_text, safe_subprocess_error
from armactl.server_config_schema import (
    DEFAULT_CONFIG_TEMPLATE_NAME,
    generated_default_template_context,
)
from armactl.service_manager import (
    enable_service,
    generate_services,
    install_privileged_systemctl_channel,
    restart_service,
)


class InstallError(Exception):
    """Raised when installation fails at any step."""


STEAMCMD_MAX_ATTEMPTS = 3
STEAMCMD_RETRY_DELAYS_SECONDS = (10.0, 30.0)
STEAMCMD_APP_INFO_TIMEOUT_SECONDS = 90.0
STEAMCMD_UPDATE_IDLE_TIMEOUT_SECONDS = 5 * 60.0
MAX_STEAM_APP_INFO_OUTPUT_CHARS = 2_000_000
STEAMCMD_PERMANENT_ERROR_MARKERS = (
    "No subscription",
    "Invalid platform",
    "Account login denied",
)


def _steamcmd_error_is_permanent(error: InstallError) -> bool:
    """Return True when retrying the SteamCMD failure is unlikely to help."""
    message = str(error)
    return any(marker in message for marker in STEAMCMD_PERMANENT_ERROR_MARKERS)


def _steamcmd_retry_delay(
    attempt: int,
    retry_delays: tuple[float, ...],
) -> float:
    """Return the retry delay after a failed SteamCMD attempt."""
    if not retry_delays:
        return 0.0
    return retry_delays[min(attempt - 1, len(retry_delays) - 1)]


def _validated_server_dir(instance: str) -> Path:
    """Return the safe server directory for an instance or raise InstallError."""
    try:
        return paths.validate_server_install_dir(
            paths.server_dir(instance),
            instance=instance,
        )
    except paths.UnsafeServerInstallDirError as e:
        raise InstallError(str(e)) from e


def _validated_steamcmd_install_dir(
    install_dir: Path,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> Path:
    """Return a safe SteamCMD install directory or raise InstallError."""
    try:
        return paths.validate_server_install_dir(install_dir, instance=instance)
    except paths.UnsafeServerInstallDirError as e:
        raise InstallError(str(e)) from e


def _resolve_steamcmd_binary() -> str | None:
    """Return the best available steamcmd binary path."""
    found = shutil.which("steamcmd")
    if found:
        return found

    fallback = Path("/usr/games/steamcmd")
    if fallback.exists():
        return str(fallback)
    return None


def _run_cmd(cmd: list[str], err_msg: str, env: dict[str, str] | None = None) -> None:
    """Run a subprocess command and raise InstallError on failure."""
    try:
        subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            env=env or os.environ,
        )
    except subprocess.CalledProcessError as e:
        raise InstallError(
            tr(
                "{message}:\n{details}",
                message=_(err_msg),
                details=safe_subprocess_error(e.stderr, e.stdout),
            )
        ) from e
    except OSError as e:
        raise InstallError(
            tr(
                "{message}: {error}",
                message=_(err_msg),
                error=redact_sensitive_text(e),
            )
        ) from e


def check_os() -> None:
    """Verify we are running on Linux (and ideally Ubuntu)."""
    if sys.platform != "linux":
        raise InstallError(_("armactl must be run on a Linux system."))


def check_sudo() -> None:
    """Verify sudo access."""
    try:
        subprocess.run(["sudo", "-v"], check=True)
    except subprocess.CalledProcessError:
        raise InstallError(_("Sudo access is required for installation."))


def install_steamcmd() -> None:
    """Install steamcmd via apt if missing."""
    steamcmd_bin = _resolve_steamcmd_binary()
    if steamcmd_bin:
        if steamcmd_bin.startswith("/usr/games") and "/usr/games" not in os.environ.get("PATH", ""):
            os.environ["PATH"] += f"{os.pathsep}/usr/games"
        return

    if not shutil.which("apt-get"):
        raise InstallError(_("apt-get not found. steamcmd must be installed manually on this OS."))

    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")

    _run_cmd(
        ["sudo", "dpkg", "--add-architecture", "i386"],
        "Failed to add i386 architecture",
    )

    try:
        subprocess.run(
            ["sudo", "add-apt-repository", "-y", "multiverse"],
            capture_output=True,
            check=True,
        )
    except Exception:
        pass  # Debian may not expose multiverse.

    _run_cmd(["sudo", "apt-get", "update"], "Failed to run apt-get update")

    debconf_cmds = [
        "echo steam steam/question select 'I AGREE' | sudo debconf-set-selections",
        "echo steam steam/license note '' | sudo debconf-set-selections",
    ]
    for cmd in debconf_cmds:
        try:
            subprocess.run(cmd, shell=True, check=True)
        except subprocess.CalledProcessError as e:
            raise InstallError(
                tr(
                    "Failed to auto-accept Steamcmd EULA: {error}",
                    error=redact_sensitive_text(e),
                )
            ) from e

    _run_cmd(
        ["sudo", "apt-get", "install", "-y", "steamcmd"],
        "Failed to install steamcmd via apt-get",
        env=env,
    )

    steamcmd_bin = _resolve_steamcmd_binary()
    if steamcmd_bin and steamcmd_bin.startswith("/usr/games"):
        os.environ["PATH"] += f"{os.pathsep}/usr/games"

    if not steamcmd_bin:
        raise InstallError(
            _("steamcmd installation seemed to succeed, but binary still not found in PATH.")
        )


def create_install_dir(instance: str) -> None:
    """Create essential directory structure."""
    _validated_server_dir(instance)
    paths.instance_root(instance).mkdir(parents=True, exist_ok=True)
    paths.server_dir(instance).mkdir(parents=True, exist_ok=True)
    paths.config_dir(instance).mkdir(parents=True, exist_ok=True)
    paths.backups_dir(instance).mkdir(parents=True, exist_ok=True)
    paths.modpacks_dir(instance).mkdir(parents=True, exist_ok=True)
    paths.bot_dir(instance).mkdir(parents=True, exist_ok=True)
    ensure_bot_config(instance)


def _stream_cmd(
    cmd: list[str],
    *,
    err_msg: str,
    env: dict[str, str] | None = None,
    idle_timeout_seconds: float | None = None,
) -> Iterator[str]:
    """Run a subprocess and stream output, optionally bounding output stalls."""
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env or os.environ,
            start_new_session=True,
        )
    except OSError as e:
        raise InstallError(
            tr(
                "{message}: {error}",
                message=_(err_msg),
                error=redact_sensitive_text(e),
            )
        ) from e

    output_tail: deque[str] = deque(maxlen=50)
    assert proc.stdout is not None

    if idle_timeout_seconds is None:
        for raw_line in proc.stdout:
            line = redact_sensitive_text(raw_line.rstrip())
            if not line:
                continue
            output_tail.append(line)
            yield line
    else:
        timeout = max(float(idle_timeout_seconds), 0.01)
        output_queue: queue.Queue[object] = queue.Queue()
        stream_finished = object()

        def read_output() -> None:
            try:
                for raw_line in proc.stdout:
                    output_queue.put(raw_line)
            finally:
                output_queue.put(stream_finished)

        reader = threading.Thread(
            target=read_output,
            name="armactl-subprocess-output",
            daemon=True,
        )
        reader.start()
        while True:
            try:
                queued = output_queue.get(timeout=timeout)
            except queue.Empty as exc:
                _terminate_process_group(proc)
                details = tr(
                    "SteamCMD produced no output for {seconds} seconds; "
                    "the stalled process was terminated.",
                    seconds=f"{timeout:.0f}",
                )
                if output_tail:
                    output_details = "\n".join(output_tail)
                    details = f"{output_details}\n{details}"
                raise InstallError(
                    tr(
                        "{message}:\n{details}",
                        message=_(err_msg),
                        details=details,
                    )
                ) from exc
            if queued is stream_finished:
                break
            line = redact_sensitive_text(str(queued).rstrip())
            if not line:
                continue
            output_tail.append(line)
            yield line

    return_code = proc.wait()
    if return_code == 0:
        return

    details = "\n".join(output_tail).strip()
    if not details:
        details = _("steamcmd exited without additional output.")

    raise InstallError(
        tr(
            "{message}:\n{details}",
            message=_(err_msg),
            details=details,
        )
    )


def _terminate_process_group(proc: subprocess.Popen[str]) -> None:
    """Terminate a stalled isolated subprocess and its descendants."""
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        try:
            proc.terminate()
        except OSError:
            return
    try:
        proc.wait(timeout=10.0)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            proc.kill()
        except OSError:
            return
    try:
        proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        return


def download_server(instance: str) -> Iterator[str]:
    """Download Arma Reforger via steamcmd and stream steamcmd output."""
    install_dir = _validated_server_dir(instance)
    yield from stream_server_update(install_dir, instance=instance)


def build_steamcmd_update_command(
    install_dir: Path,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
) -> list[str]:
    """Build the SteamCMD command that installs or validates the server package."""
    install_dir = _validated_steamcmd_install_dir(install_dir, instance=instance)
    steamcmd_bin = _resolve_steamcmd_binary() or "steamcmd"
    return [
        steamcmd_bin,
        "+force_install_dir",
        str(install_dir),
        "+login",
        "anonymous",
        "+app_update",
        "1874900",
        "validate",
        "+quit",
    ]


def _normalize_steam_app_id(app_id: str) -> str:
    normalized = str(app_id or "").strip()
    if not normalized.isdigit():
        raise InstallError("Steam app id is invalid.")
    return normalized


def build_steamcmd_app_info_command(app_id: str = APP_ID) -> list[str]:
    """Build the SteamCMD command that prints public app metadata."""
    normalized_app_id = _normalize_steam_app_id(app_id)
    steamcmd_bin = _resolve_steamcmd_binary() or "steamcmd"
    return [
        steamcmd_bin,
        "+login",
        "anonymous",
        "+app_info_update",
        "1",
        "+app_info_print",
        normalized_app_id,
        "+quit",
    ]


def fetch_steam_app_info(
    app_id: str = APP_ID,
    *,
    timeout_seconds: float = STEAMCMD_APP_INFO_TIMEOUT_SECONDS,
    max_output_chars: int = MAX_STEAM_APP_INFO_OUTPUT_CHARS,
) -> str:
    """Return bounded, redacted Steam app info output for read-only parsing."""
    if _resolve_steamcmd_binary() is None:
        raise InstallError(_("SteamCMD not found in PATH. Make sure it is installed."))

    try:
        result = subprocess.run(
            build_steamcmd_app_info_command(app_id),
            check=True,
            capture_output=True,
            text=True,
            timeout=max(timeout_seconds, 1.0),
        )
    except subprocess.TimeoutExpired as exc:
        raise InstallError(_("SteamCMD latest-build query timed out.")) from exc
    except subprocess.CalledProcessError as exc:
        raise InstallError(
            tr(
                "{message}:\n{details}",
                message=_("Failed to query latest server build via steamcmd"),
                details=safe_subprocess_error(exc.stderr, exc.stdout),
            )
        ) from exc
    except OSError as exc:
        raise InstallError(
            tr(
                "{message}: {error}",
                message=_("Failed to query latest server build via steamcmd"),
                error=redact_sensitive_text(exc),
            )
        ) from exc

    output = redact_sensitive_text("\n".join((result.stdout or "", result.stderr or "")))
    return output[: max(max_output_chars, 0)]


def stream_server_update(
    install_dir: Path,
    *,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    max_attempts: int = STEAMCMD_MAX_ATTEMPTS,
    retry_delays: tuple[float, ...] = STEAMCMD_RETRY_DELAYS_SECONDS,
    idle_timeout_seconds: float = STEAMCMD_UPDATE_IDLE_TIMEOUT_SECONDS,
) -> Iterator[str]:
    """Run bounded SteamCMD app_update validate retries for transient failures."""
    cmd = build_steamcmd_update_command(install_dir, instance=instance)
    attempts = max(max_attempts, 1)

    for attempt in range(1, attempts + 1):
        if attempts > 1:
            yield f"SteamCMD server download attempt {attempt}/{attempts}..."

        try:
            yield from _stream_cmd(
                cmd,
                err_msg="Failed to download server via steamcmd",
                idle_timeout_seconds=idle_timeout_seconds,
            )
            return
        except InstallError as error:
            if attempt >= attempts or _steamcmd_error_is_permanent(error):
                raise

            delay_seconds = _steamcmd_retry_delay(attempt, retry_delays)
            if delay_seconds > 0:
                yield (f"SteamCMD download failed; retrying in {delay_seconds:.0f}s...")
                time.sleep(delay_seconds)
            else:
                yield "SteamCMD download failed; retrying..."


def record_package_manifest(instance: str) -> None:
    """Create armactl's local package integrity manifest."""
    try:
        write_package_manifest(_validated_server_dir(instance))
    except (IntegrityError, OSError) as e:
        raise InstallError(
            tr("Failed to record package integrity manifest: {error}", error=e)
        ) from e


def _templates_dir() -> Path:
    return Path(__file__).parent.parent.parent / "templates"


def render_default_config(
    *,
    rcon_password: str,
    password_admin: str,
    templates_dir: Path | None = None,
) -> str:
    """Render the install/repair default config from the shared registry."""
    resolved_templates_dir = templates_dir or _templates_dir()
    if not resolved_templates_dir.exists():
        raise InstallError(tr("Templates directory missing at {path}", path=resolved_templates_dir))

    env = Environment(
        loader=FileSystemLoader(str(resolved_templates_dir)),
        undefined=StrictUndefined,
    )
    template = env.get_template(DEFAULT_CONFIG_TEMPLATE_NAME)
    return template.render(
        generated_default_template_context(
            rcon_password=rcon_password,
            password_admin=password_admin,
        )
    )


def write_default_config(
    config_path: Path | str,
    *,
    rcon_password: str | None = None,
    password_admin: str | None = None,
) -> None:
    """Write a generated default config.json to the requested path."""
    config_path = Path(config_path)
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_render = render_default_config(
            rcon_password=rcon_password or secrets.token_urlsafe(8),
            password_admin=password_admin or secrets.token_urlsafe(8),
        )
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(config_render)
    except Exception as e:
        raise InstallError(
            tr("Failed to generate default config: {error}", error=redact_sensitive_text(e))
        ) from e


def generate_default_config(instance: str) -> None:
    """Generate default config.json using the shared registry and Jinja template."""
    config_path = paths.config_file(instance)
    if config_path.exists():
        return

    write_default_config(config_path)


def smoke_check(instance: str) -> None:
    """Verify that the package manifest and binary are present."""
    install_dir = _validated_server_dir(instance)
    binary = install_dir / "ArmaReforgerServer"
    if not binary.exists():
        raise InstallError(
            tr(
                "Smoke check failed: binary missing at {path}. Did steamcmd download fail?",
                path=binary,
            )
        )

    integrity = check_package_integrity(install_dir, verify_hashes=False)
    if not integrity.complete:
        raise InstallError(
            tr(
                "Installation package integrity check failed: {details}",
                details=integrity.summary(),
            )
        )


def run_install(instance: str) -> Iterator[str]:
    """Execute the full installation sequence, yielding progress messages."""
    yield _("Verifying OS requirements...")
    check_os()

    yield _("Verifying sudo permissions...")
    check_sudo()

    yield _("Verifying steamcmd...")
    install_steamcmd()

    yield _("Creating installation directories...")
    create_install_dir(instance)

    yield _("Downloading Arma Reforger via steamcmd... (This may take a while)")
    install_dir = _validated_server_dir(instance)
    previous_integrity = check_package_integrity(install_dir)
    mark_install_started(install_dir)
    try:
        yield from download_server(instance)

        yield _("Recording package integrity manifest...")
        record_package_manifest(instance)
        clear_install_marker(install_dir)

        yield _("Running smoke check...")
        smoke_check(instance)
    except Exception:
        if previous_integrity.complete:
            clear_install_marker(install_dir)
        raise

    yield _("Generating default configuration...")
    generate_default_config(instance)

    yield _("Generating systemd services and timers...")
    results = generate_services(instance=instance)
    for result in results:
        yield tr("  - {message}", message=result.message)

    yield _("Installing secure privileged control channel...")
    privileged_results = install_privileged_systemctl_channel()
    for result in privileged_results:
        yield tr("  - {message}", message=result.message)

    yield _("Setting permissions and starting the server...")
    service_name = (
        f"armareforger@{instance}.service" if instance != "default" else paths.SERVICE_NAME
    )
    enable_service(service_name)
    restart_service(service_name)  # Ensure clean start.

    yield _("Saving state.json...")
    discover(instance=instance)

    yield _("Installation complete!")
