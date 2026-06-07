"""Regression tests for the TUI mods manager."""

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

from textual.app import App, ComposeResult
from textual.widgets import Input, ListView

from armactl.mods_manager import get_mods
from armactl.tui.app import ArmaCtlApp
from armactl.tui.screens import ModManagerScreen, _build_mod_list_item


class ModManagerSmokeApp(App):
    CSS = ArmaCtlApp.CSS

    def __init__(self, config_path: Path, **kwargs):
        super().__init__(**kwargs)
        self.config_path = config_path

    def compose(self) -> ComposeResult:
        yield from ()

    def on_mount(self) -> None:
        self.push_screen(ModManagerScreen("default"))


def _write_config(config_path: Path, mod_count: int) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    mods = [
        {"modId": f"{index:016X}", "name": f"Mod {index}", "version": ""}
        for index in range(mod_count)
    ]
    config_path.write_text(
        json.dumps(
            {
                "bindAddress": "0.0.0.0",
                "bindPort": 2001,
                "publicAddress": "",
                "publicPort": 2001,
                "game": {
                    "name": "Test Server",
                    "scenarioId": "{TEST}Missions/Test.conf",
                    "maxPlayers": 64,
                    "mods": mods,
                },
            },
            indent=4,
        ),
        encoding="utf-8",
    )


def test_mod_list_items_do_not_use_mod_ids_as_dom_ids():
    """Duplicate mod IDs should not produce duplicate Textual widget IDs."""
    first = _build_mod_list_item(
        1,
        {"modId": "60BA2C622B589E22", "name": "Alpha"},
        enabled=True,
    )
    second = _build_mod_list_item(
        2,
        {"modId": "60BA2C622B589E22", "name": "Alpha Again"},
        enabled=False,
    )

    assert first.id is None
    assert second.id is None
    assert getattr(first, "mod_id") == "60BA2C622B589E22"
    assert getattr(second, "mod_id") == "60BA2C622B589E22"
    assert getattr(first, "mod_enabled")
    assert not getattr(second, "mod_enabled")


def test_mod_list_item_labels_render_status_as_plain_text():
    """[active] and mod names with brackets should not be parsed as Rich markup."""
    item = _build_mod_list_item(
        1,
        {"modId": "60BA2C622B589E22", "name": "Name [red]"},
        enabled=True,
    )
    label = item._pending_children[0]

    assert not label._render_markup


def test_tui_add_mod_can_cross_eighty_eight_mods(tmp_path: Path) -> None:
    async def run_case() -> None:
        config_path = tmp_path / "config" / "config.json"
        _write_config(config_path, 88)

        with patch("armactl.tui.screens.paths.config_file", return_value=config_path):
            async with ModManagerSmokeApp(config_path).run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                screen = pilot.app.screen
                screen.query_one("#inp_mod_id", Input).value = f"{88:016X}"

                await pilot.click("#btn_add_mod")
                await pilot.pause()

                list_view = screen.query_one("#mods-list", ListView)
                assert len(get_mods(config_path)) == 89
                assert len(list_view.children) == 89

    asyncio.run(run_case())


def test_tui_add_mod_accepts_bulk_pasted_mod_ids(tmp_path: Path) -> None:
    async def run_case() -> None:
        config_path = tmp_path / "config" / "config.json"
        _write_config(config_path, 88)

        with patch("armactl.tui.screens.paths.config_file", return_value=config_path):
            async with ModManagerSmokeApp(config_path).run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                screen = pilot.app.screen
                screen.query_one("#inp_mod_id", Input).value = (
                    f"https://workshop/{88:016X}\n"
                    f"already-present {1:016X}\n"
                    f"another {89:016X}"
                )

                await pilot.click("#btn_add_mod")
                await pilot.pause()

                list_view = screen.query_one("#mods-list", ListView)
                assert len(get_mods(config_path)) == 90
                assert len(list_view.children) == 90

    asyncio.run(run_case())
