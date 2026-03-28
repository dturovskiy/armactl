"""Tests for Telegram bot runtime helpers."""

from __future__ import annotations

from armactl.telegram_bot import (
    BotStatusSnapshot,
    admin_chat_allowed,
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
    assert "Players: not implemented yet." in text


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
