"""GET /brief — the latest assembled Metro Citizens Brief."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..auth import require_api_key
from ..schemas import Brief

router = APIRouter(prefix="/brief", tags=["brief"])


@router.get("", response_model=Brief, dependencies=[Depends(require_api_key)])
def get_brief(request: Request) -> Brief:
    """
    Return the latest assembled Brief.

    The brief is assembled by IssueBuilder when a publication trigger fires. It is
    held in application state and replaced each time a cycle publishes a new one.
    Returns 404 when no brief has been assembled since the server started.
    """
    brief = request.app.state.last_brief
    if brief is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No brief has been assembled yet. Run a cycle via POST /cycle/run.",
        )
    return brief
