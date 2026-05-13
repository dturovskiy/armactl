"""Tests for the root TUI app menu helpers."""

from armactl.state import ServerState
from armactl.tui.app import build_main_menu_entries


def _entry_ids(state: ServerState) -> list[str]:
    return [entry.widget_id for entry in build_main_menu_entries(state)]


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
