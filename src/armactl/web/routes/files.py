"""File browser routes for armactl web."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, Query, Request, UploadFile, status
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)

from armactl.web.auth.cookies import clear_csrf_cookie, clear_session_cookie, set_csrf_cookie
from armactl.web.auth.csrf import validate_csrf_token
from armactl.web.auth.dependencies import (
    CurrentSession,
    get_current_session,
    get_form_csrf_token,
    get_web_runtime_config,
    permission_denied_response,
    require_permission,
)
from armactl.web.auth.permissions import FILES_READ, FILES_WRITE
from armactl.web.services import file_replacements, file_uploads
from armactl.web.services.filesystem_errors import (
    FileBrowserError,
    UnknownFileRootError,
    UploadUnavailableError,
)
from armactl.web.services.filesystem_listing import list_directory
from armactl.web.services.filesystem_paths import parent_relative_path
from armactl.web.services.filesystem_preview import preview_text_file
from armactl.web.services.filesystem_roots import list_allowed_roots, root_allows_upload
from armactl.web.services.filesystem_transfer import resolve_download_file

router = APIRouter()


def _redirect_to_login(request: Request) -> RedirectResponse:
    config = get_web_runtime_config(request)
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    clear_session_cookie(response, config)
    clear_csrf_cookie(response, config)
    return response


def _render_files(
    request: Request,
    current: CurrentSession,
    *,
    root_id: str | None = None,
    relative_path: str | None = None,
    preview_path: str | None = None,
) -> Response:
    form_csrf = get_form_csrf_token(request, current)
    roots = list_allowed_roots(current.config.data_root)
    current_root = next((root for root in roots if root.root_id == root_id), None)
    directory = None
    preview = None
    error = ""
    status_code = status.HTTP_200_OK
    can_write_files = require_permission(current, FILES_WRITE)

    if root_id is not None:
        try:
            if preview_path is not None:
                preview = preview_text_file(
                    current.config.data_root,
                    root_id,
                    preview_path,
                )
                parent_path = parent_relative_path(preview.metadata.relative_path)
                directory = list_directory(
                    current.config.data_root,
                    root_id,
                    parent_path,
                )
                current_root = preview.root
            else:
                directory = list_directory(
                    current.config.data_root,
                    root_id,
                    relative_path,
                )
                current_root = directory.root
        except UnknownFileRootError as exc:
            error = exc.public_message
            status_code = exc.status_code
        except FileBrowserError as exc:
            error = exc.public_message
            status_code = exc.status_code

    response = request.app.state.templates.TemplateResponse(
        request=request,
        name="files.html",
        context={
            "current_user": current.user,
            "csrf_token": form_csrf.token,
            "roots": roots,
            "current_root": current_root,
            "directory": directory,
            "preview": preview,
            "error": error,
            "can_upload": (
                directory is not None
                and can_write_files
                and root_allows_upload(directory.root)
            ),
            "can_replace": (
                directory is not None
                and can_write_files
                and any(entry.replace_href for entry in directory.entries)
            ),
        },
        status_code=status_code,
    )
    if form_csrf.should_set_cookie:
        set_csrf_cookie(response, form_csrf.token, current.config)
    return response


def _controlled_file_error(error: FileBrowserError) -> PlainTextResponse:
    return PlainTextResponse(error.public_message, status_code=error.status_code)


def _authenticated_files(
    request: Request,
    *,
    root_id: str | None = None,
    relative_path: str | None = None,
    preview_path: str | None = None,
) -> Response:
    current = get_current_session(request)
    if current is None:
        return _redirect_to_login(request)
    if not require_permission(current, FILES_READ):
        return permission_denied_response()
    return _render_files(
        request,
        current,
        root_id=root_id,
        relative_path=relative_path,
        preview_path=preview_path,
    )


@router.get("/files", response_class=HTMLResponse)
def files_index(request: Request) -> Response:
    """Render the read-only file browser root selector."""
    return _authenticated_files(request)


@router.get("/files/{root_id}", response_class=HTMLResponse)
def files_root(
    request: Request,
    root_id: str,
    path: str | None = Query(default=None),
) -> Response:
    """Render a safe directory listing under one fixed root."""
    return _authenticated_files(request, root_id=root_id, relative_path=path)


@router.get("/files/{root_id}/preview", response_class=HTMLResponse)
def files_preview(
    request: Request,
    root_id: str,
    path: str | None = Query(default=None),
) -> Response:
    """Render a bounded text preview for one safe file."""
    return _authenticated_files(request, root_id=root_id, preview_path=path)


@router.post("/files/{root_id}/upload", response_class=HTMLResponse)
def files_upload(
    request: Request,
    root_id: str,
    path: str = Form(default=""),
    csrf_token: str = Form(default=""),
    upload: UploadFile | None = File(default=None),
) -> Response:
    """Upload one new file into a safe directory under a fixed root."""
    current = get_current_session(request)
    if current is None:
        return _redirect_to_login(request)
    if not require_permission(current, FILES_WRITE):
        return permission_denied_response()
    if not validate_csrf_token(current.config.db_path, current.session.id, csrf_token):
        return PlainTextResponse(
            "Invalid CSRF token.",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    if upload is None:
        return PlainTextResponse(
            UploadUnavailableError.public_message,
            status_code=UploadUnavailableError.status_code,
        )

    try:
        uploaded = file_uploads.upload_file_and_audit(
            current.config.data_root,
            root_id,
            path,
            upload.filename,
            upload.file,
            audit_log_path=current.config.audit_log_path,
            username=current.user.username,
        )
    except FileBrowserError as exc:
        return _controlled_file_error(exc)
    except (file_uploads.FileUploadAuditError, file_uploads.FileUploadPublishError) as exc:
        return PlainTextResponse(
            str(exc),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return RedirectResponse(uploaded.directory_href, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/files/{root_id}/replace", response_class=HTMLResponse)
def files_replace(
    request: Request,
    root_id: str,
    path: str = Form(default=""),
    csrf_token: str = Form(default=""),
    replacement: UploadFile | None = File(default=None),
) -> Response:
    """Replace one allowlisted config/profile file through the safe publish flow."""
    current = get_current_session(request)
    if current is None:
        return _redirect_to_login(request)
    if not require_permission(current, FILES_WRITE):
        return permission_denied_response()
    if not validate_csrf_token(current.config.db_path, current.session.id, csrf_token):
        return PlainTextResponse(
            "Invalid CSRF token.",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    if replacement is None:
        return PlainTextResponse(
            "File replacement unavailable.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        replaced = file_replacements.replace_file_and_audit(
            current.config.data_root,
            root_id,
            path,
            replacement.file,
            audit_log_path=current.config.audit_log_path,
            username=current.user.username,
            db_path=current.config.db_path,
        )
    except FileBrowserError as exc:
        return _controlled_file_error(exc)
    except (
        file_replacements.FileReplaceAuditError,
        file_replacements.FileReplacePublishError,
        file_replacements.FileReplaceTrackingError,
    ) as exc:
        return PlainTextResponse(
            str(exc),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return RedirectResponse(replaced.directory_href, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/files/{root_id}/download")
def files_download(
    request: Request,
    root_id: str,
    path: str | None = Query(default=None),
) -> Response:
    """Download one safe file under a fixed root as an attachment."""
    current = get_current_session(request)
    if current is None:
        return _redirect_to_login(request)
    if not require_permission(current, FILES_READ):
        return permission_denied_response()
    try:
        download = resolve_download_file(current.config.data_root, root_id, path)
    except FileBrowserError as exc:
        return _controlled_file_error(exc)
    return FileResponse(
        download.path,
        media_type="application/octet-stream",
        filename=download.filename,
    )
