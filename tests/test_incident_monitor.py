"""Tests for persistent, non-restarting incident evidence collection."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from armactl import incident_monitor


def _journal_record(
    message: str,
    *,
    cursor: str,
    timestamp: float,
    pid: int = 4321,
) -> str:
    return json.dumps(
        {
            "MESSAGE": message,
            "__CURSOR": cursor,
            "_SOURCE_REALTIME_TIMESTAMP": str(int(timestamp * 1_000_000)),
            "_PID": str(pid),
        }
    )


def _systemctl_output(pid: int = 4321) -> str:
    return "\n".join(
        (
            "ActiveState=active",
            "SubState=running",
            f"MainPID={pid}",
            "NRestarts=0",
            "Result=success",
            "ExecMainCode=0",
            "ExecMainStatus=0",
            "ExecMainStartTimestamp=Wed 2026-09-09 14:00:00 UTC",
            "ExecMainExitTimestamp=",
            "LimitCORE=infinity",
            "LimitCORESoft=infinity",
        )
    )


def _runtime_files(data_root: Path, *, mtime: float) -> Path:
    config_dir = data_root / "default" / "config"
    log_dir = config_dir / "logs" / "logs_2026-09-09_14-00-00"
    log_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        json.dumps(
            {
                "game": {
                    "name": "Test server",
                    "scenarioId": "{MISSION}Missions/TestScenario.conf",
                    "maxPlayers": 128,
                    "password": "do-not-store",
                    "mods": [
                        {"modId": "64F10E068D5880A6", "name": "GM Tools", "version": "2.2.0"}
                    ],
                },
                "rcon": {"password": "also-do-not-store"},
            }
        ),
        encoding="utf-8",
    )
    update_root = data_root / "default" / "server-update"
    update_root.mkdir()
    (update_root / "state.json").write_text(
        json.dumps(
            {
                "active_profile": "community-modded",
                "active_mode": "modded",
                "active_build": "24870635",
            }
        ),
        encoding="utf-8",
    )
    console = log_dir / "console.log"
    console.write_text(
        "ENGINE : Creating game instance..., version 1.8.0.13, built yesterday\n"
        "DEFAULT : FPS: 120.0, frame time (avg: 8 ms, min: 1 ms, max: 12 ms), "
        "Mem: 100 kB, Player: 1, AI: 0, AIChar: 0\n",
        encoding="utf-8",
    )
    for name in ("script.log", "error.log", "crash.log"):
        (log_dir / name).write_text("", encoding="utf-8")
    os.utime(console, (mtime, mtime))
    return log_dir


def test_collector_correlates_double_free_and_hang_and_redacts_bundle(tmp_path: Path) -> None:
    now = datetime(2026, 9, 9, 14, 20, tzinfo=timezone.utc).timestamp()
    _runtime_files(tmp_path, mtime=now)
    journal = "\n".join(
        (
            _journal_record(
                "SCRIPT : Exited Unlimited Game Master from 192.168.1.20",
                cursor="c1",
                timestamp=now - 420,
            ),
            _journal_record(
                "double free or corruption (!prev)", cursor="c2", timestamp=now - 419
            ),
            _journal_record(
                "Application hangs (force crash) 301 s", cursor="c3", timestamp=now - 118
            ),
        )
    )

    def runner(args):
        if args[0] == "systemctl":
            return incident_monitor.CommandOutput(0, _systemctl_output())
        assert args[0] == "journalctl"
        return incident_monitor.CommandOutput(0, journal)

    result = incident_monitor.collect_incidents_once(
        data_root=tmp_path,
        runner=runner,
        now=now,
    )

    assert result.success is True
    assert result.captured == 1
    assert result.updated == 1
    assert len(set(result.incident_ids)) == 1
    bundle = tmp_path / "default" / "incidents" / result.incident_ids[0]
    metadata = json.loads((bundle / "metadata.json").read_text(encoding="utf-8"))
    runtime = json.loads((bundle / "runtime.json").read_text(encoding="utf-8"))
    journal_text = (bundle / "journal.log").read_text(encoding="utf-8")

    assert metadata["kind"] == "memory_corruption"
    assert metadata["suspect"] == "Game Master cleanup / admin-mod interaction"
    assert metadata["confirmed"] is True
    assert metadata["signals"] == ["hang", "memory_corruption"]
    assert metadata["profile"]["mod_count"] == 1
    assert metadata["profile"]["profile_name"] == "community-modded"
    assert runtime["game_version"] == "1.8.0.13"
    assert runtime["core_capture"]["service_limit_core_soft"] == "infinity"
    assert "do-not-store" not in json.dumps(metadata)
    assert "also-do-not-store" not in json.dumps(runtime)
    assert "192.168.1.20" not in journal_text
    assert "double free or corruption" in journal_text
    assert "Application hangs" in journal_text


def test_collector_deduplicates_already_seen_journal_signal(tmp_path: Path) -> None:
    now = datetime(2026, 9, 9, 14, 20, tzinfo=timezone.utc).timestamp()
    _runtime_files(tmp_path, mtime=now)
    journal = _journal_record(
        "double free or corruption (!prev)", cursor="c1", timestamp=now - 1
    )

    def runner(args):
        if args[0] == "systemctl":
            return incident_monitor.CommandOutput(0, _systemctl_output())
        return incident_monitor.CommandOutput(0, journal)

    first = incident_monitor.collect_incidents_once(
        data_root=tmp_path, runner=runner, now=now
    )
    second = incident_monitor.collect_incidents_once(
        data_root=tmp_path, runner=runner, now=now + 10
    )

    assert first.captured == 1
    assert second.captured == 0
    assert second.updated == 0
    assert second.ignored >= 1


def test_collector_captures_stale_live_process_before_restart(tmp_path: Path) -> None:
    now = datetime(2026, 9, 9, 14, 20, tzinfo=timezone.utc).timestamp()
    _runtime_files(tmp_path, mtime=now - 200)

    def runner(args):
        if args[0] == "systemctl":
            return incident_monitor.CommandOutput(0, _systemctl_output(pid=999999))
        return incident_monitor.CommandOutput(0, "")

    first = incident_monitor.collect_incidents_once(
        data_root=tmp_path, runner=runner, now=now
    )
    second = incident_monitor.collect_incidents_once(
        data_root=tmp_path,
        runner=runner,
        now=now + incident_monitor.MONITOR_STALE_TELEMETRY_SECONDS + 1,
    )

    assert first.captured == 0
    assert second.captured == 1
    bundle = tmp_path / "default" / "incidents" / second.incident_ids[0]
    metadata = json.loads((bundle / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["kind"] == "telemetry_hang_suspected"
    assert metadata["confirmed"] is False
    assert metadata["pid"] == 999999
