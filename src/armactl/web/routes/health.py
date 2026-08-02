"""Health-check routes for armactl web."""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from armactl.web.readiness import check_web_readiness

router = APIRouter()


@router.get("/healthz")
def healthz() -> dict[str, bool]:
    """Return a minimal liveness response."""
    return {"ok": True}


@router.get("/readyz")
def readyz(request: Request) -> JSONResponse:
    """Return bounded schema readiness for the currently running process."""
    report = check_web_readiness(request.app.state.web_data_root)
    status_code = status.HTTP_200_OK if report.ready else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(report.to_public_dict(), status_code=status_code)
