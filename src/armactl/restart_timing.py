"""Shared timing contract for bounded Arma Reforger restarts."""

from __future__ import annotations

from dataclasses import dataclass


def _format_systemd_timeout(seconds: int) -> str:
    """Return a compact systemd duration while preserving current unit text."""
    if seconds % 60 == 0:
        return f"{seconds // 60}min"
    return f"{seconds}s"


@dataclass(frozen=True)
class RestartTimingContract:
    """Single source of truth for safe restart helper and caller timeouts."""

    stop_grace_seconds: int
    post_kill_grace_seconds: int
    start_grace_seconds: int
    stability_check_seconds: int
    poll_seconds: int
    helper_overhead_seconds: int
    caller_guard_seconds: int

    @property
    def helper_state_window_seconds(self) -> int:
        """Bounded helper window spent waiting on service state transitions."""
        return (
            self.stop_grace_seconds
            + self.post_kill_grace_seconds
            + self.start_grace_seconds
            + self.stability_check_seconds
        )

    @property
    def helper_worst_case_window_seconds(self) -> int:
        """Helper state window plus bounded polling/subprocess overhead."""
        return self.helper_state_window_seconds + self.helper_overhead_seconds

    @property
    def restart_unit_timeout_seconds(self) -> int:
        """systemd TimeoutStartSec for the restart helper unit."""
        return self.helper_worst_case_window_seconds

    @property
    def restart_unit_timeout_start_sec(self) -> str:
        """systemd TimeoutStartSec value rendered into unit files."""
        return _format_systemd_timeout(self.restart_unit_timeout_seconds)

    @property
    def caller_timeout_seconds(self) -> int:
        """Caller-side subprocess guard for starting the restart helper unit."""
        return self.restart_unit_timeout_seconds + self.caller_guard_seconds


RESTART_TIMING = RestartTimingContract(
    stop_grace_seconds=95,
    post_kill_grace_seconds=25,
    start_grace_seconds=180,
    stability_check_seconds=30,
    poll_seconds=2,
    helper_overhead_seconds=30,
    caller_guard_seconds=60,
)
