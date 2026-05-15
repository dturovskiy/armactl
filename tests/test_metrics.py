"""Tests for Linux process metric helpers."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import armactl.metrics as metrics

SAMPLE_FPS_LINE = (
    "17:45:09.973   DEFAULT      : FPS: 60.0, frame time "
    "(avg: 16.7 ms, min: 15.3 ms, max: 17.8 ms), Mem: 3387286 kB, "
    "Player: 0, AI: 227, AIChar: 168, Veh: 0 (8), Proj "
    "(S: 0, M: 0, G: 0 | 0), Streaming(Dynam: 1433, Static: 29291)"
)


def _write_console_log(
    config_dir: Path,
    timestamp: str,
    text: str,
    *,
    mtime: float,
) -> Path:
    log_dir = config_dir / "logs" / timestamp
    log_dir.mkdir(parents=True)
    log_path = log_dir / "console.log"
    log_path.write_text(text, encoding="utf-8")
    os.utime(log_path, (mtime, mtime))
    return log_path


def test_format_bytes_handles_small_and_large_values() -> None:
    assert metrics.format_bytes(None) == "Unknown"
    assert metrics.format_bytes(512) == "512 B"
    assert metrics.format_bytes(1024) == "1.0 KiB"
    assert metrics.format_bytes(268435456) == "256.0 MiB"


def test_format_cpu_percent_handles_missing_values() -> None:
    assert metrics.format_cpu_percent(None) == "Unknown"
    assert metrics.format_cpu_percent(12.5) == "12.5%"


def test_format_fps_and_frame_time_handle_missing_values() -> None:
    assert metrics.format_fps(None) == "Unknown"
    assert metrics.format_fps(59.95) == "60.0"
    assert metrics.format_frame_time_ms(None) == "Unknown"
    assert metrics.format_frame_time_ms(16.666) == "16.7 ms"


def test_format_load_average_and_duration_handle_missing_values() -> None:
    assert metrics.format_load_average(None, None, None) == "Unknown"
    assert metrics.format_load_average(0.75, 0.50, 0.25) == "0.75 / 0.50 / 0.25"
    assert metrics.format_duration(None) == "Unknown"
    assert metrics.format_duration(93784) == "1d 2h 3m"


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
        cpu_percent = metrics.estimate_host_cpu_percent()

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
        result = metrics.query_process_metrics(1234)

    assert result.available is True
    assert result.pid == 1234
    assert result.memory_rss_bytes == 268435456
    assert result.cpu_percent is not None
    assert result.cpu_percent > 0


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
        result = metrics.query_process_metrics(1234)

    assert result.available is True
    assert result.memory_rss_bytes == 268435456


def test_query_process_metrics_handles_missing_proc_files() -> None:
    with patch("armactl.metrics._read_text", side_effect=OSError("missing")):
        result = metrics.query_process_metrics(1234)

    assert result.available is False
    assert result.error == "missing"


def test_estimate_service_cpu_percent_uses_systemd_monotonic_timestamps() -> None:
    service_status = {
        "cpu_usage_nsec": 5_000_000_000,
        "exec_main_start_usec": 5_000_000,
    }

    with (
        patch("armactl.metrics._read_text", return_value="15.0 0.0\n"),
        patch("armactl.metrics._cpu_count", return_value=1),
    ):
        cpu_percent = metrics.estimate_service_cpu_percent(service_status)

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
        result = metrics.query_service_runtime_metrics(service_status)

    assert result.available is True
    assert result.pid == 0
    assert result.cpu_percent == 50.0
    assert result.memory_rss_bytes == 268435456


def test_query_service_runtime_metrics_hides_stale_values_for_stopped_service() -> None:
    service_status = {
        "active": False,
        "active_state": "inactive",
        "main_pid": 0,
        "memory_current_bytes": None,
        "cpu_usage_nsec": 5_000_000_000,
        "exec_main_start_usec": 5_000_000,
    }

    result = metrics.query_service_runtime_metrics(service_status)

    assert result.available is False
    assert result.pid == 0
    assert result.cpu_percent is None
    assert result.memory_rss_bytes is None


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
        result = metrics.query_host_metrics("/")

    assert result.available is True
    assert result.cpu_percent == 37.5
    assert result.memory_used_bytes == 4294967296
    assert result.memory_total_bytes == 8589934592
    assert result.disk_used_bytes == 400
    assert result.disk_total_bytes == 1000
    assert result.load_average_1m == 0.75
    assert result.load_average_5m == 0.5
    assert result.load_average_15m == 0.25
    assert result.uptime_seconds == 7200.0


def test_query_server_fps_metrics_parses_valid_logstats_line(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    log_path = _write_console_log(
        config_dir,
        "2026-05-15_174500",
        f"{SAMPLE_FPS_LINE.replace('FPS: 60.0', 'FPS: 30.0')}\n{SAMPLE_FPS_LINE}\n",
        mtime=1000.0,
    )

    with patch("armactl.metrics.time.time", return_value=1008.0):
        result = metrics.query_server_fps_metrics(config_dir)

    assert result.available is True
    assert result.stale is False
    assert result.source == str(log_path)
    assert result.fps == 60.0
    assert result.frame_avg_ms == 16.7
    assert result.frame_min_ms == 15.3
    assert result.frame_max_ms == 17.8
    assert result.engine_memory_kb == 3387286
    assert result.players == 0
    assert result.ai == 227
    assert result.ai_char == 168
    assert result.age_seconds == 8.0


def test_query_server_fps_metrics_selects_latest_console_log_by_mtime(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    _write_console_log(
        config_dir,
        "older",
        SAMPLE_FPS_LINE.replace("FPS: 60.0", "FPS: 30.0"),
        mtime=1000.0,
    )
    latest_log = _write_console_log(
        config_dir,
        "newer",
        SAMPLE_FPS_LINE.replace("FPS: 60.0", "FPS: 55.5"),
        mtime=2000.0,
    )

    with patch("armactl.metrics.time.time", return_value=2010.0):
        result = metrics.query_server_fps_metrics(config_dir)

    assert result.available is True
    assert result.source == str(latest_log)
    assert result.fps == 55.5


def test_query_server_fps_metrics_reports_missing_logs(tmp_path: Path) -> None:
    result = metrics.query_server_fps_metrics(tmp_path / "config")

    assert result.available is False
    assert result.error == "server FPS telemetry log is not available"


def test_query_server_fps_metrics_reports_missing_fps_line(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    log_path = _write_console_log(
        config_dir,
        "2026-05-15_174500",
        "server started\nno telemetry yet\n",
        mtime=1000.0,
    )

    result = metrics.query_server_fps_metrics(config_dir)

    assert result.available is False
    assert result.source == str(log_path)
    assert result.error == "server FPS telemetry line is not available"


def test_query_server_fps_metrics_returns_stale_values(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write_console_log(
        config_dir,
        "2026-05-15_174500",
        SAMPLE_FPS_LINE,
        mtime=1000.0,
    )

    with patch("armactl.metrics.time.time", return_value=1100.0):
        result = metrics.query_server_fps_metrics(config_dir, max_age_seconds=45.0)

    assert result.available is False
    assert result.stale is True
    assert result.error == "server FPS telemetry is stale"
    assert result.fps == 60.0
    assert result.frame_avg_ms == 16.7
    assert result.age_seconds == 100.0


def test_query_server_fps_metrics_ignores_malformed_lines(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write_console_log(
        config_dir,
        "2026-05-15_174500",
        "FPS: nope, frame time (avg: no ms, min: 0 ms, max: 0 ms)\n",
        mtime=1000.0,
    )

    result = metrics.query_server_fps_metrics(config_dir)

    assert result.available is False
    assert result.error == "server FPS telemetry line is not available"
