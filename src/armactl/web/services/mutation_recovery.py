"""Shared recovery helpers for post-mutation web bookkeeping."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from armactl import paths
from armactl.web.services import pending_work


@dataclass(frozen=True)
class RestartPendingRecovery:
    """Inputs needed to leave an operator-visible restart marker after mutation."""

    db_path: Path | None
    kind: str
    source_action: str
    username: str
    details: object = ""
    instance: str = paths.DEFAULT_INSTANCE_NAME
    source_path: str | None = None
    title: str | None = None
    baseline_fingerprint: str = ""
    current_fingerprint: str = ""
    required: bool = True


def mark_restart_pending_for_mutation(
    recovery: RestartPendingRecovery,
) -> pending_work.PendingWorkWriteResult:
    """Record restart-required recovery work with fallback sidecar protection.

    This helper is intentionally narrow: callers still own validation, intent audit,
    backup/stage/snapshot, apply, verify, and outcome audit. It centralizes the
    post-mutation recovery marker so a bookkeeping exception does not erase the
    operator's recovery handle after state has already changed.
    """
    if recovery.db_path is None or not recovery.required:
        return pending_work.PendingWorkWriteResult()

    try:
        if recovery.baseline_fingerprint and recovery.current_fingerprint:
            return pending_work.mark_restart_pending_for_state(
                recovery.db_path,
                instance=recovery.instance,
                kind=recovery.kind,
                source_action=recovery.source_action,
                source_path=recovery.source_path,
                title=recovery.title,
                username=recovery.username,
                details=recovery.details,
                baseline_fingerprint=recovery.baseline_fingerprint,
                current_fingerprint=recovery.current_fingerprint,
            )
        return pending_work.mark_restart_pending_for_service(
            recovery.db_path,
            instance=recovery.instance,
            kind=recovery.kind,
            source_action=recovery.source_action,
            source_path=recovery.source_path,
            title=recovery.title,
            username=recovery.username,
            details=recovery.details,
        )
    except Exception:  # noqa: BLE001 - backend mutation already happened; leave marker.
        return _mark_restart_pending_fallback(recovery)


def _mark_restart_pending_fallback(
    recovery: RestartPendingRecovery,
) -> pending_work.PendingWorkWriteResult:
    try:
        pending_work.mark_restart_pending_fallback(
            recovery.db_path,
            instance=recovery.instance,
            kind=recovery.kind,
            source_action=recovery.source_action,
            source_path=recovery.source_path,
            title=recovery.title,
            username=recovery.username,
            details=recovery.details,
            baseline_fingerprint=recovery.baseline_fingerprint,
            current_fingerprint=recovery.current_fingerprint,
        )
    except Exception:  # noqa: BLE001 - both stores failed after mutation.
        return pending_work.PendingWorkWriteResult(
            error=pending_work.PENDING_WORK_STORAGE_FAILED_MESSAGE,
        )
    return pending_work.PendingWorkWriteResult(
        warning=pending_work.PENDING_WORK_FALLBACK_WARNING,
    )
