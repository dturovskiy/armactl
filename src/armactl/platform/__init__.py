"""Platform backend adapter boundaries."""

from __future__ import annotations

from armactl.platform.service_adapter import (
    LinuxSystemdServiceAdapter,
    ServiceAdapter,
    ServiceResult,
    get_service_adapter,
)

__all__ = ["LinuxSystemdServiceAdapter", "ServiceAdapter", "ServiceResult", "get_service_adapter"]
