"""Tests for platform service adapter invariants."""

from __future__ import annotations

from armactl import paths
from armactl.platform.service_adapter import LinuxSystemdServiceAdapter, get_service_adapter


def test_default_service_adapter_is_linux_systemd_backend() -> None:
    assert isinstance(get_service_adapter(), LinuxSystemdServiceAdapter)


def test_linux_systemd_service_adapter_preserves_unit_names() -> None:
    adapter = LinuxSystemdServiceAdapter()

    assert adapter.service_unit_name() == paths.SERVICE_NAME
    assert adapter.restart_service_unit_name() == paths.RESTART_SERVICE_NAME
    assert adapter.timer_unit_name() == paths.TIMER_NAME
    assert adapter.service_unit_name("alpha") == "armareforger@alpha.service"
    assert adapter.restart_service_unit_name("alpha") == "armareforger-restart@alpha.service"
    assert adapter.timer_unit_name("alpha") == "armareforger-restart@alpha.timer"
