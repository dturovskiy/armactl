"""Tests for Telegram bot runtime helpers."""

import types
import unittest.mock as mock

import armactl.metrics as metrics
import armactl.status_summary as status_summary
import armactl.telegram_bot as telegram_bot


def test_admin_chat_allowed():
    assert telegram_bot.admin_chat_allowed(123456789, ["123456789", "-1001234567890"]) is True
    assert (
        telegram_bot.admin_chat_allowed("-1001234567890", ["123456789", "-1001234567890"])
        is True
    )
    assert telegram_bot.admin_chat_allowed(42, ["123456789"]) is False


def test_render_bot_status_text_uses_english_fallback():
    snapshot = telegram_bot.BotStatusSnapshot(
        instance="default",
        server_running=True,
        service_name="armareforger.service",
        service_active_state="active",
        service_enabled=True,
        timer_name="armareforger-restart.timer",
        schedule="08:00, 20:00",
        next_run="2026-03-29 08:00:00 UTC",
        player_count=3,
        max_players=64,
        main_pid=4321,
        cpu_percent=12.5,
        memory_rss_bytes=268435456,
        config_summary=status_summary.ConfigSummary(
            available=True,
            server_name="Denis Reforger",
            scenario_id="{ECC61978EDCC2B5A}Missions/23_Campaign.conf",
            max_players=64,
            bind_port=2001,
            a2s_port=17777,
            rcon_port=19999,
            visible=True,
            battleye=True,
        ),
        mods_summary=status_summary.ModsSummary(
            available=True,
            count=4,
            preview=[
                status_summary.ModSummaryEntry(mod_id="ABC123", name="Weapons Pack"),
                status_summary.ModSummaryEntry(mod_id="DEF456", name="Vehicles Pack"),
            ],
            remaining_count=2,
        ),
        player_lines=["Denis (#17)", "Vova (#18)"],
        roster_available=True,
    )

    text = telegram_bot.render_bot_status_text(snapshot, "en")

    assert "\U0001F916" in text
    assert "\U0001F7E2" in text
    assert "ArmaCtl Telegram Bot [default]" in text
    assert "Server: Running" in text
    assert "Service: armareforger.service" in text
    assert "Service enabled: Yes" in text
    assert "Timer: armareforger-restart.timer" in text
    assert "Current schedule: 08:00, 20:00" in text
    assert "Players: 3/64" in text
    assert "Denis (#17)" not in text
    assert "Vova (#18)" not in text


def test_render_bot_metrics_text_uses_english_fallback():
    snapshot = telegram_bot.BotStatusSnapshot(
        instance="default",
        server_running=True,
        service_name="armareforger.service",
        service_active_state="active",
        service_enabled=True,
        timer_name="armareforger-restart.timer",
        schedule="08:00, 20:00",
        next_run="2026-03-29 08:00:00 UTC",
        player_count=3,
        max_players=64,
        main_pid=4321,
        cpu_percent=12.5,
        memory_rss_bytes=268435456,
        host_metrics=metrics.HostMetrics(
            available=True,
            cpu_percent=37.5,
            memory_used_bytes=3221225472,
            memory_total_bytes=8589934592,
            disk_used_bytes=21474836480,
            disk_total_bytes=107374182400,
            load_average_1m=0.75,
            load_average_5m=0.5,
            load_average_15m=0.25,
            uptime_seconds=93784,
        ),
    )

    text = telegram_bot.render_bot_metrics_text(snapshot, "en")

    assert "Metrics: default" in text
    assert "Runtime Metrics" in text
    assert "Main PID: 4321" in text
    assert "Server CPU: 12.5%" in text
    assert "Server RAM: 256.0 MiB" in text
    assert "Host / VM Metrics" in text
    assert "Host CPU: 37.5%" in text
    assert "Host RAM: 3.0 GiB / 8.0 GiB" in text
    assert "Host Disk: 20.0 GiB / 100.0 GiB" in text
    assert "Host Load Avg: 0.75 / 0.50 / 0.25" in text
    assert "Host uptime: 1d 2h 3m" in text


