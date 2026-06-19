"""Service and timer backend adapter boundary.

The current production backend is Linux/systemd through ``service_manager``.
CLI and TUI still import that module directly for compatibility, while newer
web service/timer workflows depend on this narrower adapter seam.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from armactl import paths, service_manager
from armactl.service_manager import ServiceResult


class ServiceAdapter(Protocol):
    """Backend contract for service and restart-timer operations."""

    def start_service(self, service_name: str = paths.SERVICE_NAME) -> ServiceResult:
        """Start a service unit or equivalent backend target."""
        ...

    def stop_service(self, service_name: str = paths.SERVICE_NAME) -> ServiceResult:
        """Stop a service unit or equivalent backend target."""
        ...

    def restart_service(self, service_name: str = paths.SERVICE_NAME) -> ServiceResult:
        """Restart a service unit or equivalent backend target."""
        ...

    def enable_service(self, service_name: str) -> ServiceResult:
        """Enable a service/timer unit or equivalent backend target."""
        ...

    def disable_service(self, service_name: str) -> ServiceResult:
        """Disable a service/timer unit or equivalent backend target."""
        ...

    def update_restart_timer_schedule(
        self,
        instance: str = paths.DEFAULT_INSTANCE_NAME,
        on_calendar: str | list[str] = "*-*-* 06:00:00",
    ) -> list[ServiceResult]:
        """Update the restart timer schedule for an instance."""
        ...

    def get_service_status(self, service_name: str = paths.SERVICE_NAME) -> dict[str, Any]:
        """Return service status for page models and diagnostics."""
        ...

    def get_timer_status(self, timer_name: str = paths.TIMER_NAME) -> dict[str, Any]:
        """Return timer status for page models and diagnostics."""
        ...

    def service_unit_name(self, instance: str = paths.DEFAULT_INSTANCE_NAME) -> str:
        """Return the main game service backend target for an instance."""
        ...

    def restart_service_unit_name(self, instance: str = paths.DEFAULT_INSTANCE_NAME) -> str:
        """Return the restart-now backend target for an instance."""
        ...

    def timer_unit_name(self, instance: str = paths.DEFAULT_INSTANCE_NAME) -> str:
        """Return the restart timer backend target for an instance."""
        ...

    def format_schedule_for_input(self, schedule_entries: list[str]) -> str:
        """Convert backend schedule entries to the compact web input format."""
        ...


@dataclass(frozen=True)
class LinuxSystemdServiceAdapter:
    """Default adapter backed by the existing Linux/systemd service manager."""

    def start_service(self, service_name: str = paths.SERVICE_NAME) -> ServiceResult:
        return service_manager.start_service(service_name)

    def stop_service(self, service_name: str = paths.SERVICE_NAME) -> ServiceResult:
        return service_manager.stop_service(service_name)

    def restart_service(self, service_name: str = paths.SERVICE_NAME) -> ServiceResult:
        return service_manager.restart_service(service_name)

    def enable_service(self, service_name: str) -> ServiceResult:
        return service_manager.enable_service(service_name)

    def disable_service(self, service_name: str) -> ServiceResult:
        return service_manager.disable_service(service_name)

    def update_restart_timer_schedule(
        self,
        instance: str = paths.DEFAULT_INSTANCE_NAME,
        on_calendar: str | list[str] = "*-*-* 06:00:00",
    ) -> list[ServiceResult]:
        return service_manager.update_restart_timer_schedule(
            instance=instance,
            on_calendar=on_calendar,
        )

    def get_service_status(self, service_name: str = paths.SERVICE_NAME) -> dict[str, Any]:
        return service_manager.get_service_status(service_name)

    def get_timer_status(self, timer_name: str = paths.TIMER_NAME) -> dict[str, Any]:
        return service_manager.get_timer_status(timer_name)

    def service_unit_name(self, instance: str = paths.DEFAULT_INSTANCE_NAME) -> str:
        return service_manager.service_unit_name(instance)

    def restart_service_unit_name(self, instance: str = paths.DEFAULT_INSTANCE_NAME) -> str:
        return service_manager.restart_service_unit_name(instance)

    def timer_unit_name(self, instance: str = paths.DEFAULT_INSTANCE_NAME) -> str:
        return service_manager.timer_unit_name(instance)

    def format_schedule_for_input(self, schedule_entries: list[str]) -> str:
        return service_manager.format_schedule_for_input(schedule_entries)


_DEFAULT_SERVICE_ADAPTER: ServiceAdapter = LinuxSystemdServiceAdapter()


def get_service_adapter() -> ServiceAdapter:
    """Return the process default service/timer backend adapter."""
    return _DEFAULT_SERVICE_ADAPTER
