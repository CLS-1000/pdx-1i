"""GET /leads — records at INVESTIGATE or ESCALATE disposition."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from ..auth import require_api_key
from ..schemas import RecordPage

router = APIRouter(prefix="/leads", tags=["leads"])

@router.get("", response_model=RecordPage, dependencies=[Depends(require_api_key)])
def list_leads(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> RecordPage:
    """
    Return records at INVESTIGATE, ESCALATE, or CORROBORATED disposition.

    These are the records that cleared the four gates and rose above the MONITOR
    threshold -- the analyst queue.
    """
    store = request.app.state.store
    total = store.count_leads()
    leads = store.query_leads(limit=limit, offset=offset)
    return RecordPage(total=total, limit=limit, offset=offset, items=leads)
