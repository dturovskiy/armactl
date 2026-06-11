"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from armactl.i18n import using_lang


@pytest.fixture(autouse=True)
def default_test_language():
    """Keep tests independent from the user's saved armactl UI language."""
    with using_lang("en"):
        yield
