"""Tests for TUI dashboard formatting helpers."""

import asyncio
from unittest.mock import patch

from rich.cells import cell_len
from textual.app import App, ComposeResult
from textual.widgets import Button

import armactl.metrics as metrics
from armactl.i18n import _, using_lang
from armactl.tui.app import ArmaCtlApp
from armactl.tui.dashboard import format_player_count, format_usage_bar
from armactl.tui.screens import ManageScreen


class ManageSmokeApp(App):
    CSS = ArmaCtlApp.CSS

    def on_mount(self) -> None:
        self.push_screen(ManageScreen("default"))

    def compose(self) -> ComposeResult:
        yield from ()


def _button_label(button: Button) -> str:
    label = button.label
    return label.plain if hasattr(label, "plain") else str(label)


def _assert_button_label_fits(button: Button) -> None:
    assert button.region.width >= cell_len(_button_label(button)) + 2


def _visible_action_buttons(screen: ManageScreen) -> list[Button]:
    return [
        button
        for button in screen.query("#manage-action-row Button").results(Button)
        if button.display
    ]


def test_format_usage_bar_returns_percentage_bar() -> None:
    assert format_usage_bar(25, 100, width=10) == "[##--------] 25%"


def test_format_usage_bar_clamps_out_of_range_usage() -> None:
    assert format_usage_bar(125, 100, width=10) == "[##########] 100%"
    assert format_usage_bar(-1, 100, width=10) == "[----------] 0%"


def test_format_usage_bar_handles_unavailable_values() -> None:
    assert format_usage_bar(None, 100) == "[----------] Unknown"
    assert format_usage_bar(10, 0) == "[----------] Unknown"


def test_format_player_count() -> None:
    assert format_player_count(0, 128) == "0 / 128"
    assert format_player_count(3, None) == "3"
    assert format_player_count(None, 128) == "Unknown"


def test_manage_yes_no_preserves_unknown_for_non_bool_values() -> None:
    with using_lang("en"):
        assert ManageScreen._yes_no(True) == "Yes"
        assert ManageScreen._yes_no(False) == "No"
        assert ManageScreen._yes_no(None) == "Unknown"
        assert ManageScreen._yes_no("") == "Unknown"


def test_manage_server_fps_lines_render_fresh_metrics() -> None:
    screen = ManageScreen("default")

    with using_lang("en"):
        lines = screen._server_fps_lines(
            metrics.ServerFpsMetrics(
                available=True,
                fps=60.0,
                frame_avg_ms=16.7,
                frame_min_ms=15.3,
                frame_max_ms=17.8,
                age_seconds=8.0,
            )
        )

    assert lines == [
        "Server FPS: 60.0",
        "Frame time avg: 16.7 ms",
        "Frame time max: 17.8 ms",
        "Telemetry age: 8s",
    ]


def test_manage_server_fps_lines_render_stale_and_unavailable_metrics() -> None:
    screen = ManageScreen("default")

    with using_lang("en"):
        stale_lines = screen._server_fps_lines(
            metrics.ServerFpsMetrics(
                available=False,
                fps=60.0,
                frame_avg_ms=16.7,
                frame_max_ms=17.8,
                age_seconds=72.0,
                stale=True,
            )
        )
        unavailable_lines = screen._server_fps_lines(metrics.ServerFpsMetrics(False))

    assert stale_lines == ["Server FPS: stale", "Last telemetry: 1m ago"]
    assert unavailable_lines == ["Server FPS: unavailable"]


def test_manage_navigation_uses_unified_dashboard_tabs() -> None:
    screen = ManageScreen("default")

    assert [item[0] for item in screen._nav_items()] == [
        "overview",
        "config",
        "admins",
        "mods",
        "schedule",
        "bot",
        "cleanup",
        "logs",
        "status",
        "ports",
    ]


def test_manage_context_actions_keep_deep_screens_out_of_primary_tabs() -> None:
    screen = ManageScreen("default")
    screen._active_panel = "config"

    assert [action[0] for action in screen._context_actions()] == [
        "open_config",
        "open_raw_config",
    ]


def test_manage_context_actions_do_not_duplicate_global_refresh() -> None:
    screen = ManageScreen("default")

    for panel, _button_id, _label in screen._nav_items():
        screen._active_panel = panel
        assert "refresh" not in [action[0] for action in screen._context_actions()]


