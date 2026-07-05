"""Tests for shared web mutation recovery helpers."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest

from armactl.web.services import file_replacements, mutation_recovery, pending_work
from armactl.web.services.audit import AuditLogError


def _fingerprint(value: str) -> str:
    return pending_work.safe_state_fingerprint({"value": value})


def _recovery(db_path: Path) -> mutation_recovery.RestartPendingRecovery:
    return mutation_recovery.RestartPendingRecovery(
        db_path=db_path,
        instance="default",
        kind=pending_work.KIND_CONFIG,
        source_action="config.save",
        username="owner token=raw-user-secret",
        details="max_players password=raw-detail-secret",
        baseline_fingerprint=_fingerprint("before"),
        current_fingerprint=_fingerprint("after"),
    )


def _write_profile_file(data_root: Path, text: str = "before") -> Path:
    config_root = data_root / "default" / "config"
    config_root.mkdir(parents=True)
    target = config_root / "profile.cfg"
    target.write_text(text, encoding="utf-8")
    return target


def _replacement_backups(data_root: Path) -> list[Path]:
    backup_root = data_root / "default" / "backups" / "file-replacements"
    return sorted(backup_root.glob("*.bak"))


def test_mutation_recovery_falls_back_when_state_marker_raises(
    tmp_path: Path,
    monkeypatch,
):
    db_path = tmp_path / "web" / "web.db"

    def fail_primary(*args, **kwargs):
        raise RuntimeError("web.db locked token=raw-pending-secret")

    monkeypatch.setattr(pending_work, "mark_restart_pending_for_state", fail_primary)

    result = mutation_recovery.mark_restart_pending_for_mutation(_recovery(db_path))

    assert result.warning == pending_work.PENDING_WORK_FALLBACK_WARNING
    assert result.error == ""
    item = pending_work.get_fallback_pending_work(db_path, kind=pending_work.KIND_CONFIG)
    assert item is not None
    assert item.source_action == "config.save"
    assert item.details == "max_players password=***"
    sidecar_text = pending_work.fallback_pending_work_path(db_path).read_text(
        encoding="utf-8"
    )
    assert "raw-pending-secret" not in sidecar_text
    assert "raw-user-secret" not in sidecar_text
    assert "raw-detail-secret" not in sidecar_text


def test_mutation_recovery_reports_controlled_error_when_fallback_fails(
    tmp_path: Path,
    monkeypatch,
):
    db_path = tmp_path / "web" / "web.db"

    def fail_primary(*args, **kwargs):
        raise RuntimeError("web.db locked token=raw-pending-secret")

    def fail_fallback(*args, **kwargs):
        raise RuntimeError("fallback failed token=raw-fallback-secret")

    monkeypatch.setattr(pending_work, "mark_restart_pending_for_state", fail_primary)
    monkeypatch.setattr(pending_work, "mark_restart_pending_fallback", fail_fallback)

    result = mutation_recovery.mark_restart_pending_for_mutation(_recovery(db_path))

    assert result.warning == ""
    assert result.error == pending_work.PENDING_WORK_STORAGE_FAILED_MESSAGE
    assert "raw-pending-secret" not in result.error
    assert "raw-fallback-secret" not in result.error
    assert pending_work.list_fallback_pending_work(db_path) == []


def test_file_replacement_intent_audit_failure_does_not_publish(
    tmp_path: Path,
    monkeypatch,
):
    target = _write_profile_file(tmp_path)

    def fail_intent(audit_log_path, *, details, **kwargs):
        if details["phase"] == "intent":
            raise AuditLogError("disk full token=raw-audit-secret")

    monkeypatch.setattr(file_replacements, "append_audit_event", fail_intent)

    with pytest.raises(file_replacements.FileReplaceAuditError) as error:
        file_replacements.replace_file_and_audit(
            tmp_path,
            "config",
            "profile.cfg",
            BytesIO(b"after password=raw-content-secret"),
            audit_log_path=tmp_path / "logs" / "web" / "audit.log",
            username="owner",
            db_path=tmp_path / "web" / "web.db",
        )

    assert str(error.value) == file_replacements.REPLACE_AUDIT_FAILED_MESSAGE
    assert target.read_text(encoding="utf-8") == "before"
    assert _replacement_backups(tmp_path) == []
    assert not any(
        path.name.startswith(".armactl-replace-") for path in target.parent.iterdir()
    )
    assert str(target) not in str(error.value)
    assert "raw-audit-secret" not in str(error.value)
    assert "raw-content-secret" not in str(error.value)


def test_file_replacement_outcome_audit_failure_keeps_pending_marker(
    tmp_path: Path,
    monkeypatch,
):
    target = _write_profile_file(tmp_path)

    def fail_outcome(audit_log_path, *, details, **kwargs):
        if details["phase"] == "outcome":
            raise AuditLogError("disk full token=raw-audit-secret")

    monkeypatch.setattr(file_replacements, "append_audit_event", fail_outcome)

    with pytest.raises(file_replacements.FileReplaceAuditError) as error:
        file_replacements.replace_file_and_audit(
            tmp_path,
            "config",
            "profile.cfg",
            BytesIO(b"after password=raw-content-secret"),
            audit_log_path=tmp_path / "logs" / "web" / "audit.log",
            username="owner",
            db_path=tmp_path / "web" / "web.db",
        )

    assert str(error.value) == file_replacements.REPLACE_OUTCOME_AUDIT_FAILED_MESSAGE
    assert error.value.result is not None
    assert error.value.result.audit_written is False
    assert target.read_text(encoding="utf-8") == "after password=raw-content-secret"
    item = pending_work.get_pending_work(
        tmp_path / "web" / "web.db",
        kind=pending_work.KIND_CONFIG,
    )
    assert item is not None
    assert item.source_action == "file.replace"
    assert item.details == "profile_file"
    assert str(target) not in str(error.value)
    assert "raw-audit-secret" not in str(error.value)
    assert "raw-content-secret" not in str(error.value)


def test_file_replacement_pending_total_failure_is_controlled(
    tmp_path: Path,
    monkeypatch,
):
    target = _write_profile_file(tmp_path)

    def fail_primary(*args, **kwargs):
        raise RuntimeError("web.db locked token=raw-pending-secret")

    def fail_fallback(*args, **kwargs):
        raise RuntimeError("fallback failed token=raw-fallback-secret")

    monkeypatch.setattr(pending_work, "mark_restart_pending_for_state", fail_primary)
    monkeypatch.setattr(pending_work, "mark_restart_pending_fallback", fail_fallback)

    with pytest.raises(file_replacements.FileReplaceTrackingError) as error:
        file_replacements.replace_file_and_audit(
            tmp_path,
            "config",
            "profile.cfg",
            BytesIO(b"after password=raw-content-secret"),
            audit_log_path=tmp_path / "logs" / "web" / "audit.log",
            username="owner",
            db_path=tmp_path / "web" / "web.db",
        )

    assert str(error.value) == file_replacements.REPLACE_PENDING_FAILED_MESSAGE
    assert (
        error.value.result.pending_work_error == pending_work.PENDING_WORK_STORAGE_FAILED_MESSAGE
    )
    assert target.read_text(encoding="utf-8") == "after password=raw-content-secret"
    assert pending_work.list_fallback_pending_work(tmp_path / "web" / "web.db") == []
    assert str(target) not in str(error.value)
    assert "raw-pending-secret" not in str(error.value)
    assert "raw-fallback-secret" not in str(error.value)
    assert "raw-content-secret" not in str(error.value)
