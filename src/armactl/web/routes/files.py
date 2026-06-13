"""Read-only file browser routes for armactl web."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from armactl.web.auth.cookies import clear_csrf_cookie, clear_session_cookie, set_csrf_cookie
from armactl.web.auth.dependencies import (
    CurrentSession,
    get_current_session,
    get_form_csrf_token,
    get_web_runtime_config,
    permission_denied_response,
    require_permission,
)
from armactl.web.auth.permissions import FILES_READ
from armactl.web.services import filesystem

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
    roots = filesystem.list_allowed_roots(current.config.data_root)
    current_root = next((root for root in roots if root.root_id == root_id), None)
    directory = None
    preview = None
    error = ""
    status_code = status.HTTP_200_OK

    if root_id is not None:
        try:
            if preview_path is not None:
                preview = filesystem.preview_text_file(
                    current.config.data_root,
                    root_id,
                    preview_path,
                )
                parent_path = filesystem.parent_relative_path(preview.metadata.relative_path)
                directory = filesystem.list_directory(
                    current.config.data_root,
                    root_id,
                    parent_path,
                )
                current_root = preview.root
            else:
                directory = filesystem.list_directory(
                    current.config.data_root,
                    root_id,
                    relative_path,
                )
                current_root = directory.root
        except filesystem.UnknownFileRootError as exc:
            error = exc.public_message
            status_code = exc.status_code
        except filesystem.FileBrowserError as exc:
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
        },
        status_code=status_code,
    )
    if form_csrf.should_set_cookie:
        set_csrf_cookie(response, form_csrf.token, current.config)
    return response


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
