"""Tests for secret redaction helpers."""

import armactl.redaction as redaction


def test_redact_sensitive_text_masks_bot_tokens_and_password_assignments() -> None:
    text = (
        "ARMACTL_BOT_TOKEN=123456789:ABCDEF_secret_token\n"
        'passwordAdmin="super-secret"\n'
        '{"password":"top-secret","token":"123456789:ABCDEF_secret_token"}'
    )

    redacted = redaction.redact_sensitive_text(text)

    assert "super-secret" not in redacted
    assert "top-secret" not in redacted
    assert "123456789:ABCDEF_secret_token" not in redacted
    assert "ARMACTL_BOT_TOKEN=***" in redacted
    assert '"password":"***"' in redacted


def test_safe_subprocess_error_prefers_stderr_and_redacts_secrets() -> None:
    result = redaction.safe_subprocess_error(
        "token=123456789:ABCDEF_secret_token",
        "password=top-secret",
    )

    assert result == "token=***"