def test_manage_top_bars_fit_english_and_ukrainian_labels() -> None:
    async def run_case(lang: str) -> None:
        with (
            using_lang(lang),
            patch.object(ManageScreen, "action_refresh_state", lambda self: None),
        ):
            async with ManageSmokeApp().run_test(size=(44, 24)) as pilot:
                await pilot.pause()
                screen = pilot.app.screen
                nav = screen.query_one("#manage-nav")
                actions = screen.query_one("#manage-action-row")

                assert nav.virtual_size.width > nav.region.width
                assert actions.virtual_size.width > actions.region.width

                for button in screen.query("#manage-nav Button").results(Button):
                    _assert_button_label_fits(button)

                for panel, _button_id, _label in screen._nav_items():
                    screen._active_panel = panel
                    screen._update_context_actions()
                    await pilot.pause()
                    for button in screen.query("#manage-action-row Button").results(Button):
                        if not button.display:
                            continue
                        _assert_button_label_fits(button)

    for lang in ("en", "uk"):
        asyncio.run(run_case(lang))


def test_manage_context_action_slots_hide_unused_buttons() -> None:
    async def run_case(lang: str) -> None:
        with (
            using_lang(lang),
            patch.object(ManageScreen, "action_refresh_state", lambda self: None),
        ):
            async with ManageSmokeApp().run_test(size=(70, 24)) as pilot:
                await pilot.pause()
                screen = pilot.app.screen

                for panel in ("mods", "schedule", "bot", "cleanup", "logs", "status", "ports"):
                    screen._active_panel = panel
                    screen._update_context_actions()
                    await pilot.pause()

                    visible_labels = [
                        _button_label(button) for button in _visible_action_buttons(screen)
                    ]
                    assert "" not in visible_labels

                    secondary = screen.query_one("#btn_context_secondary", Button)
                    assert not secondary.display
                    assert secondary.disabled
                    assert _button_label(secondary) == ""
                    assert secondary.region.width == 0

    for lang in ("en", "uk"):
        asyncio.run(run_case(lang))


def test_manage_context_action_slots_show_two_action_tabs() -> None:
    async def run_case(lang: str) -> None:
        with (
            using_lang(lang),
            patch.object(ManageScreen, "action_refresh_state", lambda self: None),
        ):
            async with ManageSmokeApp().run_test(size=(70, 24)) as pilot:
                await pilot.pause()
                screen = pilot.app.screen

                for panel in ("overview", "config"):
                    screen._active_panel = panel
                    screen._update_context_actions()
                    await pilot.pause()

                    primary = screen.query_one("#btn_context_primary", Button)
                    secondary = screen.query_one("#btn_context_secondary", Button)
                    assert primary.display
                    assert secondary.display
                    assert not primary.disabled
                    assert not secondary.disabled
                    assert _button_label(primary)
                    assert _button_label(secondary)

    for lang in ("en", "uk"):
        asyncio.run(run_case(lang))


def test_manage_action_row_keeps_one_global_refresh_button() -> None:
    async def run_case(lang: str) -> None:
        with (
            using_lang(lang),
            patch.object(ManageScreen, "action_refresh_state", lambda self: None),
        ):
            async with ManageSmokeApp().run_test(size=(70, 24)) as pilot:
                await pilot.pause()
                screen = pilot.app.screen

                for panel, _button_id, _label in screen._nav_items():
                    screen._active_panel = panel
                    screen._update_context_actions()
                    await pilot.pause()

                    labels = [_button_label(button) for button in _visible_action_buttons(screen)]
                    assert labels.count(_("Refresh")) == 1

                    refresh_button = screen.query_one("#btn_refresh_manage", Button)
                    assert refresh_button.display
                    assert _button_label(refresh_button) == _("Refresh")
                    assert not refresh_button.disabled

                    secondary = screen.query_one("#btn_context_secondary", Button)
                    if len(screen._context_actions()) == 1:
                        assert not secondary.display
                        assert secondary.disabled
                        assert _button_label(secondary) == ""
                        assert secondary.region.width == 0

    for lang in ("en", "uk"):
        asyncio.run(run_case(lang))