def test_render_bot_details_text_uses_english_fallback():
    snapshot = telegram_bot.BotStatusSnapshot(
        instance="default",
        server_running=True,
        service_name="armareforger.service",
        service_active_state="active",
        service_enabled=True,
        timer_name="armareforger-restart.timer",
        schedule="08:00, 20:00",
        next_run="2026-03-29 08:00:00 UTC",
        player_count=3,
        max_players=64,
        config_summary=status_summary.ConfigSummary(
            available=True,
            server_name="Denis Reforger",
            scenario_id="{ECC61978EDCC2B5A}Missions/23_Campaign.conf",
            max_players=64,
            bind_port=2001,
            a2s_port=17777,
            rcon_port=19999,
            visible=True,
            battleye=True,
        ),
        mods_summary=status_summary.ModsSummary(
            available=True,
            count=4,
            preview=[
                status_summary.ModSummaryEntry(mod_id="ABC123", name="Weapons Pack"),
                status_summary.ModSummaryEntry(mod_id="DEF456", name="Vehicles Pack"),
            ],
            remaining_count=2,
        ),
        player_lines=["Denis (#17)", "Vova (#18)"],
        roster_available=True,
        roster_configured=True,
    )

    text = telegram_bot.render_bot_details_text(snapshot, "en")

    assert "Server Details: default" in text
    assert "Config Summary" in text
    assert "Server name: Denis Reforger" in text
    assert "Scenario: {ECC61978EDCC2B5A}Missions/23_Campaign.conf" in text
    assert "Ports: game 2001 / A2S 17777 / RCON 19999" in text
    assert "BattlEye: Yes" in text
    assert "Mods Summary" in text
    assert "Installed mods: 4" in text
    assert "Weapons Pack (ABC123)" in text
    assert "+ 2 more mod(s)" in text
    assert "Player Roster" in text
    assert "Players: 3/64" in text
    assert "Denis (#17)" in text
    assert "Vova (#18)" in text


def test_render_bot_schedule_text_uses_english_fallback():
    text = telegram_bot.render_bot_schedule_text(
        "default",
        {
            "enabled": True,
            "schedule": "08:00, 20:00",
            "next_run": "2026-03-29 08:00:00 UTC",
        },
        "en",
    )

    assert "\u23F0" in text
    assert "\u270D\uFE0F" in text
    assert "Restart Schedule: default" in text
    assert "Enabled: Yes" in text
    assert "Current schedule: 08:00, 20:00" in text
    assert "To change the schedule:" in text
    assert "Press Change Time, then send something like 08:00, 20:00." in text


def test_render_schedule_input_prompt_uses_english_fallback():
    text = telegram_bot.render_schedule_input_prompt("08:00, 20:00", "en")

    assert "\u270D\uFE0F" in text
    assert "Send restart times in your next message." in text
    assert "Current schedule: 08:00, 20:00" in text
    assert "Example: 08:00, 20:00 or 06:00 18:00." in text


def test_render_bot_status_text_handles_unavailable_player_query():
    snapshot = telegram_bot.BotStatusSnapshot(
        instance="default",
        server_running=True,
        service_name="armareforger.service",
        service_active_state="active",
        service_enabled=True,
        timer_name="armareforger-restart.timer",
        schedule="08:00, 20:00",
        next_run="2026-03-29 08:00:00 UTC",
        player_count=None,
        max_players=64,
        main_pid=0,
        cpu_percent=None,
        memory_rss_bytes=None,
        config_summary=status_summary.ConfigSummary(available=False),
        mods_summary=status_summary.ModsSummary(available=False),
        player_lines=[],
        roster_available=False,
    )

    text = telegram_bot.render_bot_status_text(snapshot, "en")

    assert "Players: unavailable" in text


