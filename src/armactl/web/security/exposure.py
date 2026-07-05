"""Safe exposure warnings for web bind settings."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from ipaddress import ip_address
from typing import Any

SAFE_BIND_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
EXTERNAL_BIND_WITHOUT_HTTPS_WARNING = (
    "External bind without HTTPS-required cookies. Use only with a documented "
    "gateway/firewall/VPN profile, or serve the browser over HTTPS and set "
    "ARMACTL_WEB_HTTPS_REQUIRED=true. HTTPS_REQUIRED only sets the Secure "
    "cookie flag; it does not protect the bind or gateway port."
)
EXTERNAL_BIND_WARNING = (
    "External bind detected. Ensure a documented gateway/firewall/VPN/HTTPS "
    "profile protects access. HTTPS_REQUIRED only controls Secure cookies, not "
    "the bind or gateway mapping."
)


@dataclass(frozen=True)
class ExposureWarning:
    """Operator-facing warning about web panel exposure."""

    severity: str
    message: str
    external: bool
    https_required: bool
    bind_host: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def is_external_bind(bind_host: str) -> bool:
    """Return True when a bind host may expose the panel beyond localhost."""
    value = str(bind_host or "").strip()
    normalized = value.casefold()
    if normalized in SAFE_BIND_HOSTS:
        return False

    try:
        parsed = ip_address(value)
    except ValueError:
        return True

    return not parsed.is_loopback


def get_exposure_warning(
    bind_host: str,
    https_required: bool,
) -> ExposureWarning | None:
    """Return an operator-facing warning for unsafe/external web bind settings."""
    if not is_external_bind(bind_host):
        return None

    if https_required:
        return ExposureWarning(
            severity="warning",
            message=EXTERNAL_BIND_WARNING,
            external=True,
            https_required=True,
            bind_host=str(bind_host or ""),
        )

    return ExposureWarning(
        severity="danger",
        message=EXTERNAL_BIND_WITHOUT_HTTPS_WARNING,
        external=True,
        https_required=False,
        bind_host=str(bind_host or ""),
    )
