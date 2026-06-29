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


def test_redact_sensitive_text_masks_absolute_paths_and_ip_addresses() -> None:
    posix_path = "/home/deus/projects/armactl/default/config/logs/run/console.log"
    sep = chr(92)
    windows_path = (
        f"C:{sep}armactl-data{sep}default{sep}config{sep}logs{sep}run{sep}console.log"
    )
    text = (
        f"read {posix_path} from {windows_path} "
        "via 198.51.100.77:2302 and [2001:db8::1]:2302"
    )

    redacted = redaction.redact_sensitive_text(text)

    assert posix_path not in redacted
    assert windows_path not in redacted
    assert "198.51.100.77" not in redacted
    assert "2001:db8::1" not in redacted
    assert redaction.REDACTED in redacted
