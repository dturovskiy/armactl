"""Verified service workflow for typed native ban and unban actions."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

from armactl import paths, rcon
from armactl.web.services import moderation_verification
from armactl.web.services.audit import AuditLogError, append_audit_event
from armactl.web.services.player_identity import normalize_reliable_player_id

ACTION_BAN = rcon.NATIVE_MODERATION_ACTION_BAN
ACTION_UNBAN = rcon.NATIVE_MODERATION_ACTION_UNBAN
SUPPORTED_ACTIONS = frozenset({ACTION_BAN, ACTION_UNBAN})

CLASSIFICATION_CHANGED = "changed"
CLASSIFICATION_NOOP = "noop"
CLASSIFICATION_FAILED = "failed"
CLASSIFICATION_UNCERTAIN = "uncertain"

AUDIT_ACTIONS = {
    ACTION_BAN: "players.ban",
    ACTION_UNBAN: "players.unban",
}
AUTHORITATIVE_LIST_TIMEOUT_SECONDS = 10.0
ERROR_AUTHORITATIVE_UNAVAILABLE = "authoritative_list_unavailable"
ERROR_DURATION_MISMATCH = "existing_duration_mismatch"
ERROR_INTENT_AUDIT = "intent_audit_failed"
ERROR_OUTCOME_AUDIT = "outcome_audit_failed"
ERROR_RECOVERY_STORAGE = "recovery_storage_failed"
ERROR_RECOVERY_NOT_FOUND = "recovery_not_found"

_LOCKS_GUARD = threading.Lock()
_INSTANCE_LOCKS: dict[str, threading.Lock] = {}


class NativeModerationError(ValueError):
    """Raised when a native moderation request is invalid."""


@dataclass(frozen=True)
class AuthoritativeBanSnapshot:
    """One bounded complete native list used only inside a locked workflow."""

    complete: bool
    entries: tuple[rcon.NativeBanEntry, ...] = ()
    error_code: str = ""
    error: str = ""


@dataclass(frozen=True)
class NativeModerationResult:
    """Controlled service result with no raw command or RCON response."""

    action: str
    instance: str
    target_identity: str
    classification: str
    success: bool
    changed: bool
    message: str
    command_status: str = ""
    error_code: str = ""
    intent_audited: bool = True
    audit_written: bool = True
    baseline_complete: bool = False
    verification_complete: bool = False
    recovery_record_id: int | None = None
    recovery_state: str = ""
    recovery_error: str = ""

    @property
    def uncertain(self) -> bool:
        return self.classification == CLASSIFICATION_UNCERTAIN

    @property
    def status_label(self) -> str:
        return "success" if self.success else "failure"


def _instance_lock(instance: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _INSTANCE_LOCKS.setdefault(instance, threading.Lock())


@contextmanager
def moderation_lock(instance: str) -> Iterator[None]:
    """Serialize native list and mutation workflows for one instance."""
    normalized_instance = paths.validate_instance_name(instance)
    with _instance_lock(normalized_instance):
        yield


def _normalize_action(action: object) -> str:
    normalized = str(action or "").strip().casefold()
    if normalized not in SUPPORTED_ACTIONS:
        raise NativeModerationError("Unknown native moderation action.")
    return normalized


def _normalize_target(target_identity: object) -> str:
    target = normalize_reliable_player_id(target_identity)
    if not target:
        raise NativeModerationError("A reliable player identity is required for native moderation.")
    try:
        return rcon.normalize_native_ban_target(target)
    except ValueError as exc:
        raise NativeModerationError(
            "A reliable player identity is required for native moderation."
        ) from exc


def _normalize_duration(action: str, duration_seconds: object) -> int:
    if action == ACTION_UNBAN:
        return 0
    try:
        return rcon.normalize_native_ban_duration(duration_seconds)
    except ValueError as exc:
        raise NativeModerationError("Native ban duration is invalid.") from exc


def _normalize_reason(action: str, reason: object) -> str:
    if action == ACTION_UNBAN:
        return ""
    try:
        return rcon.normalize_native_ban_reason(reason)
    except ValueError as exc:
        raise NativeModerationError("Native ban reason is invalid.") from exc


def _reason_class(reason: str) -> str:
    return (
        moderation_verification.REASON_CLASS_PROVIDED
        if reason
        else moderation_verification.REASON_CLASS_NONE
    )


def _same_identity(left: object, right: object) -> bool:
    return str(left or "").strip().casefold() == str(right or "").strip().casefold()


def _target_entries(
    snapshot: AuthoritativeBanSnapshot,
    target_identity: str,
) -> tuple[rcon.NativeBanEntry, ...]:
    return tuple(
        entry for entry in snapshot.entries if _same_identity(entry.player_uid, target_identity)
    )


def _read_authoritative_ban_snapshot(
    instance: str,
    *,
    timeout: float = AUTHORITATIVE_LIST_TIMEOUT_SECONDS,
) -> AuthoritativeBanSnapshot:
    """Read every bounded native page or fail closed without partial truth."""
    deadline = time.monotonic() + max(0.1, float(timeout))
    entries: list[rcon.NativeBanEntry] = []
    native_ids: set[str] = set()

    for page in range(rcon.NATIVE_BAN_MIN_PAGE, rcon.NATIVE_BAN_MAX_PAGE + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return AuthoritativeBanSnapshot(
                complete=False,
                error_code=rcon.NATIVE_BAN_ERROR_TIMEOUT,
                error="Authoritative native ban-list verification timed out.",
            )
        result = rcon.query_native_ban_list(
            instance,
            page=page,
            timeout=min(rcon.RCON_NATIVE_BAN_TIMEOUT_SECONDS, remaining),
        )
        if not result.complete:
            return AuthoritativeBanSnapshot(
                complete=False,
                error_code=result.error_code or ERROR_AUTHORITATIVE_UNAVAILABLE,
                error=result.error or "Authoritative native ban list is unavailable.",
            )
        for entry in result.entries:
            if entry.native_ban_id in native_ids:
                return AuthoritativeBanSnapshot(
                    complete=False,
                    error_code=rcon.NATIVE_BAN_ERROR_MALFORMED_RESPONSE,
                    error="Authoritative native ban list contained duplicate rows.",
                )
            native_ids.add(entry.native_ban_id)
            entries.append(entry)
        if len(result.entries) < rcon.NATIVE_BAN_PAGE_SIZE:
            return AuthoritativeBanSnapshot(
                complete=True,
                entries=tuple(entries),
            )

    return AuthoritativeBanSnapshot(
        complete=False,
        error_code=rcon.NATIVE_BAN_ERROR_MALFORMED_RESPONSE,
        error="Authoritative native ban list exceeded the bounded page limit.",
    )


def _result(
    *,
    action: str,
    instance: str,
    target_identity: str,
    classification: str,
    message: str,
    command_status: str = "",
    error_code: str = "",
    baseline_complete: bool = False,
    verification_complete: bool = False,
) -> NativeModerationResult:
    return NativeModerationResult(
        action=action,
        instance=instance,
        target_identity=target_identity,
        classification=classification,
        success=classification in {CLASSIFICATION_CHANGED, CLASSIFICATION_NOOP},
        changed=classification == CLASSIFICATION_CHANGED,
        message=message,
        command_status=command_status,
        error_code=error_code,
        baseline_complete=baseline_complete,
        verification_complete=verification_complete,
    )


def _append_moderation_audit(
    result: NativeModerationResult,
    *,
    audit_log_path: Path,
    username: str,
    phase: str,
    duration_seconds: int,
    reason_class: str,
) -> None:
    details: dict[str, object] = {
        "phase": phase,
        "classification": (
            "pending"
            if phase == "intent"
            else result.classification
        ),
        "command_status": result.command_status or "not_run",
        "reason_class": reason_class,
        "baseline_complete": result.baseline_complete,
        "verification_complete": result.verification_complete,
    }
    if result.action == ACTION_BAN:
        details["duration_seconds"] = str(duration_seconds)
    if result.recovery_record_id is not None:
        details["recovery_record_id"] = str(result.recovery_record_id)
        details["recovery_state"] = result.recovery_state
    append_audit_event(
        audit_log_path,
        username=username,
        action=AUDIT_ACTIONS[result.action],
        instance=result.instance,
        target=result.target_identity,
        success=(phase == "intent" or result.success),
        message=("Native moderation action requested." if phase == "intent" else result.message),
        exit_code=0 if phase == "intent" or result.success else 1,
        details=details,
    )


def _intent_audit_failure(
    *,
    action: str,
    instance: str,
    target_identity: str,
    baseline_complete: bool,
) -> NativeModerationResult:
    return replace(
        _result(
            action=action,
            instance=instance,
            target_identity=target_identity,
            classification=CLASSIFICATION_FAILED,
            message=("Native moderation action was not run because audit logging failed."),
            error_code=ERROR_INTENT_AUDIT,
            baseline_complete=baseline_complete,
        ),
        intent_audited=False,
        audit_written=False,
    )


def _initial_classification(
    *,
    action: str,
    target_entries: tuple[rcon.NativeBanEntry, ...],
    duration_seconds: int,
    verification_retry: bool,
) -> tuple[str, str, str]:
    if action == ACTION_UNBAN and not target_entries:
        return (
            CLASSIFICATION_NOOP,
            "Player is not present in the authoritative native ban list.",
            "",
        )
    if action == ACTION_BAN and target_entries:
        if verification_retry or any(
            entry.duration_seconds == duration_seconds for entry in target_entries
        ):
            return (
                CLASSIFICATION_NOOP,
                "Player is already present in the authoritative native ban list.",
                "",
            )
        return (
            CLASSIFICATION_FAILED,
            "Player already has a native ban with a different duration.",
            ERROR_DURATION_MISMATCH,
        )
    return ("", "", "")


def _execute_command(
    *,
    action: str,
    instance: str,
    target_identity: str,
    duration_seconds: int,
    reason: str,
) -> rcon.NativeModerationCommandResult:
    if action == ACTION_BAN:
        return rcon.create_native_ban(
            instance,
            target_identity=target_identity,
            duration_seconds=duration_seconds,
            reason=reason,
        )
    return rcon.remove_native_ban(
        instance,
        target_identity=target_identity,
    )


def _classify_verified_outcome(
    *,
    action: str,
    target_identity: str,
    snapshot: AuthoritativeBanSnapshot,
    command: rcon.NativeModerationCommandResult,
) -> NativeModerationResult:
    if not snapshot.complete:
        return _result(
            action=action,
            instance="",
            target_identity=target_identity,
            classification=CLASSIFICATION_UNCERTAIN,
            message=(
                "Native moderation outcome is uncertain; authoritative verification is required."
            ),
            command_status=command.status,
            error_code=snapshot.error_code or command.error_code,
            baseline_complete=True,
            verification_complete=False,
        )

    target_present = bool(_target_entries(snapshot, target_identity))
    desired_state = target_present if action == ACTION_BAN else not target_present
    if desired_state:
        return _result(
            action=action,
            instance="",
            target_identity=target_identity,
            classification=CLASSIFICATION_CHANGED,
            message=(
                "Native ban confirmed." if action == ACTION_BAN else "Native unban confirmed."
            ),
            command_status=command.status,
            baseline_complete=True,
            verification_complete=True,
        )
    return _result(
        action=action,
        instance="",
        target_identity=target_identity,
        classification=CLASSIFICATION_FAILED,
        message=(
            "Native ban was not confirmed."
            if action == ACTION_BAN
            else "Native unban was not confirmed."
        ),
        command_status=command.status,
        error_code=command.error_code,
        baseline_complete=True,
        verification_complete=True,
    )


def _set_recovery_state(
    db_path: Path,
    *,
    record_id: int,
    state: str,
) -> bool:
    try:
        record = moderation_verification.set_moderation_verification_state(
            db_path,
            record_id,
            state,
        )
    except moderation_verification.ModerationVerificationError:
        return False
    return record is not None


def _create_recovery_record(
    db_path: Path,
    *,
    instance: str,
    action: str,
    target_identity: str,
    reason_class: str,
    state: str,
) -> int | None:
    try:
        record = moderation_verification.create_moderation_verification(
            db_path,
            instance=instance,
            action=action,
            reliable_identity=target_identity,
            reason_class=reason_class,
            verification_state=state,
        )
    except moderation_verification.ModerationVerificationError:
        return None
    return record.id


def _attach_pending_recovery(
    result: NativeModerationResult,
    *,
    db_path: Path,
    reason_class: str,
    state: str,
    existing_record_id: int | None,
) -> NativeModerationResult:
    record_id = existing_record_id
    stored = False
    if record_id is not None:
        stored = _set_recovery_state(
            db_path,
            record_id=record_id,
            state=state,
        )
    else:
        record_id = _create_recovery_record(
            db_path,
            instance=result.instance,
            action=result.action,
            target_identity=result.target_identity,
            reason_class=reason_class,
            state=state,
        )
        stored = record_id is not None
    return replace(
        result,
        recovery_record_id=record_id,
        recovery_state=state if stored else "",
        recovery_error=("" if stored else "Moderation verification recovery could not be stored."),
        success=result.success and stored,
        error_code=result.error_code or ("" if stored else ERROR_RECOVERY_STORAGE),
    )


def _resolve_existing_recovery(
    result: NativeModerationResult,
    *,
    db_path: Path,
    existing_record_id: int | None,
) -> NativeModerationResult:
    if existing_record_id is None:
        return result
    states = {
        CLASSIFICATION_CHANGED: moderation_verification.STATE_RESOLVED_CHANGED,
        CLASSIFICATION_NOOP: moderation_verification.STATE_RESOLVED_NOOP,
        CLASSIFICATION_FAILED: moderation_verification.STATE_RESOLVED_FAILED,
    }
    state = states.get(result.classification)
    if state is None:
        return result
    stored = _set_recovery_state(
        db_path,
        record_id=existing_record_id,
        state=state,
    )
    return replace(
        result,
        recovery_record_id=existing_record_id,
        recovery_state=state if stored else "",
        recovery_error=("" if stored else "Moderation verification recovery could not be updated."),
        success=result.success and stored,
        error_code=result.error_code or ("" if stored else ERROR_RECOVERY_STORAGE),
    )


def _audit_outcome(
    result: NativeModerationResult,
    *,
    audit_log_path: Path,
    username: str,
    duration_seconds: int,
    reason_class: str,
    command_attempted: bool,
    db_path: Path,
    existing_record_id: int | None,
) -> NativeModerationResult:
    try:
        _append_moderation_audit(
            result,
            audit_log_path=audit_log_path,
            username=username,
            phase="outcome",
            duration_seconds=duration_seconds,
            reason_class=reason_class,
        )
    except AuditLogError:
        failed = replace(
            result,
            success=False,
            audit_written=False,
            message=("Native moderation finished but outcome audit logging failed."),
            error_code=ERROR_OUTCOME_AUDIT,
        )
        if command_attempted:
            return _attach_pending_recovery(
                failed,
                db_path=db_path,
                reason_class=reason_class,
                state=(
                    moderation_verification.STATE_PENDING_VERIFICATION
                    if result.uncertain
                    else moderation_verification.STATE_PENDING_OUTCOME_AUDIT
                ),
                existing_record_id=(result.recovery_record_id or existing_record_id),
            )
        return failed
    return result


def _run_locked_native_moderation(
    *,
    action: str,
    instance: str,
    target_identity: str,
    duration_seconds: int,
    reason: str,
    audit_log_path: Path,
    username: str,
    db_path: Path,
    existing_record_id: int | None = None,
    verification_retry: bool = False,
) -> NativeModerationResult:
    reason_class = _reason_class(reason)
    baseline = _read_authoritative_ban_snapshot(instance)
    if not baseline.complete:
        return _result(
            action=action,
            instance=instance,
            target_identity=target_identity,
            classification=CLASSIFICATION_FAILED,
            message="Authoritative native ban list is unavailable; no action was run.",
            error_code=baseline.error_code or ERROR_AUTHORITATIVE_UNAVAILABLE,
            baseline_complete=False,
        )

    initial_classification, initial_message, initial_error = _initial_classification(
        action=action,
        target_entries=_target_entries(baseline, target_identity),
        duration_seconds=duration_seconds,
        verification_retry=verification_retry,
    )
    intent_result = _result(
        action=action,
        instance=instance,
        target_identity=target_identity,
        classification=initial_classification or CLASSIFICATION_CHANGED,
        message="Native moderation action requested.",
        baseline_complete=True,
    )
    try:
        _append_moderation_audit(
            intent_result,
            audit_log_path=audit_log_path,
            username=username,
            phase="intent",
            duration_seconds=duration_seconds,
            reason_class=reason_class,
        )
    except AuditLogError:
        return _intent_audit_failure(
            action=action,
            instance=instance,
            target_identity=target_identity,
            baseline_complete=True,
        )

    if initial_classification:
        result = _result(
            action=action,
            instance=instance,
            target_identity=target_identity,
            classification=initial_classification,
            message=initial_message,
            error_code=initial_error,
            baseline_complete=True,
            verification_complete=True,
        )
        result = _resolve_existing_recovery(
            result,
            db_path=db_path,
            existing_record_id=existing_record_id,
        )
        return _audit_outcome(
            result,
            audit_log_path=audit_log_path,
            username=username,
            duration_seconds=duration_seconds,
            reason_class=reason_class,
            command_attempted=False,
            db_path=db_path,
            existing_record_id=existing_record_id,
        )

    command = _execute_command(
        action=action,
        instance=instance,
        target_identity=target_identity,
        duration_seconds=duration_seconds,
        reason=reason,
    )
    verification = _read_authoritative_ban_snapshot(instance)
    result = replace(
        _classify_verified_outcome(
            action=action,
            target_identity=target_identity,
            snapshot=verification,
            command=command,
        ),
        instance=instance,
    )
    if result.uncertain:
        result = _attach_pending_recovery(
            result,
            db_path=db_path,
            reason_class=reason_class,
            state=moderation_verification.STATE_PENDING_VERIFICATION,
            existing_record_id=existing_record_id,
        )
    else:
        result = _resolve_existing_recovery(
            result,
            db_path=db_path,
            existing_record_id=existing_record_id,
        )
    return _audit_outcome(
        result,
        audit_log_path=audit_log_path,
        username=username,
        duration_seconds=duration_seconds,
        reason_class=reason_class,
        command_attempted=True,
        db_path=db_path,
        existing_record_id=existing_record_id,
    )


def run_native_moderation_action(
    action: str,
    *,
    target_identity: str,
    duration_seconds: int = 0,
    reason: str = "",
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    audit_log_path: Path,
    username: str,
    db_path: Path,
) -> NativeModerationResult:
    """Run one typed, audited, verified native ban or unban workflow."""
    normalized_action = _normalize_action(action)
    normalized_instance = paths.validate_instance_name(instance or paths.DEFAULT_INSTANCE_NAME)
    target = _normalize_target(target_identity)
    duration = _normalize_duration(normalized_action, duration_seconds)
    safe_reason = _normalize_reason(normalized_action, reason)
    with moderation_lock(normalized_instance):
        return _run_locked_native_moderation(
            action=normalized_action,
            instance=normalized_instance,
            target_identity=target,
            duration_seconds=duration,
            reason=safe_reason,
            audit_log_path=Path(audit_log_path),
            username=username,
            db_path=Path(db_path),
        )


def retry_native_moderation_verification(
    record_id: int,
    *,
    duration_seconds: int,
    reason: str = "",
    audit_log_path: Path,
    username: str,
    db_path: Path,
) -> NativeModerationResult:
    """Read first, then resolve or retry one pending verification record."""
    try:
        record = moderation_verification.get_moderation_verification(
            Path(db_path),
            record_id,
        )
    except moderation_verification.ModerationVerificationError as exc:
        raise NativeModerationError("Moderation verification recovery is unavailable.") from exc
    if record is None or record.verification_state not in (moderation_verification.PENDING_STATES):
        return _result(
            action="",
            instance=paths.DEFAULT_INSTANCE_NAME,
            target_identity="",
            classification=CLASSIFICATION_FAILED,
            message="Pending moderation verification record was not found.",
            error_code=ERROR_RECOVERY_NOT_FOUND,
        )

    action = _normalize_action(record.action)
    target = _normalize_target(record.reliable_identity)
    duration = _normalize_duration(action, duration_seconds)
    safe_reason = _normalize_reason(action, reason)
    with moderation_lock(record.instance):
        return _run_locked_native_moderation(
            action=action,
            instance=record.instance,
            target_identity=target,
            duration_seconds=duration,
            reason=safe_reason,
            audit_log_path=Path(audit_log_path),
            username=username,
            db_path=Path(db_path),
            existing_record_id=record.id,
            verification_retry=True,
        )
