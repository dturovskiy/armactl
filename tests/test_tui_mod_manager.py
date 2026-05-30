"""Regression tests for the TUI mods manager."""

from armactl.tui.screens import _build_mod_list_item


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
