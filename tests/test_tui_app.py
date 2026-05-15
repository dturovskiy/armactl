"""Tests for the root TUI app menu helpers."""

import asyncio
from unittest.mock import patch

from rich.cells import cell_len
from textual.widgets import Button

from armactl.i18n import using_lang
from armactl.state import PortInfo, ServerState
from armactl.tui.app import ArmaCtlApp, build_main_menu_entries


def _entry_ids(state: ServerState) -> list[str]:
    return [entry.widget_id for entry in build_main_menu_entries(state)]


def _button_label(button: Button) -> str:
    label = button.label
    return label.plain if hasattr(label, "plain") else str(label)


def _assert_button_label_fits(button: Button) -> None:
    assert button.region.width >= cell_len(_button_label(button)) + 2


def test_main_menu_entries_show_manage_for_installed_server() -> None:
    ids = _entry_ids(ServerState(server_installed=True))

    assert "btn_manage" in ids
    assert "btn_install" not in ids
    assert "install-warning" not in ids


def test_main_menu_entries_show_install_for_missing_server() -> None:
    ids = _entry_ids(ServerState(server_installed=False))

    assert "btn_install" in ids
    assert "btn_manage" not in ids
    assert "install-warning" not in ids


def test_main_menu_entries_warn_before_install_for_partial_server() -> None:
    ids = _entry_ids(ServerState(server_installed=False, binary_exists=True))

    assert ids.index("install-warning") < ids.index("btn_install")
    assert "btn_manage" not in ids


def test_main_menu_entries_keep_unique_widget_ids() -> None:
    ids = _entry_ids(ServerState(server_installed=False, binary_exists=True))

    assert len(ids) == len(set(ids))


def test_main_menu_action_bar_keeps_setup_actions_horizontal() -> None:
    app = ArmaCtlApp()
    buttons = app._main_menu_buttons(ServerState(server_installed=False))

    assert [button.id for button in buttons] == [
        "btn_install",
        "btn_repair",
        "btn_detect",
        "btn_host_tests",
        "btn_lang",
        "btn_exit",
    ]


def test_main_menu_status_summary_uses_dashboard_status_language() -> None:
    app = ArmaCtlApp()
    state = ServerState(
        server_installed=True,
        server_running=True,
        config_exists=True,
        ports=PortInfo(game=2001, a2s=17777, rcon=19999),
    )

    summary = app._main_menu_status_summary(state)

    assert "Installed: Yes" in summary
    assert "Status: Running" in summary
    assert "Ports: game 2001 / A2S 17777 / RCON 19999" in summary


def test_main_action_bar_buttons_fit_english_and_ukrainian_labels() -> None:
    async def run_case(lang: str, state: ServerState) -> None:
        with (
            using_lang(lang),
            patch("armactl.tui.app.discover", return_value=state),
            patch("armactl.tui.app.get_instance_server_name", return_value="Server"),
            patch("armactl.tui.app.ensure_bot_service_runtime", return_value=[]),
        ):
            async with ArmaCtlApp().run_test(size=(44, 24)) as pilot:
                await pilot.pause()
                bar = pilot.app.query_one("#main-action-bar")
                assert bar.virtual_size.width > bar.region.width
                for button in pilot.app.query("#main-action-bar Button").results(Button):
                    _assert_button_label_fits(button)

    for lang in ("en", "uk"):
        asyncio.run(run_case(lang, ServerState(server_installed=False)))
        asyncio.run(run_case(lang, ServerState(server_installed=True)))
