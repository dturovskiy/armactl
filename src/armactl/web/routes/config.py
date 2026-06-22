"""Server config management routes for armactl web."""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from armactl import paths
from armactl.web.auth.cookies import set_csrf_cookie
from armactl.web.auth.csrf import validate_csrf_token
from armactl.web.auth.dependencies import (
    CurrentSession,
    get_current_session,
    get_form_csrf_token,
    permission_denied_response,
    require_permission,
)
from armactl.web.auth.permissions import CONFIG_VIEW, SETTINGS_MANAGE
from armactl.web.page_models import config as config_page_model
from armactl.web.routes._common import redirect_to_login
from armactl.web.services import config_edit

router = APIRouter()


def _render_config_page(
    request: Request,
    current: CurrentSession,
    *,
    saved: bool = False,
    save_error: str = "",
    unchanged: bool = False,
    audit_error: str = "",
    pending_work_warning: str = "",
    pending_work_error: str = "",
    status_code: int = status.HTTP_200_OK,
) -> Response:
    if not require_permission(current, CONFIG_VIEW):
        return permission_denied_response()

    form_csrf = get_form_csrf_token(request, current)
    page = config_page_model.load_config_page(paths.DEFAULT_INSTANCE_NAME)
    response = request.app.state.templates.TemplateResponse(
        request=request,
        name="config.html",
        context={
            "current_user": current.user,
            "csrf_token": form_csrf.token,
            "page": page,
            "can_edit_config": require_permission(current, SETTINGS_MANAGE),
            "config_saved": saved,
            "config_save_error": save_error,
            "config_unchanged": unchanged,
            "config_audit_error": audit_error,
            "config_pending_work_warning": pending_work_warning,
            "config_pending_work_error": pending_work_error,
        },
        status_code=status_code,
    )
    if form_csrf.should_set_cookie:
        set_csrf_cookie(response, form_csrf.token, current.config)
    return response


@router.get("/config", response_class=HTMLResponse)
def config_page(request: Request) -> Response:
    """Render server config details and safe edit controls when permitted."""
    current = get_current_session(request)
    if current is None:
        return redirect_to_login(request)
    return _render_config_page(
        request,
        current,
        saved=request.query_params.get("saved") == "1",
        unchanged=request.query_params.get("unchanged") == "1",
    )


@router.post("/config", response_class=HTMLResponse)
async def save_config_page(request: Request) -> Response:
    """Save allowlisted basic config fields without restarting the server."""
    current = get_current_session(request)
    if current is None:
        return redirect_to_login(request)
    if not require_permission(current, SETTINGS_MANAGE):
        return permission_denied_response()

    submitted_form = await request.form()
    csrf_token = str(submitted_form.get("csrf_token") or "")
    if not validate_csrf_token(current.config.db_path, current.session.id, csrf_token):
        return PlainTextResponse(
            "Invalid CSRF token.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    form = config_edit.extract_config_edit_form(submitted_form)
    try:
        result = config_edit.save_default_config_and_audit(
            paths.DEFAULT_INSTANCE_NAME,
            form,
            audit_log_path=current.config.audit_log_path,
            username=current.user.username,
            db_path=current.config.db_path,
        )
    except config_edit.ConfigEditError as error:
        return _render_config_page(
            request,
            current,
            save_error=str(error),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except config_edit.ConfigAuditError as error:
        result = error.result
        return _render_config_page(
            request,
            current,
            saved=True,
            audit_error=str(error),
            pending_work_warning=result.pending_work_warning,
            pending_work_error=result.pending_work_error,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if not result.changed_fields:
        return RedirectResponse("/config?unchanged=1", status_code=status.HTTP_303_SEE_OTHER)
    if result.pending_work_error:
        return _render_config_page(
            request,
            current,
            saved=True,
            pending_work_error=result.pending_work_error,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    if result.pending_work_warning:
        return _render_config_page(
            request,
            current,
            saved=True,
            pending_work_warning=result.pending_work_warning,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return RedirectResponse("/config?saved=1", status_code=status.HTTP_303_SEE_OTHER)
