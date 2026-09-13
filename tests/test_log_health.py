"""Tests for bounded active Enfusion log anomaly checks."""

from __future__ import annotations

from pathlib import Path

from armactl import log_health


def _active_logs(tmp_path: Path) -> tuple[Path, Path]:
    run = tmp_path / "config" / "logs" / "logs_2026-09-13_20-00-00"
    run.mkdir(parents=True)
    console = run / "console.log"
    console.write_text("healthy\n", encoding="utf-8")
    return tmp_path / "config", console


def test_clean_active_generation_reports_only_allowlisted_file_names(tmp_path: Path) -> None:
    config_dir, console = _active_logs(tmp_path)
    (console.parent / "error.log").write_text("healthy\n", encoding="utf-8")
    (console.parent / "script.log").write_text("healthy\n", encoding="utf-8")
    (console.parent / "private.log").write_text("password=secret\n", encoding="utf-8")

    result = log_health.query_active_log_health(config_dir, active_console=console)

    assert result.available is True
    assert result.checked_files == ("console.log", "error.log", "script.log")
    assert result.anomalies == ()
    assert str(config_dir) not in str(result.to_dict())
    assert "private.log" not in result.checked_files


def test_large_file_is_reported_from_metadata_with_a_bounded_tail(tmp_path: Path) -> None:
    config_dir, console = _active_logs(tmp_path)
    console.write_bytes(b"x" * 4096)

    result = log_health.query_active_log_health(
        config_dir,
        active_console=console,
        large_threshold_bytes=1024,
        tail_sample_bytes=128,
    )

    assert len(result.anomalies) == 1
    anomaly = result.anomalies[0]
    assert anomaly.name == "console.log"
    assert anomaly.size_bytes == 4096
    assert anomaly.large is True
    assert anomaly.spam is False
    assert anomaly.sampled_bytes == 128


def test_repeated_known_error_signature_is_reported_without_raw_lines(tmp_path: Path) -> None:
    config_dir, console = _active_logs(tmp_path)
    console.write_text(
        "\n".join(["Virtual Machine Exception: private player detail"] * 25),
        encoding="utf-8",
    )

    result = log_health.query_active_log_health(
        config_dir,
        active_console=console,
        large_threshold_bytes=1024 * 1024,
    )

    assert len(result.anomalies) == 1
    anomaly = result.anomalies[0]
    assert anomaly.spam is True
    assert anomaly.spam_signal == "virtual_machine_exception"
    assert anomaly.spam_matches == 25
    assert "private player detail" not in str(result.to_dict())


def test_spam_detection_reads_only_the_bounded_tail(tmp_path: Path) -> None:
    config_dir, console = _active_logs(tmp_path)
    console.write_bytes(
        (b"Virtual Machine Exception\n" * 30)
        + (b"current healthy telemetry\n" * 100)
    )

    result = log_health.query_active_log_health(
        config_dir,
        active_console=console,
        large_threshold_bytes=1024 * 1024,
        tail_sample_bytes=256,
    )

    assert result.anomalies == ()
    assert result.tail_sample_bytes == 256


def test_untrusted_or_nested_console_path_is_rejected(tmp_path: Path) -> None:
    config_dir, console = _active_logs(tmp_path)
    outside = tmp_path / "outside" / "console.log"
    outside.parent.mkdir()
    outside.write_text("Virtual Machine Exception\n" * 30, encoding="utf-8")
    nested = console.parent / "nested" / "console.log"
    nested.parent.mkdir()
    nested.write_text("Virtual Machine Exception\n" * 30, encoding="utf-8")

    outside_result = log_health.query_active_log_health(
        config_dir,
        active_console=outside,
    )
    nested_result = log_health.query_active_log_health(
        config_dir,
        active_console=nested,
    )

    assert outside_result.available is False
    assert nested_result.available is False
    assert outside_result.checked_files == ()
    assert str(outside) not in str(outside_result.to_dict())


def test_symlinked_log_is_not_followed(tmp_path: Path) -> None:
    config_dir, console = _active_logs(tmp_path)
    target = tmp_path / "secret-error.log"
    target.write_text("Virtual Machine Exception\n" * 30, encoding="utf-8")
    (console.parent / "error.log").symlink_to(target)

    result = log_health.query_active_log_health(config_dir, active_console=console)

    assert result.available is True
    assert result.checked_files == ("console.log",)
    assert result.anomalies == ()
