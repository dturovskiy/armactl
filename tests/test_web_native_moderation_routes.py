"""Authenticated mutation routes for verified native moderation."""

from __future__ import annotations

from pathlib import Path

import pytest
from web_route_helpers import _client, _form_token, _login

import armactl.rcon as rcon
from armactl.web.auth.setup import setup_owner_user
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
    )


def _result(
    action: str,
    *,
    classification: str = native_moderation.CLASSIFICATION_CHANGED,
    message: str = "Native ban confirmed.",
    recovery_record_id: int | None = None,
    recovery_state: str = "",
) -> native_moderation.NativeModerationResult:
    return native_moderation.NativeModerationResult(
        action=action,
        instance="default",
        target_identity=TARGET,
        classification=classification,
        success=classification
        in {
            native_moderation.CLASSIFICATION_CHANGED,
            native_moderation.CLASSIFICATION_NOOP,
        },
        changed=classification == native_moderation.CLASSIFICATION_CHANGED,
        message=message,
        baseline_complete=True,
        verification_complete=classification != native_moderation.CLASSIFICATION_UNCERTAIN,
        recovery_record_id=recovery_record_id,
        recovery_state=recovery_state,
    )


def _authed_client(tmp_path: Path):
    from armactl.web.app import create_app

    password = "native moderation route password"
    setup_owner_user(tmp_path, "owner", password)
    app = create_app(data_root=tmp_path)
    client = _client(app)
    response = _login(client, "owner", password)
    assert response.status_code == 303
    return app, client


def _patch_ban_list(monkeypatch, result: rcon.NativeBanListResult | None = None) -> None:
    from armactl.web.services import native_banlist

    monkeypatch.setattr(
        native_banlist,
        "load_native_ban_list",
        lambda instance, *, page: result or _ban_list(),
    )


