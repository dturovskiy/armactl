"""Health-check routes for armactl web."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/healthz")
def healthz() -> dict[str, bool]:
    """Return a minimal liveness response."""
    return {"ok": True}