def test_render_bot_status_text_keeps_rcon_roster_errors_out_of_main_status():
    snapshot = telegram_bot.BotStatusSnapshot(
        instance="default",
        server_running=True,
        service_name="armareforger.service",
        service_active_state="active",
        service_enabled=True,
        timer_name="armareforger-restart.timer",
        schedule="08:00, 20:00",
        next_run="2026-03-29 08:00:00 UTC",
        player_count=2,
        max_players=64,
        main_pid=123,
        cpu_percent=4.0,
        memory_rss_bytes=134217728,
        config_summary=status_summary.ConfigSummary(available=True),
        mods_summary=status_summary.ModsSummary(available=True, count=0),
        player_lines=[],
        roster_available=False,
        roster_configured=True,
        roster_error="RCON command timed out.",
    )

    text = telegram_bot.render_bot_status_text(snapshot, "en")

    assert "Players: 2/64" in text
    assert "Player roster unavailable" not in text


def test_render_bot_details_text_explains_roster_failures():
    snapshot = telegram_bot.BotStatusSnapshot(
        instance="default",
        server_running=True,
        service_name="armareforger.service",
        service_active_state="active",
        service_enabled=True,
        timer_name="armareforger-restart.timer",
        schedule="08:00, 20:00",
        next_run="2026-03-29 08:00:00 UTC",
        player_count=2,
        max_players=64,
        config_summary=status_summary.ConfigSummary(available=True),
        mods_summary=status_summary.ModsSummary(available=True, count=0),
        player_lines=[],
        roster_available=False,
        roster_configured=True,
        roster_error="RCON command timed out.",
    )

    text = telegram_bot.render_bot_details_text(snapshot, "en")

    assert "Player Roster" in text
    assert "Players: 2/64" in text
    assert "Player roster unavailable: RCON command timed out." in text
    assert "Check local RCON address, port, and password." in text


def test_bot_snapshot_cache_reuses_view_data_until_refresh():
    config = types.SimpleNamespace(
        instance="default",
        language="en",
        enabled=True,
        token="token",
        admin_chat_ids=["1"],
    )
    snapshot = telegram_bot.BotStatusSnapshot(
        instance="default",
        server_running=True,
        service_name="armareforger.service",
        service_active_state="active",
        service_enabled=True,
        timer_name="armareforger-restart.timer",
        schedule="08:00, 20:00",
        next_run="2026-03-29 08:00:00 UTC",
        player_count=0,
        max_players=64,
    )

    with mock.patch("armactl.telegram_bot.load_bot_config", return_value=config):
        bot = telegram_bot.ArmaCtlTelegramBot("default")

    with mock.patch("armactl.telegram_bot._build_status_snapshot", return_value=snapshot) as build:
        bot._status_text()
        bot._status_text()
        bot._metrics_text()
        bot._metrics_text()
        bot._details_text()
        bot._details_text()
        bot._status_text(force_refresh=True)

    assert build.call_count == 4
    assert build.call_args_list[0] == mock.call("default")
    assert build.call_args_list[1] == mock.call(
        "default",
        include_runtime_metrics=True,
        include_host_metrics=True,
    )
    assert build.call_args_list[2] == mock.call(
        "default",
        include_summaries=True,
        include_roster=True,
    )
    assert build.call_args_list[3] == mock.call("default")


def test_parse_friendly_schedule_input_accepts_simple_times():
    assert telegram_bot.parse_friendly_schedule_input("08:00, 20:00") == [
        "*-*-* 08:00:00",
        "*-*-* 20:00:00",
    ]
    assert telegram_bot.parse_friendly_schedule_input("06:00 18:00") == [
        "*-*-* 06:00:00",
        "*-*-* 18:00:00",
    ]


def test_parse_friendly_schedule_input_rejects_non_time_text():
    assert telegram_bot.parse_friendly_schedule_input("tomorrow at six") == []
