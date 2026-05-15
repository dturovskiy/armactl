"""Tests for TUI dashboard formatting helpers."""

import asyncio
from unittest.mock import patch

from rich.cells import cell_len
from textual.app import App, ComposeResult
from textual.widgets import Button

from armactl.i18n import using_lang
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
    assert ManageScreen._yes_no(True) == "Yes"
    assert ManageScreen._yes_no(False) == "No"
    assert ManageScreen._yes_no(None) == "Unknown"
    assert ManageScreen._yes_no("") == "Unknown"


def test_manage_navigation_uses_unified_dashboard_tabs() -> None:
    screen = ManageScreen("default")

    assert [item[0] for item in screen._nav_items()] == [
        "overview",
        "config",
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
                        _assert_button_label_fits(button)

    for lang in ("en", "uk"):
        asyncio.run(run_case(lang))
