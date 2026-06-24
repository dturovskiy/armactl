"""Workshop mod management routes for armactl web."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, Request, UploadFile, status
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

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
from armactl.web.auth.permissions import MODS_MANAGE, MODS_VIEW
from armactl.web.page_models import mods as mods_page_model
from armactl.web.routes._common import redirect_to_login
from armactl.web.services import mod_actions

router = APIRouter()


def _render_mods_page(
    request: Request,
    current: CurrentSession,
    *,
    result: mod_actions.ModActionResult | None = None,
    pending_work_warning: str = "",
    pending_work_error: str = "",
    status_code: int = status.HTTP_200_OK,
) -> Response:
    if not require_permission(current, MODS_VIEW):
        return permission_denied_response()

    form_csrf = get_form_csrf_token(request, current)
    page = mods_page_model.load_mods_page(paths.DEFAULT_INSTANCE_NAME)
    response = request.app.state.templates.TemplateResponse(
        request=request,
        name="mods.html",
        context={
            "current_user": current.user,
            "csrf_token": form_csrf.token,
            "page": page,
            "result": result,
            "pending_work_warning": pending_work_warning,
            "pending_work_error": pending_work_error,
            "can_manage_mods": require_permission(current, MODS_MANAGE),
        },
        status_code=status_code,
    )
    if form_csrf.should_set_cookie:
        set_csrf_cookie(response, form_csrf.token, current.config)
    return response


def _result_status(result: mod_actions.ModActionResult) -> int:
    result_status = status.HTTP_200_OK if result.success else status.HTTP_400_BAD_REQUEST
    if result.pending_work_warning or result.pending_work_error:
        result_status = status.HTTP_500_INTERNAL_SERVER_ERROR
    return result_status


def _render_action_result(
    request: Request,
    current: CurrentSession,
    result: mod_actions.ModActionResult,
) -> Response:
    return _render_mods_page(
        request,
        current,
        result=result,
        pending_work_warning=result.pending_work_warning,
        pending_work_error=result.pending_work_error,
        status_code=_result_status(result),
    )


def _require_manage_post(
    request: Request,
    csrf_token: str,
) -> tuple[CurrentSession | None, Response | None]:
    current = get_current_session(request)
    if current is None:
        return None, redirect_to_login(request)
    if not require_permission(current, MODS_MANAGE):
        return None, permission_denied_response()
    if not validate_csrf_token(current.config.db_path, current.session.id, csrf_token):
        return None, PlainTextResponse(
            "Invalid CSRF token.",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    return current, None


def _run_mod_action(
    request: Request,
    *,
    action: str,
    csrf_token: str,
    mod_id: str = "",
    name: str = "",
    version: str = "",
) -> Response:
    current, error_response = _require_manage_post(request, csrf_token)
    if error_response is not None:
        return error_response
    assert current is not None

    try:
        result = mod_actions.run_mod_action_and_audit(
            action,
            instance=paths.DEFAULT_INSTANCE_NAME,
            mod_id=mod_id,
            name=name,
            version=version,
            audit_log_path=current.config.audit_log_path,
            username=current.user.username,
            db_path=current.config.db_path,
        )
    except mod_actions.ModActionError:
        return PlainTextResponse(
            "Unknown mod action.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    return _render_action_result(request, current, result)


@router.get("/mods", response_class=HTMLResponse)
def mods_page(request: Request) -> Response:
    """Render mod details and safe edit controls when permitted."""
    current = get_current_session(request)
    if current is None:
        return redirect_to_login(request)
    return _render_mods_page(request, current)


@router.post("/mods/add", response_class=HTMLResponse)
def add_mod_page(
    request: Request,
    csrf_token: str = Form(default=""),
    mod_id: str = Form(default=""),
    name: str = Form(default=""),
    version: str = Form(default=""),
) -> Response:
    """Add or update one Workshop mod."""
    return _run_mod_action(
        request,
        action=mod_actions.ACTION_ADD,
        csrf_token=csrf_token,
        mod_id=mod_id,
        name=name,
        version=version,
    )


@router.post("/mods/bulk-add", response_class=HTMLResponse)
def bulk_add_mods_page(
    request: Request,
    csrf_token: str = Form(default=""),
    bulk_mods: str = Form(default=""),
) -> Response:
    """Add multiple Workshop mods from pasted IDs or links."""
    current, error_response = _require_manage_post(request, csrf_token)
    if error_response is not None:
        return error_response
    assert current is not None

    result = mod_actions.run_bulk_add_and_audit(
        instance=paths.DEFAULT_INSTANCE_NAME,
        text=bulk_mods,
        audit_log_path=current.config.audit_log_path,
        username=current.user.username,
        db_path=current.config.db_path,
    )
    return _render_action_result(request, current, result)


@router.post("/mods/import", response_class=HTMLResponse)
def import_mod_pack_page(
    request: Request,
    csrf_token: str = Form(default=""),
    mode: str = Form(default=mod_actions.IMPORT_MODE_APPEND),
    confirm: str = Form(default=""),
    upload: UploadFile | None = File(default=None),
) -> Response:
    """Import a mod-pack JSON file or full config JSON with game.mods."""
    current, error_response = _require_manage_post(request, csrf_token)
    if error_response is not None:
        return error_response
    assert current is not None

    if mode.strip().lower() == mod_actions.IMPORT_MODE_REPLACE and confirm != "replace":
        return _render_mods_page(
            request,
            current,
            result=mod_actions.import_replace_confirmation_failure(
                instance=paths.DEFAULT_INSTANCE_NAME,
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if upload is None:
        return _render_mods_page(
            request,
            current,
            result=mod_actions.missing_import_file_failure(
                instance=paths.DEFAULT_INSTANCE_NAME,
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    result = mod_actions.run_import_mod_pack_and_audit(
        instance=paths.DEFAULT_INSTANCE_NAME,
        upload_file=upload.file,
        data_root=current.config.data_root,
        mode=mode,
        audit_log_path=current.config.audit_log_path,
        username=current.user.username,
        db_path=current.config.db_path,
    )
    return _render_action_result(request, current, result)


@router.post("/mods/export")
def export_mod_pack_page(
    request: Request,
    csrf_token: str = Form(default=""),
) -> Response:
    """Export active Workshop mods as a JSON attachment."""
    current, error_response = _require_manage_post(request, csrf_token)
    if error_response is not None:
        return error_response
    assert current is not None

    download = mod_actions.export_mod_pack_and_audit(
        instance=paths.DEFAULT_INSTANCE_NAME,
        audit_log_path=current.config.audit_log_path,
        username=current.user.username,
    )
    if not download.result.success:
        return _render_action_result(request, current, download.result)
    return Response(
        content=download.content,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{download.filename}"',
        },
    )


@router.post("/mods/dedupe", response_class=HTMLResponse)
def dedupe_mods_page(
    request: Request,
    csrf_token: str = Form(default=""),
) -> Response:
    """Remove duplicate Workshop mods from active and disabled lists."""
    current, error_response = _require_manage_post(request, csrf_token)
    if error_response is not None:
        return error_response
    assert current is not None

    result = mod_actions.run_dedupe_and_audit(
        instance=paths.DEFAULT_INSTANCE_NAME,
        audit_log_path=current.config.audit_log_path,
        username=current.user.username,
        db_path=current.config.db_path,
    )
    return _render_action_result(request, current, result)


@router.post("/mods/cleanup-check", response_class=HTMLResponse)
def check_unused_addons_page(
    request: Request,
    csrf_token: str = Form(default=""),
) -> Response:
    """Dry-run cleanup for unused local Workshop addon files."""
    current, error_response = _require_manage_post(request, csrf_token)
    if error_response is not None:
        return error_response
    assert current is not None

    result = mod_actions.check_unused_addons(instance=paths.DEFAULT_INSTANCE_NAME)
    return _render_action_result(request, current, result)


@router.post("/mods/cleanup", response_class=HTMLResponse)
def cleanup_unused_addons_page(
    request: Request,
    csrf_token: str = Form(default=""),
    confirm: str = Form(default=""),
) -> Response:
    """Clean unused local Workshop addon files after explicit confirmation."""
    current, error_response = _require_manage_post(request, csrf_token)
    if error_response is not None:
        return error_response
    assert current is not None

    if confirm != "cleanup":
        return _render_mods_page(
            request,
            current,
            result=mod_actions.cleanup_confirmation_failure(
                instance=paths.DEFAULT_INSTANCE_NAME,
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    result = mod_actions.run_cleanup_unused_addons_and_audit(
        instance=paths.DEFAULT_INSTANCE_NAME,
        audit_log_path=current.config.audit_log_path,
        username=current.user.username,
    )
    return _render_action_result(request, current, result)


@router.post("/mods/disable", response_class=HTMLResponse)
def disable_mod_page(
    request: Request,
    csrf_token: str = Form(default=""),
    mod_id: str = Form(default=""),
) -> Response:
    """Disable one active Workshop mod without deleting addon files."""
    return _run_mod_action(
        request,
        action=mod_actions.ACTION_DISABLE,
        csrf_token=csrf_token,
        mod_id=mod_id,
    )


@router.post("/mods/enable", response_class=HTMLResponse)
def enable_mod_page(
    request: Request,
    csrf_token: str = Form(default=""),
    mod_id: str = Form(default=""),
) -> Response:
    """Enable one disabled Workshop mod."""
    return _run_mod_action(
        request,
        action=mod_actions.ACTION_ENABLE,
        csrf_token=csrf_token,
        mod_id=mod_id,
    )


@router.post("/mods/remove", response_class=HTMLResponse)
def remove_mod_page(
    request: Request,
    csrf_token: str = Form(default=""),
    mod_id: str = Form(default=""),
    confirm: str = Form(default=""),
) -> Response:
    """Remove one Workshop mod after explicit confirmation."""
    current, error_response = _require_manage_post(request, csrf_token)
    if error_response is not None:
        return error_response
    assert current is not None
    if confirm != "remove":
        return _render_mods_page(
            request,
            current,
            result=mod_actions.confirmation_failure(
                instance=paths.DEFAULT_INSTANCE_NAME,
                target=mod_id,
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return _run_mod_action(
        request,
        action=mod_actions.ACTION_REMOVE,
        csrf_token=csrf_token,
        mod_id=mod_id,
    )
