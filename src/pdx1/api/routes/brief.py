"""GET /brief — the latest assembled Metro Citizens Brief, plus the archive."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from ..auth import require_api_key
from ..schemas import Brief

router = APIRouter(prefix="/brief", tags=["brief"])


@router.get("", response_model=Brief, dependencies=[Depends(require_api_key)])
def get_brief(request: Request) -> Brief:
    """
    Return the most recently produced Brief.

    Read from the store rather than process memory, so a brief assembled by the CLI or
    by the scheduler is visible here and survives a restart. Returns 404 only when no
    cycle has ever published one.
    """
    brief = request.app.state.store.latest_brief()
    if brief is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No brief has been assembled yet. Run a cycle via POST /cycle/run.",
        )
    return brief


@router.get(
    "/archive", response_model=list[Brief], dependencies=[Depends(require_api_key)]
)
def list_briefs(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[Brief]:
    """Every published brief, newest first."""
    return request.app.state.store.briefs(limit=limit, offset=offset)


@router.get("/{brief_id}", response_model=Brief, dependencies=[Depends(require_api_key)])
def get_brief_by_id(request: Request, brief_id: str) -> Brief:
    """Fetch one brief by its ID."""
    brief = request.app.state.store.brief(brief_id)
    if brief is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No brief with id {brief_id!r}.",
        )
    return brief
