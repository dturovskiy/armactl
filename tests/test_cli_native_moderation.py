"""CLI fallback coverage for typed native moderation workflows."""

from __future__ import annotations

from click.testing import CliRunner

import armactl.rcon as rcon
from armactl.cli import main
from armactl.web.services import moderation_verification, native_moderation

TARGET = "21761a7f-c9b4-4bff-8375-b4b43abb95ec"


def _ban_list(
    *entries: rcon.NativeBanEntry,
    status: str = rcon.NATIVE_BAN_STATUS_COMPLETE,
) -> rcon.NativeBanListResult:
    return rcon.NativeBanListResult(
        requested_page=1,
        available=status != rcon.NATIVE_BAN_STATUS_UNAVAILABLE,
        complete=status == rcon.NATIVE_BAN_STATUS_COMPLETE,
        status=status,
        entries=entries,
        error=("Native ban list unavailable." if status != rcon.NATIVE_BAN_STATUS_COMPLETE else ""),
    )


def _moderation_result(
    action: str,
    *,
    classification: str = native_moderation.CLASSIFICATION_CHANGED,
    recovery_record_id: int | None = None,
) -> native_moderation.NativeModerationResult:
    success = classification in {
        native_moderation.CLASSIFICATION_CHANGED,
        native_moderation.CLASSIFICATION_NOOP,
    }
    return native_moderation.NativeModerationResult(
        action=action,
        instance="default",
        target_identity=TARGET,
        classification=classification,
        success=success,
        changed=classification == native_moderation.CLASSIFICATION_CHANGED,
        message=(
            "Native moderation outcome is uncertain; authoritative verification is required."
            if not success
            else "Native moderation confirmed."
        ),
        baseline_complete=True,
        verification_complete=success,
        recovery_record_id=recovery_record_id,
        recovery_state=(
            moderation_verification.STATE_PENDING_VERIFICATION if recovery_record_id else ""
        ),
    )


def _patch_runtime_paths(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "armactl.cli._native_moderation_runtime_paths",
        lambda: (tmp_path / "audit.log", tmp_path / "web.db"),
    )
    monkeypatch.setattr("armactl.cli._native_moderation_username", lambda: "cli:test")


def test_cli_native_ban_list_uses_typed_read_adapter(monkeypatch) -> None:
    calls = []
    entry = rcon.NativeBanEntry(
        native_ban_id="41",
        player_uid=TARGET,
        duration_seconds=3600,
    )
    monkeypatch.setattr(
        "armactl.web.services.native_banlist.load_native_ban_list",
        lambda instance, *, page: calls.append((instance, page)) or _ban_list(entry),
    )

    result = CliRunner().invoke(main, ["players", "bans", "list", "--page", "1"])

    assert result.exit_code == 0
    assert calls == [("default", 1)]
    assert TARGET in result.output
    assert "3600 seconds" in result.output
    assert "#ban list" not in result.output


def test_cli_native_ban_list_incomplete_exits_nonzero_without_raw_output(monkeypatch) -> None:
    monkeypatch.setattr(
        "armactl.web.services.native_banlist.load_native_ban_list",
        lambda *args, **kwargs: _ban_list(status=rcon.NATIVE_BAN_STATUS_UNAVAILABLE),
    )

    result = CliRunner().invoke(
        main,
        ["--json-output", "players", "bans", "list"],
    )

    assert result.exit_code == 1
    assert '"complete": false' in result.output
    assert "Native ban list unavailable." in result.output
    for forbidden in ("#ban list", "password=", "198.51.100.10", "Traceback"):
        assert forbidden not in result.output


def test_cli_native_ban_requires_explicit_confirmation(monkeypatch) -> None:
    monkeypatch.setattr(
        native_moderation,
        "run_native_moderation_action",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )

    result = CliRunner().invoke(
        main,
        ["players", "bans", "ban", TARGET, "--duration", "3600"],
        input="n\n",
    )

    assert result.exit_code == 1
    assert "Aborted" in result.output


def test_cli_native_ban_validates_target_before_prompt_or_service(monkeypatch) -> None:
    monkeypatch.setattr(
        native_moderation,
        "run_native_moderation_action",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )

    result = CliRunner().invoke(
        main,
        [
            "players",
            "bans",
            "ban",
            "bad\x1b[31m-target",
            "--duration",
            "3600",
        ],
    )

    assert result.exit_code == 1
    assert "A reliable player identity is required for native moderation." in result.output
    assert "\x1b" not in result.output
    assert "Ban reliable identity" not in result.output


