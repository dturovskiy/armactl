"""Tests for Linux process metric helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from armactl.metrics import (
    estimate_service_cpu_percent,
    estimate_host_cpu_percent,
    format_bytes,
    format_cpu_percent,
    format_duration,
    format_load_average,
    query_host_metrics,
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


def test_format_load_average_and_duration_handle_missing_values() -> None:
    assert format_load_average(None, None, None) == "Unknown"
    assert format_load_average(0.75, 0.50, 0.25) == "0.75 / 0.50 / 0.25"
    assert format_duration(None) == "Unknown"
    assert format_duration(93784) == "1d 2h 3m"


def test_estimate_host_cpu_percent_uses_proc_stat_delta() -> None:
    proc_stat_samples = iter(
        [
            "cpu  100 0 100 700 100 0 0 0 0 0\n",
            "cpu  160 0 140 730 110 0 0 0 0 0\n",
        ]
    )

    with (
        patch("armactl.metrics._read_text", side_effect=lambda path: next(proc_stat_samples)),
        patch("armactl.metrics.time.sleep"),
    ):
        cpu_percent = estimate_host_cpu_percent()

    assert cpu_percent is not None
    assert round(cpu_percent, 1) == 71.4


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
        "active": True,
        "active_state": "active",
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


def test_query_service_runtime_metrics_hides_stale_values_for_stopped_service() -> None:
    service_status = {
        "active": False,
        "active_state": "inactive",
        "main_pid": 0,
        "memory_current_bytes": None,
        "cpu_usage_nsec": 5_000_000_000,
        "exec_main_start_usec": 5_000_000,
    }

    metrics = query_service_runtime_metrics(service_status)

    assert metrics.available is False
    assert metrics.pid == 0
    assert metrics.cpu_percent is None
    assert metrics.memory_rss_bytes is None


def test_query_host_metrics_reads_meminfo_disk_load_and_uptime() -> None:
    contents = {
        str(Path("/proc/meminfo")): "MemTotal:       8388608 kB\nMemAvailable:   4194304 kB\n",
        str(Path("/proc/uptime")): "7200.00 0.00\n",
    }
    disk_usage = type("DiskUsage", (), {"used": 400, "total": 1000})()

    def fake_read_text(path: Path) -> str:
        return contents[str(path)]

    with (
        patch("armactl.metrics._read_text", side_effect=fake_read_text),
        patch("armactl.metrics.estimate_host_cpu_percent", return_value=37.5),
        patch("armactl.metrics.shutil.disk_usage", return_value=disk_usage),
        patch("armactl.metrics.os.getloadavg", return_value=(0.75, 0.5, 0.25)),
    ):
        metrics = query_host_metrics("/")

    assert metrics.available is True
    assert metrics.cpu_percent == 37.5
    assert metrics.memory_used_bytes == 4294967296
    assert metrics.memory_total_bytes == 8589934592
    assert metrics.disk_used_bytes == 400
    assert metrics.disk_total_bytes == 1000
    assert metrics.load_average_1m == 0.75
    assert metrics.load_average_5m == 0.5
    assert metrics.load_average_15m == 0.25
    assert metrics.uptime_seconds == 7200.0
