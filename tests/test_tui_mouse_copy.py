"""Tests for terminal mouse-copy helpers."""

from __future__ import annotations

from io import StringIO

from armactl.tui import app


def test_terminal_mouse_tracking_helpers_write_expected_sequences(monkeypatch) -> None:
    output = StringIO()
    monkeypatch.setattr(app.sys, "stdout", output)

    app._disable_terminal_mouse_tracking()
    assert output.getvalue() == app.TERMINAL_MOUSE_RESET_SEQUENCES

    output.seek(0)
    output.truncate(0)

    app._enable_terminal_mouse_tracking()
    assert output.getvalue() == app.TERMINAL_MOUSE_ENABLE_SEQUENCES


def test_mouse_selection_env_flag(monkeypatch) -> None:
    monkeypatch.delenv("ARMACTL_TUI_MOUSE_SELECTION", raising=False)
    assert not app._mouse_selection_requested()

    monkeypatch.setenv("ARMACTL_TUI_MOUSE_SELECTION", "yes")
    assert app._mouse_selection_requested()
