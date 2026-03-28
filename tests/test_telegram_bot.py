"""Tests for Telegram bot runtime helpers."""

from __future__ import annotations

from armactl.telegram_bot import (
    BotStatusSnapshot,
    admin_chat_allowed,
    render_bot_schedule_text,
    render_bot_status_text,
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
    assert "To change the schedule, send:" in text
    assert "/schedule 05:00, 20:00" in text
