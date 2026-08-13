"""Focused tests for the typed native moderation Slice 7c service."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from armactl import rcon
from armactl.web.runtime.db import ensure_web_db
from armactl.web.services import (
    moderation_verification,
    native_banlist,
    native_moderation,
)
from armactl.web.services.audit import AuditLogError

TARGET = "21761a7f-c9b4-4bff-8375-b4b43abb95ec"


def _entry(
    *,
    native_ban_id: str = "41",
    target: str = TARGET,
    duration_seconds: int = 3600,
) -> rcon.NativeBanEntry:
    return rcon.NativeBanEntry(
        native_ban_id=native_ban_id,
        player_uid=target,
        duration_seconds=duration_seconds,
    )


def _page(
    *,
    page: int = 1,
    entries: tuple[rcon.NativeBanEntry, ...] = (),
    status: str = rcon.NATIVE_BAN_STATUS_COMPLETE,
    error_code: str = "",
    error: str = "",
) -> rcon.NativeBanListResult:
    return rcon.NativeBanListResult(
        requested_page=page,
        available=status != rcon.NATIVE_BAN_STATUS_UNAVAILABLE,
        complete=status == rcon.NATIVE_BAN_STATUS_COMPLETE,
        status=status,
        entries=entries,
        error_code=error_code,
        error=error,
        has_previous=page > 1,
        has_next=(
            status == rcon.NATIVE_BAN_STATUS_COMPLETE
            and len(entries) == rcon.NATIVE_BAN_PAGE_SIZE
            and page < rcon.NATIVE_BAN_MAX_PAGE
        ),
    )


def _command(
    action: str,
    *,
    status: str = rcon.NATIVE_MODERATION_STATUS_DISPATCHED,
    error_code: str = "",
) -> rcon.NativeModerationCommandResult:
    return rcon.NativeModerationCommandResult(
        action=action,
        target_identity=TARGET,
        dispatched=status == rcon.NATIVE_MODERATION_STATUS_DISPATCHED,
        status=status,
        error_code=error_code,
        error="",
    )


def _audit_events(audit_log_path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in audit_log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _run(
    tmp_path: Path,
    action: str,
    *,
    duration_seconds: int = 0,
    reason: str = "",
) -> native_moderation.NativeModerationResult:
    return native_moderation.run_native_moderation_action(
        action,
        target_identity=TARGET,
        duration_seconds=duration_seconds,
        reason=reason,
        audit_log_path=tmp_path / "logs" / "web" / "audit.log",
        username="owner",
        db_path=tmp_path / "web" / "web.db",
    )


def test_read_and_mutation_services_share_one_moderation_lock() -> None:
    assert native_banlist.moderation_lock is native_moderation.moderation_lock
def test_authoritative_snapshot_follows_bounded_pages(monkeypatch) -> None:
    first_page = tuple(
        _entry(
            native_ban_id=str(index),
            target=f"21761a7f-c9b4-4bff-8375-{index:012d}",
        )
        for index in range(1, 26)
    )
    calls: list[tuple[str, int, float]] = []

    def query(instance: str, *, page: int, timeout: float):
        calls.append((instance, page, timeout))
        return _page(page=page, entries=first_page if page == 1 else ())

    monkeypatch.setattr(rcon, "query_native_ban_list", query)

    snapshot = native_moderation._read_authoritative_ban_snapshot("default")

    assert snapshot.complete is True
    assert len(snapshot.entries) == 25
    assert [(instance, page) for instance, page, _timeout in calls] == [
        ("default", 1),
        ("default", 2),
    ]
    assert all(0.1 <= timeout <= 1.5 for _instance, _page, timeout in calls)


def test_authoritative_snapshot_rejects_partial_page(monkeypatch) -> None:
    def query(instance: str, *, page: int, timeout: float):
        return _page(
            status=rcon.NATIVE_BAN_STATUS_PARTIAL,
            entries=(_entry(),),
            error_code=rcon.NATIVE_BAN_ERROR_MALFORMED_RESPONSE,
            error="Controlled partial page.",
        )

    monkeypatch.setattr(rcon, "query_native_ban_list", query)

    snapshot = native_moderation._read_authoritative_ban_snapshot("default")

    assert snapshot.complete is False
    assert snapshot.entries == ()
    assert snapshot.error_code == rcon.NATIVE_BAN_ERROR_MALFORMED_RESPONSE


def test_native_ban_changed_is_read_before_write_verified_and_audited(
    tmp_path: Path,
    monkeypatch,
) -> None:
    pages = [_page(), _page(entries=(_entry(),))]
    commands: list[tuple[str, int, str]] = []

    def query(instance: str, *, page: int, timeout: float):
        assert page == 1
        return pages.pop(0)

    def create(
        instance: str,
        *,
        target_identity: str,
        duration_seconds: int,
        reason: str,
    ):
        commands.append((target_identity, duration_seconds, reason))
        return _command(rcon.NATIVE_MODERATION_ACTION_BAN)

    monkeypatch.setattr(rcon, "query_native_ban_list", query)
    monkeypatch.setattr(rcon, "create_native_ban", create)

    result = _run(
        tmp_path,
        native_moderation.ACTION_BAN,
        duration_seconds=3600,
        reason="private operator reason",
    )

    assert result.classification == native_moderation.CLASSIFICATION_CHANGED
    assert result.success is True
    assert result.changed is True
    assert result.verification_complete is True
    assert result.recovery_record_id is None
    assert commands == [(TARGET, 3600, "private operator reason")]
    events = _audit_events(tmp_path / "logs" / "web" / "audit.log")
    assert [event["details"]["phase"] for event in events] == ["intent", "outcome"]
    assert events[-1]["details"]["classification"] == "changed"
    serialized = json.dumps(events)
    assert "private operator reason" not in serialized
    assert "#ban create" not in serialized


def test_native_ban_same_duration_is_audited_noop_without_command(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        rcon,
        "query_native_ban_list",
        lambda instance, *, page, timeout: _page(entries=(_entry(),)),
    )
    monkeypatch.setattr(
        rcon,
        "create_native_ban",
        lambda *args, **kwargs: pytest.fail("Idempotent ban must not be sent."),
    )

    result = _run(
        tmp_path,
        native_moderation.ACTION_BAN,
        duration_seconds=3600,
    )

    assert result.classification == native_moderation.CLASSIFICATION_NOOP
    assert result.success is True
    assert result.changed is False
    events = _audit_events(tmp_path / "logs" / "web" / "audit.log")
    assert len(events) == 2
    assert events[-1]["details"]["command_status"] == "not_run"


def test_native_ban_different_duration_fails_closed_without_command(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        rcon,
        "query_native_ban_list",
        lambda instance, *, page, timeout: _page(entries=(_entry(duration_seconds=60),)),
    )
    monkeypatch.setattr(
        rcon,
        "create_native_ban",
        lambda *args, **kwargs: pytest.fail("Conflicting ban must not be sent."),
    )

    result = _run(
        tmp_path,
        native_moderation.ACTION_BAN,
        duration_seconds=3600,
    )

    assert result.classification == native_moderation.CLASSIFICATION_FAILED
    assert result.error_code == native_moderation.ERROR_DURATION_MISMATCH
    assert result.verification_complete is True


def test_unban_absent_is_audited_noop_without_command(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        rcon,
        "query_native_ban_list",
        lambda instance, *, page, timeout: _page(),
    )
    monkeypatch.setattr(
        rcon,
        "remove_native_ban",
        lambda *args, **kwargs: pytest.fail("Absent unban must not be sent."),
    )

    result = _run(tmp_path, native_moderation.ACTION_UNBAN)

    assert result.classification == native_moderation.CLASSIFICATION_NOOP
    assert result.success is True
    assert result.changed is False


def test_unavailable_baseline_stops_before_intent_or_command(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        rcon,
        "query_native_ban_list",
        lambda instance, *, page, timeout: _page(
            status=rcon.NATIVE_BAN_STATUS_UNAVAILABLE,
            error_code=rcon.NATIVE_BAN_ERROR_TIMEOUT,
            error="Controlled timeout.",
        ),
    )
    monkeypatch.setattr(
        rcon,
        "create_native_ban",
        lambda *args, **kwargs: pytest.fail("Unavailable baseline must fail closed."),
    )

    result = _run(
        tmp_path,
        native_moderation.ACTION_BAN,
        duration_seconds=3600,
    )

    assert result.classification == native_moderation.CLASSIFICATION_FAILED
    assert result.baseline_complete is False
    assert result.error_code == rcon.NATIVE_BAN_ERROR_TIMEOUT
    assert not (tmp_path / "logs" / "web" / "audit.log").exists()
    assert not (tmp_path / "web" / "web.db").exists()


def test_uncertain_verification_creates_only_recovery_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    pages = [
        _page(),
        _page(
            status=rcon.NATIVE_BAN_STATUS_UNAVAILABLE,
            error_code=rcon.NATIVE_BAN_ERROR_TIMEOUT,
            error="Controlled timeout.",
        ),
    ]
    monkeypatch.setattr(
        rcon,
        "query_native_ban_list",
        lambda instance, *, page, timeout: pages.pop(0),
    )
    monkeypatch.setattr(
        rcon,
        "create_native_ban",
        lambda *args, **kwargs: _command(
            rcon.NATIVE_MODERATION_ACTION_BAN,
            status=rcon.NATIVE_MODERATION_STATUS_UNCERTAIN,
            error_code=rcon.NATIVE_BAN_ERROR_TIMEOUT,
        ),
    )

    result = _run(
        tmp_path,
        native_moderation.ACTION_BAN,
        duration_seconds=3600,
        reason="reason text must not persist",
    )

    assert result.classification == native_moderation.CLASSIFICATION_UNCERTAIN
    assert result.success is False
    assert result.recovery_record_id is not None
    record = moderation_verification.get_moderation_verification(
        tmp_path / "web" / "web.db",
        result.recovery_record_id,
    )
    assert record is not None
    assert record.verification_state == (moderation_verification.STATE_PENDING_VERIFICATION)
    assert record.reliable_identity == TARGET
    with sqlite3.connect(tmp_path / "web" / "web.db") as connection:
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert not any("ban" in table.casefold() for table in tables)
    assert "reason text must not persist" not in repr(record)


def test_retry_reads_first_and_resolves_present_ban_without_mutation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "web" / "web.db"
    ensure_web_db(db_path)
    record = moderation_verification.create_moderation_verification(
        db_path,
        instance="default",
        action=moderation_verification.ACTION_BAN,
        reliable_identity=TARGET,
        reason_class=moderation_verification.REASON_CLASS_PROVIDED,
    )
    monkeypatch.setattr(
        rcon,
        "query_native_ban_list",
        lambda instance, *, page, timeout: _page(entries=(_entry(duration_seconds=17),)),
    )
    monkeypatch.setattr(
        rcon,
        "create_native_ban",
        lambda *args, **kwargs: pytest.fail("Retry must verify before mutation."),
    )

    result = native_moderation.retry_native_moderation_verification(
        record.id,
        duration_seconds=3600,
        reason="replacement reason",
        audit_log_path=tmp_path / "logs" / "web" / "audit.log",
        username="owner",
        db_path=db_path,
    )

    assert result.classification == native_moderation.CLASSIFICATION_NOOP
    assert result.success is True
    resolved = moderation_verification.get_moderation_verification(
        db_path,
        record.id,
    )
    assert resolved is not None
    assert resolved.verification_state == (moderation_verification.STATE_RESOLVED_NOOP)


def test_intent_audit_failure_runs_no_command(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        rcon,
        "query_native_ban_list",
        lambda instance, *, page, timeout: _page(),
    )
    monkeypatch.setattr(
        native_moderation,
        "append_audit_event",
        lambda *args, **kwargs: (_ for _ in ()).throw(AuditLogError("controlled")),
    )
    monkeypatch.setattr(
        rcon,
        "create_native_ban",
        lambda *args, **kwargs: pytest.fail("Intent audit failure must stop."),
    )

    result = _run(
        tmp_path,
        native_moderation.ACTION_BAN,
        duration_seconds=3600,
    )

    assert result.intent_audited is False
    assert result.audit_written is False
    assert result.error_code == native_moderation.ERROR_INTENT_AUDIT
    assert result.recovery_record_id is None


def test_outcome_audit_failure_after_verified_change_creates_recovery(
    tmp_path: Path,
    monkeypatch,
) -> None:
    pages = [_page(), _page(entries=(_entry(),))]
    monkeypatch.setattr(
        rcon,
        "query_native_ban_list",
        lambda instance, *, page, timeout: pages.pop(0),
    )
    monkeypatch.setattr(
        rcon,
        "create_native_ban",
        lambda *args, **kwargs: _command(rcon.NATIVE_MODERATION_ACTION_BAN),
    )
    original_append = native_moderation.append_audit_event
    calls = 0

    def append_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise AuditLogError("controlled")
        return original_append(*args, **kwargs)

    monkeypatch.setattr(native_moderation, "append_audit_event", append_once)

    result = _run(
        tmp_path,
        native_moderation.ACTION_BAN,
        duration_seconds=3600,
    )

    assert result.classification == native_moderation.CLASSIFICATION_CHANGED
    assert result.success is False
    assert result.changed is True
    assert result.audit_written is False
    assert result.error_code == native_moderation.ERROR_OUTCOME_AUDIT
    assert result.recovery_record_id is not None
    record = moderation_verification.get_moderation_verification(
        tmp_path / "web" / "web.db",
        result.recovery_record_id,
    )
    assert record is not None
    assert record.verification_state == (moderation_verification.STATE_PENDING_OUTCOME_AUDIT)


@pytest.mark.parametrize(
    ("action", "target", "duration", "reason"),
    (
        (native_moderation.ACTION_BAN, "nickname only", 3600, ""),
        (native_moderation.ACTION_BAN, TARGET, True, ""),
        (native_moderation.ACTION_BAN, TARGET, 3600, "bad\nreason"),
        ("raw command", TARGET, 3600, ""),
    ),
)
def test_invalid_moderation_input_stops_before_rcon(
    action: str,
    target: str,
    duration: object,
    reason: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        rcon,
        "query_native_ban_list",
        lambda *args, **kwargs: pytest.fail("Invalid input must stop before RCON."),
    )

    with pytest.raises(native_moderation.NativeModerationError):
        native_moderation.run_native_moderation_action(
            action,
            target_identity=target,
            duration_seconds=duration,
            reason=reason,
            audit_log_path=tmp_path / "audit.log",
            username="owner",
            db_path=tmp_path / "web.db",
        )
