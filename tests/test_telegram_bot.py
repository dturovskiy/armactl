"""Tests for Telegram bot runtime helpers."""

from armactl.status_summary import (
    ConfigSummary,
    ModsSummary,
    ModSummaryEntry,
)
from armactl.telegram_bot import (
    admin_chat_allowed,
    BotStatusSnapshot,
    parse_friendly_schedule_input,
    render_bot_schedule_text,
    render_bot_status_text,
    render_schedule_input_prompt,
)


def test_admin_chat_allowed():
    assert admin_chat_allowed(123456789, ["123456789", "-1001234567890"]) is True
    assert admin_chat_allowed("-1001234567890", ["123456789", "-1001234567890"]) is True
    assert admin_chat_allowed(42, ["123456789"]) is False


def test_render_bot_status_text_uses_english_fallback():
    snapshot = BotStatusSnapshot(
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
        config_summary=ConfigSummary(
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
        mods_summary=ModsSummary(
            available=True,
            count=4,
            preview=[
                ModSummaryEntry(mod_id="ABC123", name="Weapons Pack"),
                ModSummaryEntry(mod_id="DEF456", name="Vehicles Pack"),
            ],
            remaining_count=2,
        ),
        player_lines=["Denis (#17)", "Vova (#18)"],
        roster_available=True,
    )

    text = render_bot_status_text(snapshot, "en")

    assert "\U0001F916" in text
    assert "\U0001F7E2" in text
    assert "ArmaCtl Telegram Bot [default]" in text
    assert "Server: Running" in text
    assert "Service: armareforger.service" in text
    assert "Service enabled: Yes" in text
    assert "Timer: armareforger-restart.timer" in text
    assert "Current schedule: 08:00, 20:00" in text
    assert "Runtime Metrics" in text
    assert "Main PID: 4321" in text
    assert "Server CPU: 12.5%" in text
    assert "Server RAM: 256.0 MiB" in text
    assert "Config Summary" in text
    assert "Server name: Denis Reforger" in text
    assert "Scenario: {ECC61978EDCC2B5A}Missions/23_Campaign.conf" in text
    assert "Ports: game 2001 / A2S 17777 / RCON 19999" in text
    assert "BattlEye: Yes" in text
    assert "Mods Summary" in text
    assert "Installed mods: 4" in text
    assert "Weapons Pack (ABC123)" in text
    assert "+ 2 more mod(s)" in text
    assert "Players: 3/64" in text
    assert "Denis (#17)" in text
    assert "Vova (#18)" in text


def test_render_bot_schedule_text_uses_english_fallback():
    text = render_bot_schedule_text(
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
    text = render_schedule_input_prompt("08:00, 20:00", "en")

    assert "\u270D\uFE0F" in text
    assert "Send restart times in your next message." in text
    assert "Current schedule: 08:00, 20:00" in text
    assert "Example: 08:00, 20:00 or 06:00 18:00." in text


def test_render_bot_status_text_handles_unavailable_player_query():
    snapshot = BotStatusSnapshot(
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
        config_summary=ConfigSummary(available=False),
        mods_summary=ModsSummary(available=False),
        player_lines=[],
        roster_available=False,
    )

    text = render_bot_status_text(snapshot, "en")

    assert "Players: unavailable" in text
    assert "Server CPU: Unknown" in text
    assert "Server RAM: Unknown" in text
    assert "Config summary unavailable." in text
    assert "Mods summary unavailable." in text


def test_render_bot_status_text_warns_when_roster_is_unavailable():
    snapshot = BotStatusSnapshot(
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
        config_summary=ConfigSummary(available=True),
        mods_summary=ModsSummary(available=True, count=0),
        player_lines=[],
        roster_available=False,
    )

    text = render_bot_status_text(snapshot, "en")

    assert "Players: 2/64" in text
    assert "Player roster unavailable via RCON." in text


def test_parse_friendly_schedule_input_accepts_simple_times():
    assert parse_friendly_schedule_input("08:00, 20:00") == [
        "*-*-* 08:00:00",
        "*-*-* 20:00:00",
    ]
    assert parse_friendly_schedule_input("06:00 18:00") == [
        "*-*-* 06:00:00",
        "*-*-* 18:00:00",
    ]


def test_parse_friendly_schedule_input_rejects_non_time_text():
    assert parse_friendly_schedule_input("tomorrow at six") == []