def test_native_moderation_post_requires_authentication(tmp_path: Path, monkeypatch) -> None:
    from armactl.web.app import create_app

    monkeypatch.setattr(
        native_moderation,
        "run_native_moderation_action",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    client = _client(create_app(data_root=tmp_path))

    response = client.post(
        "/players/bans/ban",
        data={"target_identity": TARGET, "duration_seconds": "3600", "confirm": "ban"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_native_moderation_post_requires_permission(
    tmp_path: Path,
    monkeypatch,
    set_web_owner_permissions,
) -> None:
    set_web_owner_permissions(set())
    _app, client = _authed_client(tmp_path)
    monkeypatch.setattr(
        native_moderation,
        "run_native_moderation_action",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )

    response = client.post(
        "/players/bans/ban",
        data={"csrf_token": "unused", "confirm": "ban"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.text == "Permission denied."


def test_native_moderation_post_requires_csrf(tmp_path: Path, monkeypatch) -> None:
    _app, client = _authed_client(tmp_path)
    monkeypatch.setattr(
        native_moderation,
        "run_native_moderation_action",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )

    response = client.post(
        "/players/bans/ban",
        data={"csrf_token": "invalid", "confirm": "ban"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.text == "Invalid CSRF token."


def test_native_ban_requires_explicit_confirmation(tmp_path: Path, monkeypatch) -> None:
    _patch_ban_list(monkeypatch)
    _app, client = _authed_client(tmp_path)
    token = _form_token(client.get("/players/bans").text)
    monkeypatch.setattr(
        native_moderation,
        "run_native_moderation_action",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )

    response = client.post(
        "/players/bans/ban",
        data={
            "csrf_token": token,
            "target_identity": TARGET,
            "duration_seconds": "3600",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "Explicit confirmation is required for native moderation." in response.text


def test_confirmed_native_ban_calls_typed_service_and_renders_outcome(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _patch_ban_list(monkeypatch)
    app, client = _authed_client(tmp_path)
    token = _form_token(client.get("/players/bans").text)
    calls: list[tuple[tuple, dict]] = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return _result(native_moderation.ACTION_BAN)

    monkeypatch.setattr(native_moderation, "run_native_moderation_action", run)

    response = client.post(
        "/players/bans/ban",
        data={
            "csrf_token": token,
            "target_identity": TARGET,
            "duration_seconds": "86400",
            "reason": "Repeated team killing",
            "confirm": "ban",
        },
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "Native ban confirmed." in response.text
    assert "changed" in response.text
    assert calls == [
        (
            (native_moderation.ACTION_BAN,),
            {
                "target_identity": TARGET,
                "duration_seconds": "86400",
                "reason": "Repeated team killing",
                "instance": "default",
                "audit_log_path": app.state.web_runtime_config.audit_log_path,
                "username": "owner",
                "db_path": app.state.web_runtime_config.db_path,
            },
        )
    ]


def test_native_unban_row_requires_confirmation_and_calls_typed_service(
    tmp_path: Path,
    monkeypatch,
) -> None:
    entry = rcon.NativeBanEntry(
        native_ban_id="41",
        player_uid=TARGET,
        duration_seconds=3600,
    )
    _patch_ban_list(monkeypatch, _ban_list(entry))
    _app, client = _authed_client(tmp_path)
    page = client.get("/players/bans")
    token = _form_token(page.text)
    assert 'action="/players/bans/unban"' in page.text
    assert 'name="confirm" value="unban"' in page.text
    calls: list[tuple[str, str]] = []

    def run(action: str, **kwargs):
        calls.append((action, kwargs["target_identity"]))
        return _result(
            native_moderation.ACTION_UNBAN,
            message="Native unban confirmed.",
        )

    monkeypatch.setattr(native_moderation, "run_native_moderation_action", run)

    response = client.post(
        "/players/bans/unban",
        data={
            "csrf_token": token,
            "target_identity": TARGET,
            "confirm": "unban",
        },
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "Native unban confirmed." in response.text
    assert calls == [(native_moderation.ACTION_UNBAN, TARGET)]


def test_uncertain_result_renders_recovery_without_raw_backend_data(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _patch_ban_list(monkeypatch)
    _app, client = _authed_client(tmp_path)
    token = _form_token(client.get("/players/bans").text)
    result = _result(
        native_moderation.ACTION_BAN,
        classification=native_moderation.CLASSIFICATION_UNCERTAIN,
        message=("Native moderation outcome is uncertain; authoritative verification is required."),
        recovery_record_id=17,
        recovery_state=moderation_verification.STATE_PENDING_VERIFICATION,
    )
    monkeypatch.setattr(
        native_moderation,
        "run_native_moderation_action",
        lambda *args, **kwargs: result,
    )

    response = client.post(
        "/players/bans/ban",
        data={
            "csrf_token": token,
            "target_identity": TARGET,
            "duration_seconds": "3600",
            "confirm": "ban",
        },
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert "Verification recovery record" in response.text
    assert "#17" in response.text
    assert "pending verification" in response.text
    for forbidden in ("#ban create", "RCON password", "Traceback", "198.51.100.10"):
        assert forbidden not in response.text


def test_pending_verification_has_confirmed_read_first_retry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _patch_ban_list(monkeypatch)
    app, client = _authed_client(tmp_path)
    record = moderation_verification.create_moderation_verification(
        app.state.web_runtime_config.db_path,
        instance="default",
        action=moderation_verification.ACTION_BAN,
        reliable_identity=TARGET,
        reason_class=moderation_verification.REASON_CLASS_PROVIDED,
    )
    page = client.get("/players/bans")
    token = _form_token(page.text)
    assert 'action="/players/bans/retry"' in page.text
    assert f'value="{record.id}"' in page.text
    assert "Confirm verification retry" in page.text
    calls: list[tuple[int, str, str]] = []

    def retry(record_id: int, **kwargs):
        calls.append((record_id, kwargs["duration_seconds"], kwargs["reason"]))
        return _result(
            native_moderation.ACTION_BAN,
            classification=native_moderation.CLASSIFICATION_NOOP,
            message="Player is already present in the authoritative native ban list.",
            recovery_record_id=record_id,
            recovery_state=moderation_verification.STATE_RESOLVED_NOOP,
        )

    monkeypatch.setattr(native_moderation, "retry_native_moderation_verification", retry)

    response = client.post(
        "/players/bans/retry",
        data={
            "csrf_token": token,
            "record_id": str(record.id),
            "duration_seconds": "604800",
            "reason": "Replacement reason",
            "confirm": "retry",
        },
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "already present in the authoritative native ban list" in response.text
    assert calls == [(record.id, "604800", "Replacement reason")]


@pytest.mark.parametrize(
    "record_id",
    ("0", "-1", "abc", "１", pytest.param("9" * 5000, id="oversized")),
)
def test_retry_rejects_invalid_or_unbounded_recovery_record_id(
    record_id: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    _patch_ban_list(monkeypatch)
    _app, client = _authed_client(tmp_path)
    token = _form_token(client.get("/players/bans").text)
    monkeypatch.setattr(
        native_moderation,
        "retry_native_moderation_verification",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )

    response = client.post(
        "/players/bans/retry",
        data={
            "csrf_token": token,
            "record_id": record_id,
            "confirm": "retry",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "Pending moderation verification record is invalid." in response.text


def test_native_moderation_validation_error_does_not_echo_submitted_reason(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _patch_ban_list(monkeypatch)
    _app, client = _authed_client(tmp_path)
    token = _form_token(client.get("/players/bans").text)
    raw_reason = "198.51.100.10 password=raw-secret\nsecond line"

    def reject(*args, **kwargs):
        raise native_moderation.NativeModerationError("Native ban reason is invalid.")

    monkeypatch.setattr(native_moderation, "run_native_moderation_action", reject)

    response = client.post(
        "/players/bans/ban",
        data={
            "csrf_token": token,
            "target_identity": TARGET,
            "duration_seconds": "3600",
            "reason": raw_reason,
            "confirm": "ban",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "Native ban reason is invalid." in response.text
    assert raw_reason not in response.text
    assert "raw-secret" not in response.text
