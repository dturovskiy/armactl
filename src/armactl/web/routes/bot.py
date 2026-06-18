"""Telegram bot management routes for armactl web."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from armactl.web.auth.permissions import BOT_VIEW
from armactl.web.page_models.bot import load_bot_page
from armactl.web.routes._common import authenticated_page

router = APIRouter()


@router.get("/bot", response_class=HTMLResponse)
def bot_page(request: Request) -> Response:
    """Render read-only Telegram bot details."""
    return authenticated_page(
        request,
        permission=BOT_VIEW,
        template="bot.html",
        loader=load_bot_page,
    )
