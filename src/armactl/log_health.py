"""Bounded health checks for the active Enfusion log generation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

ACTIVE_LOG_NAMES = ("console.log", "error.log", "script.log")
LARGE_LOG_THRESHOLD_BYTES = 256 * 1024 * 1024
TAIL_SAMPLE_BYTES = 256 * 1024
MIN_SPAM_SIGNAL_MATCHES = 20

_SPAM_SIGNALS = (
    ("virtual_machine_exception", b"virtual machine exception"),
    ("division_by_zero", b"reason: division by zero"),
    ("unknown_class", b"unknown class '"),
    ("addon_loading_failed", b"addon loading failed"),
    ("game_creation_failed", b"cannot create game"),
)


@dataclass(frozen=True)
class ActiveLogAnomaly:
    """One path-free anomaly found in an allowlisted active log."""

    name: str
    size_bytes: int
    large: bool
    spam: bool
    spam_signal: str = ""
    spam_matches: int = 0
    sampled_bytes: int = 0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ActiveLogHealth:
    """Path-free result of one bounded active-log inspection."""

    available: bool
    checked_files: tuple[str, ...] = ()
    anomalies: tuple[ActiveLogAnomaly, ...] = ()
    large_threshold_bytes: int = LARGE_LOG_THRESHOLD_BYTES
    tail_sample_bytes: int = TAIL_SAMPLE_BYTES
    min_spam_signal_matches: int = MIN_SPAM_SIGNAL_MATCHES
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _active_log_directory(config_dir: Path, active_console: object) -> Path | None:
    if not isinstance(active_console, str | Path):
        return None
    candidate = Path(active_console)
    if candidate.name != "console.log":
        return None
    try:
        if candidate.is_symlink() or not candidate.is_file():
            return None
        logs_root = (config_dir / "logs").resolve(strict=True)
        resolved = candidate.resolve(strict=True)
    except OSError:
        return None
    if resolved.parent.parent != logs_root:
        return None
    return resolved.parent


def _tail_sample(path: Path, *, max_bytes: int) -> tuple[int, bytes] | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        size = path.stat().st_size
        read_size = min(max(size, 0), max_bytes)
        with path.open("rb") as handle:
            if read_size < size:
                handle.seek(size - read_size)
            data = handle.read(read_size)
    except OSError:
        return None
    return size, data


def _strongest_spam_signal(sample: bytes) -> tuple[str, int]:
    lowered = sample.lower()
    signal = ""
    matches = 0
    for candidate, marker in _SPAM_SIGNALS:
        candidate_matches = lowered.count(marker)
        if candidate_matches > matches:
            signal = candidate
            matches = candidate_matches
    return signal, matches


def query_active_log_health(
    config_dir: str | Path,
    *,
    active_console: object,
    large_threshold_bytes: int = LARGE_LOG_THRESHOLD_BYTES,
    tail_sample_bytes: int = TAIL_SAMPLE_BYTES,
    min_spam_signal_matches: int = MIN_SPAM_SIGNAL_MATCHES,
) -> ActiveLogHealth:
    """Inspect only three allowlisted sibling logs and a bounded tail of each.

    ``active_console`` must be the trusted latest-console result already resolved
    by the telemetry reader. No path supplied by an HTTP request reaches here.
    """
    root = Path(config_dir)
    threshold = max(int(large_threshold_bytes), 1)
    sample_limit = min(max(int(tail_sample_bytes), 1), TAIL_SAMPLE_BYTES)
    spam_threshold = max(int(min_spam_signal_matches), 1)
    log_dir = _active_log_directory(root, active_console)
    if log_dir is None:
        return ActiveLogHealth(
            False,
            large_threshold_bytes=threshold,
            tail_sample_bytes=sample_limit,
            min_spam_signal_matches=spam_threshold,
            error="Active game logs are unavailable.",
        )

    checked: list[str] = []
    anomalies: list[ActiveLogAnomaly] = []
    for name in ACTIVE_LOG_NAMES:
        loaded = _tail_sample(log_dir / name, max_bytes=sample_limit)
        if loaded is None:
            continue
        size, sample = loaded
        checked.append(name)
        signal, matches = _strongest_spam_signal(sample)
        large = size >= threshold
        spam = matches >= spam_threshold
        if large or spam:
            anomalies.append(
                ActiveLogAnomaly(
                    name=name,
                    size_bytes=size,
                    large=large,
                    spam=spam,
                    spam_signal=signal if spam else "",
                    spam_matches=matches if spam else 0,
                    sampled_bytes=len(sample),
                )
            )

    return ActiveLogHealth(
        True,
        checked_files=tuple(checked),
        anomalies=tuple(anomalies),
        large_threshold_bytes=threshold,
        tail_sample_bytes=sample_limit,
        min_spam_signal_matches=spam_threshold,
    )