def test_cli_confirmed_native_ban_calls_shared_service(monkeypatch, tmp_path) -> None:
    _patch_runtime_paths(monkeypatch, tmp_path)
    calls = []

    def run(action: str, **kwargs):
        calls.append((action, kwargs))
        return _moderation_result(action)

    monkeypatch.setattr(native_moderation, "run_native_moderation_action", run)

    result = CliRunner().invoke(
        main,
        [
            "players",
            "bans",
            "ban",
            TARGET,
            "--duration",
            "604800",
            "--reason",
            "Repeated team killing",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    assert calls == [
        (
            native_moderation.ACTION_BAN,
            {
                "target_identity": TARGET,
                "duration_seconds": 604800,
                "reason": "Repeated team killing",
                "instance": "default",
                "audit_log_path": tmp_path / "audit.log",
                "username": "cli:test",
                "db_path": tmp_path / "web.db",
            },
        )
    ]
    assert "Native moderation: changed" in result.output
    assert "Verification: complete" in result.output


def test_cli_confirmed_native_unban_calls_shared_service(monkeypatch, tmp_path) -> None:
    _patch_runtime_paths(monkeypatch, tmp_path)
    calls = []

    def run(action: str, **kwargs):
        calls.append((action, kwargs["target_identity"]))
        return _moderation_result(action)

    monkeypatch.setattr(native_moderation, "run_native_moderation_action", run)

    result = CliRunner().invoke(
        main,
        ["players", "bans", "unban", TARGET, "--yes"],
    )

    assert result.exit_code == 0
    assert calls == [(native_moderation.ACTION_UNBAN, TARGET)]
    assert "Action:       unban" in result.output


def test_cli_pending_recovery_list_is_bounded(monkeypatch, tmp_path) -> None:
    _patch_runtime_paths(monkeypatch, tmp_path)
    calls = []
    record = moderation_verification.ModerationVerificationRecord(
        id=17,
        instance="default",
        action=moderation_verification.ACTION_BAN,
        reliable_identity=TARGET,
        reason_class=moderation_verification.REASON_CLASS_PROVIDED,
        verification_state=moderation_verification.STATE_PENDING_VERIFICATION,
        created_at="2026-09-13T16:18:11+00:00",
        updated_at="2026-09-13T16:18:12+00:00",
    )
    monkeypatch.setattr(
        moderation_verification,
        "list_pending_moderation_verifications",
        lambda db_path, **kwargs: calls.append((db_path, kwargs)) or (record,),
    )

    result = CliRunner().invoke(
        main,
        ["players", "bans", "pending", "--limit", "7"],
    )

    assert result.exit_code == 0
    assert calls == [
        (
            tmp_path / "web.db",
            {"instance": "default", "limit": 7},
        )
    ]
    assert f"#17 | ban | {TARGET}" in result.output
    assert "reason=provided" in result.output


def test_cli_retry_requires_explicit_replacement_duration() -> None:
    result = CliRunner().invoke(
        main,
        ["players", "bans", "retry", "17", "--yes"],
    )

    assert result.exit_code == 2
    assert "Missing option '--duration'" in result.output


def test_cli_retry_surfaces_uncertain_recovery_and_exits_nonzero(
    monkeypatch,
    tmp_path,
) -> None:
    _patch_runtime_paths(monkeypatch, tmp_path)
    calls = []

    def retry(record_id: int, **kwargs):
        calls.append((record_id, kwargs))
        return _moderation_result(
            native_moderation.ACTION_BAN,
            classification=native_moderation.CLASSIFICATION_UNCERTAIN,
            recovery_record_id=record_id,
        )

    monkeypatch.setattr(native_moderation, "retry_native_moderation_verification", retry)

    result = CliRunner().invoke(
        main,
        [
            "players",
            "bans",
            "retry",
            "17",
            "--duration",
            "3600",
            "--yes",
        ],
    )

    assert result.exit_code == 1
    assert calls[0][0] == 17
    assert calls[0][1]["duration_seconds"] == 3600
    assert "Verification: incomplete" in result.output
    assert "Recovery:     #17 (pending_verification)" in result.output
