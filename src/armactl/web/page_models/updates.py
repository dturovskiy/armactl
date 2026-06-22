# Lightweight server-update page model.

from __future__ import annotations

from pathlib import Path
from typing import Any

from armactl import paths
from armactl.state import ServerState, load_state
from armactl.web.services import server_versions


def _state_from_disk(instance: str, data_root: object) -> ServerState:
    data_root_path = Path(data_root)
    state = load_state(paths.state_file(instance, data_root_path))
    if state is None:
        state = ServerState()

    if not state.instance_root:
        state.instance_root = str(paths.instance_root(instance, data_root_path))
    if not state.install_dir:
        state.install_dir = str(paths.server_dir(instance, data_root_path))
    if not state.config_path:
        state.config_path = str(paths.config_file(instance, data_root_path))
    return state


def load_updates_page(
    instance: str,
    *,
    web_config: Any,
) -> dict[str, Any]:
    normalized_instance = paths.validate_instance_name(instance)
    state = _state_from_disk(normalized_instance, web_config.data_root)
    try:
        version_state = server_versions.load_server_version_state(
            instance=normalized_instance,
            state=state,
            db_path=web_config.db_path,
        )
    except Exception as error:  # noqa: BLE001 - page rendering must fail closed.
        version_state = server_versions.failed_server_version_state(
            failure_reason=error,
            server_running=state.server_running,
        )
    return dict(
        instance=normalized_instance,
        server_installed=bool(state.server_installed),
        server_running=bool(version_state.server_running),
        version=version_state.to_dict(),
    )
