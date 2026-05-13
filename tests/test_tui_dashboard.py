"""Tests for TUI dashboard formatting helpers."""

from armactl.tui.dashboard import format_player_count, format_usage_bar
from armactl.tui.screens import ManageScreen


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
