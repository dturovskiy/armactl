"""Tests for Linux process metric helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from armactl.metrics import (
    estimate_service_cpu_percent,
    format_bytes,
    format_cpu_percent,
    query_process_metrics,
    query_service_runtime_metrics,
)


def test_format_bytes_handles_small_and_large_values() -> None:
    assert format_bytes(None) == "Unknown"
    assert format_bytes(512) == "512 B"
    assert format_bytes(1024) == "1.0 KiB"
    assert format_bytes(268435456) == "256.0 MiB"


def test_format_cpu_percent_handles_missing_values() -> None:
    assert format_cpu_percent(None) == "Unknown"
    assert format_cpu_percent(12.5) == "12.5%"


def test_query_process_metrics_reads_proc_files() -> None:
    contents = {
        str(Path("/proc/1234/stat")): (
            "1234 (server) S 1 1 1 1 1 1 1 1 1 1 200 100 0 0 0 0 0 0 0 0 1000"
        ),
        str(Path("/proc/1234/status")): "Name:\tserver\nVmRSS:\t262144 kB\n",
        str(Path("/proc/uptime")): "2000.00 0.00\n",
    }

    def fake_read_text(path: Path) -> str:
        try:
            return contents[str(path)]
        except KeyError as error:
            raise OSError("missing") from error

    with patch("armactl.metrics._read_text", side_effect=fake_read_text):
        metrics = query_process_metrics(1234)

    assert metrics.available is True
    assert metrics.pid == 1234
    assert metrics.memory_rss_bytes == 268435456
    assert metrics.cpu_percent is not None
    assert metrics.cpu_percent > 0


def test_query_process_metrics_falls_back_to_statm_when_vmrss_missing() -> None:
    contents = {
        str(Path("/proc/1234/stat")): (
            "1234 (server) S 1 1 1 1 1 1 1 1 1 1 200 100 0 0 0 0 0 0 0 0 1000"
        ),
        str(Path("/proc/1234/status")): "Name:\tserver\n",
        str(Path("/proc/1234/statm")): "1000 65536 0 0 0 0 0\n",
        str(Path("/proc/uptime")): "2000.00 0.00\n",
    }

    def fake_read_text(path: Path) -> str:
        return contents[str(path)]

    with (
        patch("armactl.metrics._read_text", side_effect=fake_read_text),
        patch("armactl.metrics._page_size", return_value=4096),
    ):
        metrics = query_process_metrics(1234)

    assert metrics.available is True
    assert metrics.memory_rss_bytes == 268435456


def test_query_process_metrics_handles_missing_proc_files() -> None:
    with patch("armactl.metrics._read_text", side_effect=OSError("missing")):
        metrics = query_process_metrics(1234)

    assert metrics.available is False
    assert metrics.error == "missing"


def test_estimate_service_cpu_percent_uses_systemd_monotonic_timestamps() -> None:
    service_status = {
        "cpu_usage_nsec": 5_000_000_000,
        "exec_main_start_usec": 5_000_000,
    }

    with (
        patch("armactl.metrics._read_text", return_value="15.0 0.0\n"),
        patch("armactl.metrics._cpu_count", return_value=1),
    ):
        cpu_percent = estimate_service_cpu_percent(service_status)

    assert cpu_percent == 50.0


def test_query_service_runtime_metrics_falls_back_to_systemd_values() -> None:
    service_status = {
        "main_pid": 0,
        "memory_current_bytes": 268435456,
        "cpu_usage_nsec": 5_000_000_000,
        "exec_main_start_usec": 5_000_000,
    }

    with (
        patch("armactl.metrics._read_text", return_value="15.0 0.0\n"),
        patch("armactl.metrics._cpu_count", return_value=1),
    ):
        metrics = query_service_runtime_metrics(service_status)

    assert metrics.available is True
    assert metrics.pid == 0
    assert metrics.cpu_percent == 50.0
    assert metrics.memory_rss_bytes == 268435456
