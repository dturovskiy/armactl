"""Tests for Linux process metric helpers."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import armactl.metrics as metrics

SAMPLE_FPS_LINE = (
    "17:45:09.973   DEFAULT      : FPS: 60.0, frame time "
    "(avg: 16.7 ms, min: 15.3 ms, max: 17.8 ms), Mem: 3387286 kB, "
    "Player: 0, AI: 227, AIChar: 168, Veh: 0 (8), Proj "
    "(S: 0, M: 0, G: 0 | 0), Streaming(Dynam: 1433, Static: 29291)"
)
SAMPLE_FPS_LINE_WITH_MEAN_MEDIAN = (
    "15:26:29.177   DEFAULT      : FPS: 60.0, frame time "
    "(avg: 16.7 ms, min: 15.1 ms, max: 18.0 ms, mean: 16.7 ms, "
    "median: 16.7 ms), Mem: 2571066 kB, Player: 0, AI: 232, "
    "AIChar: 182, Veh: 0 (62), Proj (S: 0, M: 0, G: 0 | 0), "
    "Streaming(Dynam: 1384, Static: 15780)"
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


def test_read_tail_text_file_keeps_recent_complete_lines(tmp_path: Path) -> None:
    log_path = tmp_path / "console.log"
    log_path.write_text(
        "old line\npartial-prefix\nrecent one\nrecent two\n",
        encoding="utf-8",
    )

    result = metrics._read_tail_text_file(log_path, max_bytes=24)

    assert result == "recent one\nrecent two\n"

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


def test_query_server_fps_metrics_parses_current_logstats_line(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    _write_console_log(
        config_dir,
        "2026-06-11_151427",
        SAMPLE_FPS_LINE_WITH_MEAN_MEDIAN,
        mtime=1000.0,
    )

    with patch("armactl.metrics.time.time", return_value=1008.0):
        result = metrics.query_server_fps_metrics(config_dir)

    assert result.available is True
    assert result.fps == 60.0
    assert result.frame_avg_ms == 16.7
    assert result.frame_min_ms == 15.1
    assert result.frame_max_ms == 18.0
    assert result.engine_memory_kb == 2571066
    assert result.ai == 232
    assert result.ai_char == 182


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


def test_query_server_operational_status_reports_mod_download_retry(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    log_path = _write_console_log(
        config_dir,
        "2026-05-18_230000",
        "\n".join(
            [
                "23:00:10.103 NETWORK : Starting dedicated server using command line args.",
                "23:00:12.182 BACKEND : Addon Download started 6872C012EBB3A7D4",
                "23:01:54.012 BACKEND (E): Fragmentizer: Download error",
                "23:01:54.112 BACKEND (E): Fragmentizer: Retrying download",
            ]
        ),
        mtime=1000.0,
    )

    with patch("armactl.metrics.time.time", return_value=1005.0):
        result = metrics.query_server_operational_status(config_dir)

    assert result.available is True
    assert result.state == "downloading_mods"
    assert result.severity == "warning"
    assert result.message == "Downloading mods (retrying)"
    assert result.source == str(log_path)
    assert "Retrying download" in result.details[0]


def test_query_server_operational_status_reports_mission_error(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write_console_log(
        config_dir,
        "2026-05-18_230000",
        "\n".join(
            [
                "23:00:09.955 BACKEND : Loading dedicated server config.",
                "23:00:09.955 RESOURCES (E): Failed to open",
                "23:00:09.955 RESOURCES (E): MissionHeader::ReadMissionHeader "
                "cannot load the resource 'Missions/TRYZUB_Conflict.conf'!",
            ]
        ),
        mtime=1000.0,
    )

    with patch("armactl.metrics.time.time", return_value=1005.0):
        result = metrics.query_server_operational_status(config_dir)

    assert result.available is True
    assert result.state == "mission_error"
    assert result.severity == "error"
    assert result.message == "Mission/config error"


def test_query_server_operational_status_reports_workshop_startup_failure(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write_console_log(
        config_dir,
        "2026-06-14_194328",
        "\n".join(
            [
                "19:43:31.798 ENGINE       : Game successfully created.",
                "19:43:31.914 NETWORK      : Starting dedicated server using command line args.",
                "19:43:33.320 BACKEND   (E): Curl error=SSL connect error",
                "19:43:33.404 BACKEND (E): WorkshopApi/GetDownloadListS2S failed",
                "19:43:33.503 BACKEND (E): Failed to fetch addon details from workshop API!",
                "19:43:33.702 ENGINE    (E): Unable to initialize the game",
                "19:43:33.794 ENGINE       : Game destroyed.",
            ]
        ),
        mtime=1000.0,
    )

    with patch("armactl.metrics.time.time", return_value=1005.0):
        result = metrics.query_server_operational_status(config_dir)

    assert result.available is True
    assert result.state == "startup_failed"
    assert result.severity == "error"
    assert result.message == "Workshop addon metadata error"
    assert any("Failed to fetch addon details" in item for item in result.details)
    assert any("Unable to initialize the game" in item for item in result.details)


def test_query_server_operational_status_prefers_terminal_failure_over_timeout(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    _write_console_log(
        config_dir,
        "2026-09-04_210540",
        "\n".join(
            [
                "21:05:59 BACKEND (E): Curl error=Timeout was reached",
                "21:05:59 BACKEND (E): WorkshopApi/GetDownloadListS2S Timeout",
                "21:05:59 BACKEND (E): Connection timeout to workshop API!",
                "21:05:59 ENGINE (E): Unable to initialize the game",
            ]
        ),
        mtime=1000.0,
    )

    with patch("armactl.metrics.time.time", return_value=1005.0):
        result = metrics.query_server_operational_status(config_dir)

    assert result.state == "startup_failed"
    assert result.severity == "error"
    assert result.message == "Workshop addon metadata error"
    assert any("GetDownloadListS2S" in item for item in result.details)
    assert any("Unable to initialize the game" in item for item in result.details)


def test_query_server_operational_status_exposes_failing_mod_script(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write_console_log(
        config_dir,
        "2026-09-04_211500",
        "\n".join(
            [
                "21:15:00 NETWORK : Starting dedicated server using command line args.",
                "21:15:02 SCRIPT (E): scripts/Game/UI/Inventory/"
                "WCS_LoadoutEditor_InventoryMenuUI.c(181): Unknown type "
                "'SCR_AnalyticsApplication'",
                '21:15:02 SCRIPT (E): Can\'t compile "Game" script module!',
                "21:15:02 ENGINE (E): Addon loading failed",
                "21:15:02 ENGINE (E): Cannot create game!",
                "21:15:02 ENGINE : Game destroyed.",
            ]
        ),
        mtime=1000.0,
    )

    with patch("armactl.metrics.time.time", return_value=1005.0):
        result = metrics.query_server_operational_status(config_dir)

    assert result.state == "startup_failed"
    assert result.message == "Server startup failed"
    assert any("WCS_LoadoutEditor" in item for item in result.details)
    assert any("Can't compile" in item for item in result.details)
    assert any("Addon loading failed" in item for item in result.details)


def test_query_server_operational_status_does_not_wait_after_game_destroyed(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    _write_console_log(
        config_dir,
        "2026-09-04_211600",
        "\n".join(
            [
                "21:16:00 NETWORK : Starting dedicated server using command line args.",
                "21:16:03 ENGINE : Game destroyed.",
            ]
        ),
        mtime=1000.0,
    )

    with patch("armactl.metrics.time.time", return_value=1005.0):
        result = metrics.query_server_operational_status(config_dir)

    assert result.state == "startup_failed"
    assert result.severity == "error"
    assert result.message == "Server startup failed"
    assert any("Game destroyed" in item for item in result.details)


def test_query_server_operational_status_exposes_runtime_crash_context(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    _write_console_log(
        config_dir,
        "2026-09-05_170000",
        "\n".join(
            [
                SAMPLE_FPS_LINE,
                "17:00:28 RPL (E): Script called BumpMe from an Item without any "
                "replicable state! class=CLBR_RemoteTurretDriveComponent",
                "17:00:29 RESOURCES (E): Wrong GUID/name for resource "
                "Particles/Vehicle/Mi8/Vehicle_fire_engine_Mi8_01.ptc",
                "17:00:39 ENGINE (F): Application crashed! Generated memory dump "
                "/tmp/server.dmp",
            ]
        ),
        mtime=1000.0,
    )

    with patch("armactl.metrics.time.time", return_value=1005.0):
        result = metrics.query_server_operational_status(config_dir)

    assert result.state == "runtime_crash"
    assert result.severity == "error"
    assert result.message == "Game process crashed"
    assert any("CLBR_RemoteTurretDriveComponent" in item for item in result.details)
    assert any("Vehicle_fire_engine_Mi8_01" in item for item in result.details)
    assert any("Application crashed" in item for item in result.details)


def test_query_recent_server_incidents_identifies_atgm_kornet_crash(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    _write_console_log(
        config_dir,
        "2026-09-06_160045",
        "\n".join(
            [
                SAMPLE_FPS_LINE,
                "16:29:27 ENTITY: SpawnEntityPrefab "
                '"{E4B61F751D2B8B7E}Prefabs/Weapons/Tripods/Tripod_KORNET.et"',
                "16:29:27 SCRIPT (W): [CLBR_KORNET_OPTIC_ACTION] INIT owner=Turret",
                "16:29:28 WEAPON (W): Warning: Loading incompatible ammo in barrel 0",
                "16:29:42 ENGINE (E): Application crashed! Generated memory dump: "
                "/tmp/server.dmp",
            ]
        ),
        mtime=1000.0,
    )

    with patch("armactl.metrics.time.time", return_value=1005.0):
        incidents = metrics.query_recent_server_incidents(config_dir)

    assert len(incidents) == 1
    assert incidents[0].kind == "runtime_crash"
    assert incidents[0].summary == "Native game crash (crash dump)"
    assert incidents[0].suspect == "ATGM / CLBR weapon stack"
    assert incidents[0].confidence == "high"
    assert "Kornet prefab" in incidents[0].reason
    assert any("Tripod_KORNET" in item for item in incidents[0].evidence)
    assert any("Application crashed" in item for item in incidents[0].evidence)


def test_query_recent_server_incidents_prefers_persistent_collector_bundle(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "default" / "config"
    metadata_dir = tmp_path / "default" / "incidents" / "20260909T141310Z-memory"
    metadata_dir.mkdir(parents=True)
    (metadata_dir / "metadata.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": metadata_dir.name,
                "occurred_at": "2026-09-09T14:13:10+00:00",
                "captured_at": "2026-09-09T14:13:20+00:00",
                "kind": "memory_corruption",
                "severity": "error",
                "summary": "Native memory corruption",
                "suspect": "Enfusion native heap / addon-triggered engine path",
                "confidence": "high",
                "reason": "Allocator reported double free.",
                "evidence": ["double free or corruption (!prev)"],
                "confirmed": True,
                "pid": 38222,
                "artifacts": ["journal.log", "runtime.json"],
                "bundle": f"incidents/{metadata_dir.name}",
            }
        ),
        encoding="utf-8",
    )

    with patch(
        "armactl.metrics.time.time",
        return_value=datetime(2026, 9, 9, 14, 20, tzinfo=timezone.utc).timestamp(),
    ):
        incidents = metrics.query_recent_server_incidents(config_dir)

    assert len(incidents) == 1
    assert incidents[0].source == "collector"
    assert incidents[0].kind == "memory_corruption"
    assert incidents[0].confirmed is True
    assert incidents[0].pid == 38222
    assert incidents[0].artifacts == ("journal.log", "runtime.json")


def test_query_recent_server_incidents_ignores_early_game_destroyed_after_recovery(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    _write_console_log(
        config_dir,
        "2026-09-06_171628",
        "\n".join(
            [
                "17:16:35 NETWORK: Starting dedicated server using command line args.",
                "17:16:35 ENGINE: Game destroyed.",
                SAMPLE_FPS_LINE,
            ]
        ),
        mtime=1000.0,
    )

    with patch("armactl.metrics.time.time", return_value=1005.0):
        incidents = metrics.query_recent_server_incidents(config_dir)

    assert incidents == ()


def test_query_recent_server_incidents_ignores_controlled_shutdown_after_fps(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    _write_console_log(
        config_dir,
        "2026-09-06_160045",
        "\n".join(
            [
                SAMPLE_FPS_LINE,
                "17:16:24 DEFAULT: [PERSISTENCE] Save completed successfully.",
                "17:16:26 ENGINE: Game destroyed.",
            ]
        ),
        mtime=1000.0,
    )

    with patch("armactl.metrics.time.time", return_value=1005.0):
        incidents = metrics.query_recent_server_incidents(config_dir)

    assert incidents == ()


def test_new_process_without_telemetry_reports_recent_previous_crash(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    crashed_log = _write_console_log(
        config_dir,
        "2026-09-05_170000",
        "\n".join(
            [
                SAMPLE_FPS_LINE,
                "17:00:39 ENGINE (F): Application crashed! Generated memory dump",
            ]
        ),
        mtime=1000.0,
    )
    _write_console_log(
        config_dir,
        "2026-09-05_170056",
        "17:00:57 BACKEND : Loading dedicated server config.",
        mtime=1004.0,
    )

    with patch("armactl.metrics.time.time", return_value=1005.0):
        result = metrics.query_server_operational_status(config_dir)

    assert result.state == "runtime_crash"
    assert result.message == "Game process crashed"
    assert result.source == str(crashed_log)


def test_query_server_operational_status_reports_ready_from_fps(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    _write_console_log(
        config_dir,
        "2026-05-18_230000",
        SAMPLE_FPS_LINE,
        mtime=1000.0,
    )

    with patch("armactl.metrics.time.time", return_value=1005.0):
        result = metrics.query_server_operational_status(config_dir)

    assert result.available is True
    assert result.state == "ready"
    assert result.severity == "success"
    assert result.message == "Ready"


def test_query_server_operational_status_reports_ready_when_rcon_noise_is_latest(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    _write_console_log(
        config_dir,
        "2026-06-11_151427",
        "\n".join(
            [
                SAMPLE_FPS_LINE_WITH_MEAN_MEDIAN,
                "15:26:31.433 BACKEND : [RCON] 127.0.0.1:58126 Authorized as Client #0",
                "15:26:34.689 BACKEND : [RCON] Client 0 logged out!",
            ]
        ),
        mtime=1000.0,
    )

    with patch("armactl.metrics.time.time", return_value=1005.0):
        result = metrics.query_server_operational_status(config_dir)

    assert result.available is True
    assert result.state == "ready"
    assert result.severity == "success"
    assert result.message == "Ready"


def test_query_server_operational_status_ignores_backend_heartbeat_timeout(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    _write_console_log(
        config_dir,
        "2026-06-11_151427",
        "\n".join(
            [
                SAMPLE_FPS_LINE_WITH_MEAN_MEDIAN,
                "15:26:04.972 BACKEND   (E): Curl error=Timeout was reached",
                "15:26:04.996 BACKEND   (E): DS Room Heartbeat fail, Timeout to recover=300 sec",
            ]
        ),
        mtime=1000.0,
    )

    with patch("armactl.metrics.time.time", return_value=1005.0):
        result = metrics.query_server_operational_status(config_dir)

    assert result.available is True
    assert result.state == "ready"
    assert result.message == "Ready"



def test_query_server_operational_status_reports_backend_heartbeat_shutdown(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    _write_console_log(
        config_dir,
        "2026-07-16_084328",
        "\n".join(
            [
                SAMPLE_FPS_LINE_WITH_MEAN_MEDIAN,
                "08:39:28.733 BACKEND (E): DS Room Heartbeat fail, Timeout to recover=300 sec",
                "08:43:28.731 BACKEND (E): DS Heartbeat Failing for too long, "
                "shutting down... failed=12 vs. succeed=157",
                "08:43:41.529 [PERSISTENCE] Save (SHUTDOWN) started.",
                "08:49:13.413 ENGINE (F): Application hangs (force crash) 304 s",
                "08:49:13.545 ENGINE (F): Application crashed!",
            ]
        ),
        mtime=1000.0,
    )

    with patch("armactl.metrics.time.time", return_value=1005.0):
        result = metrics.query_server_operational_status(config_dir)

    assert result.available is True
    assert result.state == "backend_heartbeat_failure"
    assert result.severity == "error"
    assert result.message == "Backend heartbeat failure"
    assert any("DS Heartbeat Failing for too long" in item for item in result.details)
    assert any("Application hangs" in item for item in result.details)


def test_query_server_operational_status_reports_backend_connectivity_before_fps(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    _write_console_log(
        config_dir,
        "2026-07-16_085214",
        "\n".join(
            [
                "08:52:20.010 BACKEND (E): Curl error=Timeout was reached",
                "08:52:20.020 BACKEND (E): GameConfig/List Timeout",
                "08:52:27.100 BACKEND (E): WorkshopApi/GetServers failed",
            ]
        ),
        mtime=1000.0,
    )

    with patch("armactl.metrics.time.time", return_value=1005.0):
        result = metrics.query_server_operational_status(config_dir)

    assert result.available is True
    assert result.state == "backend_connectivity_issue"
    assert result.severity == "warning"
    assert result.message == "Backend connectivity issue"
    assert any("GameConfig/List Timeout" in item for item in result.details)



def test_query_server_operational_status_uses_fps_from_full_bounded_tail(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    noise_lines = [
        f"15:27:{index % 60:02d}.000 SCRIPT (E): benign script exception spam {index}"
        for index in range(metrics.OPERATIONAL_STATUS_TAIL_LINES + 25)
    ]
    _write_console_log(
        config_dir,
        "2026-06-11_151427",
        "\n".join([SAMPLE_FPS_LINE_WITH_MEAN_MEDIAN, *noise_lines]),
        mtime=1000.0,
    )

    with patch("armactl.metrics.time.time", return_value=1005.0):
        result = metrics.query_server_operational_status(config_dir)

    assert result.available is True
    assert result.state == "ready"
    assert result.severity == "success"
    assert result.message == "Ready"
    assert result.details
    assert "FPS: 60.0" in result.details[0]


def test_query_server_operational_status_keeps_fresh_mod_marker_above_old_fps(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    _write_console_log(
        config_dir,
        "2026-06-11_151427",
        "\n".join(
            [
                SAMPLE_FPS_LINE_WITH_MEAN_MEDIAN,
                "15:27:01.000 SCRIPT : irrelevant line",
                "15:27:02.000 BACKEND : Addon Download started 6872C012EBB3A7D4",
            ]
        ),
        mtime=1000.0,
    )

    with patch("armactl.metrics.time.time", return_value=1005.0):
        result = metrics.query_server_operational_status(config_dir)

    assert result.available is True
    assert result.state == "downloading_mods"
    assert result.severity == "warning"
    assert result.message == "Downloading mods"


def test_query_server_operational_status_keeps_fresh_error_above_old_fps(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    _write_console_log(
        config_dir,
        "2026-06-11_151427",
        "\n".join(
            [
                SAMPLE_FPS_LINE_WITH_MEAN_MEDIAN,
                "15:27:01.000 SCRIPT : irrelevant line",
                "15:27:02.000 RESOURCES (E): MissionHeader::ReadMissionHeader "
                "cannot load the resource 'Missions/Broken.conf'!",
            ]
        ),
        mtime=1000.0,
    )

    with patch("armactl.metrics.time.time", return_value=1005.0):
        result = metrics.query_server_operational_status(config_dir)

    assert result.available is True
    assert result.state == "mission_error"
    assert result.severity == "error"
    assert result.message == "Mission/config error"


def test_query_server_operational_status_does_not_ready_from_stale_fps(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    _write_console_log(
        config_dir,
        "2026-06-11_151427",
        SAMPLE_FPS_LINE_WITH_MEAN_MEDIAN,
        mtime=1000.0,
    )

    with patch("armactl.metrics.time.time", return_value=1201.0):
        result = metrics.query_server_operational_status(config_dir, max_age_seconds=120.0)

    assert result.available is False
    assert result.state == "telemetry_stale"
    assert result.severity == "warning"
    assert result.message == "Telemetry stale"
