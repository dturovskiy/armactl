"""Typed metric and incident DTOs shared by collection and presentation layers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ProcessMetrics:
    """Best-effort process CPU and memory metrics."""

    available: bool
    pid: int
    cpu_percent: float | None = None
    memory_rss_bytes: int | None = None
    error: str = ""


@dataclass
class HostMetrics:
    """Best-effort host/VM metrics for diagnostics views."""

    available: bool
    cpu_percent: float | None = None
    memory_used_bytes: int | None = None
    memory_total_bytes: int | None = None
    disk_used_bytes: int | None = None
    disk_total_bytes: int | None = None
    load_average_1m: float | None = None
    load_average_5m: float | None = None
    load_average_15m: float | None = None
    uptime_seconds: float | None = None
    error: str = ""


@dataclass
class ServerFpsMetrics:
    """Real Arma Reforger engine FPS metrics parsed from -logStats output."""

    available: bool
    fps: float | None = None
    frame_avg_ms: float | None = None
    frame_min_ms: float | None = None
    frame_max_ms: float | None = None
    engine_memory_kb: int | None = None
    players: int | None = None
    ai: int | None = None
    ai_char: int | None = None
    age_seconds: float | None = None
    source: str = ""
    stale: bool = False
    error: str = ""


@dataclass
class ServerOperationalStatus:
    """Best-effort lifecycle state parsed from recent server log lines."""

    available: bool
    state: str = "unknown"
    severity: str = "info"
    message: str = "Unknown"
    details: tuple[str, ...] = ()
    age_seconds: float | None = None
    source: str = ""
    error: str = ""


@dataclass(frozen=True)
class ServerIncident:
    """A bounded, operator-facing explanation of a recent server exit."""

    occurred_at: str
    kind: str
    severity: str
    summary: str
    suspect: str
    confidence: str
    reason: str
    evidence: tuple[str, ...] = ()
    source: str = "log_inference"
    incident_id: str = ""
    captured_at: str = ""
    bundle: str = ""
    confirmed: bool = False
    pid: int = 0
    artifacts: tuple[str, ...] = ()
    first_seen_at: str = ""
    last_seen_at: str = ""
    occurrence_count: int = 1
