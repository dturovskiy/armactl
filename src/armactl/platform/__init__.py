"""Platform backend adapter boundaries."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from armactl.platform.service_adapter import (
        LinuxSystemdServiceAdapter,
        ServiceAdapter,
        ServiceResult,
    )

__all__ = ["LinuxSystemdServiceAdapter", "ServiceAdapter", "ServiceResult", "get_service_adapter"]


def __getattr__(name: str) -> Any:
    """Load service-adapter exports lazily to keep platform modules independent."""
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from armactl.platform import service_adapter

    return getattr(service_adapter, name)
