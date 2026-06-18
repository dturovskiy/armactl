"""Audited web upload workflow for the file browser."""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO

from armactl import paths
from armactl.web.services.audit import AuditLogError, append_audit_event
from armactl.web.services.filesystem_errors import FileBrowserError
from armactl.web.services.filesystem_transfer import (
    StagedUpload,
    UploadedFile,
    cleanup_staged_upload,
    publish_staged_upload,
    stage_upload_file,
)

UPLOAD_AUDIT_FAILED_MESSAGE = "File upload was not published because audit logging failed."
UPLOAD_PUBLISH_FAILED_MESSAGE = "File upload was audited but publishing failed."


class FileUploadAuditError(RuntimeError):
    """Raised when an upload cannot be audited before publication."""


class FileUploadPublishError(RuntimeError):
    """Raised when audit succeeded but final publication failed."""


def _audit_upload(
    staged: StagedUpload,
    *,
    audit_log_path: Path,
    username: str,
    instance: str,
) -> None:
    append_audit_event(
        audit_log_path,
        username=username,
        action="file.upload",
        instance=instance,
        target=f"{staged.root.root_id}:{staged.relative_path}",
        success=True,
        message="File upload staged for publish.",
        exit_code=0,
        details={
            "root": staged.root.root_id,
            "path": staged.relative_path,
            "size": str(staged.size),
        },
    )


def _audit_publish_failure(
    staged: StagedUpload,
    *,
    audit_log_path: Path,
    username: str,
    instance: str,
    message: str,
) -> None:
    try:
        append_audit_event(
            audit_log_path,
            username=username,
            action="file.upload.publish-failed",
            instance=instance,
            target=f"{staged.root.root_id}:{staged.relative_path}",
            success=False,
            message=message,
            exit_code=1,
            details={
                "root": staged.root.root_id,
                "path": staged.relative_path,
                "size": str(staged.size),
            },
        )
    except AuditLogError:
        return


def upload_file_and_audit(
    data_root: Path | None,
    root_id: str,
    directory_path: object | None,
    filename: object | None,
    source: BinaryIO,
    *,
    audit_log_path: Path,
    username: str,
    instance: str = paths.DEFAULT_INSTANCE_NAME,
    max_bytes: int | None = None,
) -> UploadedFile:
    """Stage upload bytes, audit intent, then publish the final file."""
    staged = stage_upload_file(
        data_root,
        root_id,
        directory_path,
        filename,
        source,
        instance=instance,
        max_bytes=max_bytes,
    )
    try:
        try:
            _audit_upload(
                staged,
                audit_log_path=audit_log_path,
                username=username,
                instance=instance,
            )
        except AuditLogError as exc:
            raise FileUploadAuditError(UPLOAD_AUDIT_FAILED_MESSAGE) from exc

        try:
            return publish_staged_upload(staged)
        except FileBrowserError as exc:
            _audit_publish_failure(
                staged,
                audit_log_path=audit_log_path,
                username=username,
                instance=instance,
                message=exc.public_message,
            )
            raise FileUploadPublishError(UPLOAD_PUBLISH_FAILED_MESSAGE) from exc
    finally:
        cleanup_staged_upload(staged)
