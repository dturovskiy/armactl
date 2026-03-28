"""Tests for Linux process metric helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from armactl.metrics import format_bytes, format_cpu_percent, query_process_metrics


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
        return contents[str(path)]

    with patch("armactl.metrics._read_text", side_effect=fake_read_text):
        metrics = query_process_metrics(1234)

    assert metrics.available is True
    assert metrics.pid == 1234
    assert metrics.memory_rss_bytes == 268435456
    assert metrics.cpu_percent is not None
    assert metrics.cpu_percent > 0


def test_query_process_metrics_handles_missing_proc_files() -> None:
    with patch("armactl.metrics._read_text", side_effect=OSError("missing")):
        metrics = query_process_metrics(1234)

    assert metrics.available is False
    assert metrics.error == "missing"
