"""GET /leads — records at INVESTIGATE or ESCALATE disposition."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from ..auth import require_api_key
from ..schemas import RecordPage

router = APIRouter(prefix="/leads", tags=["leads"])

_LEAD_OUTCOMES = {"INVESTIGATE", "ESCALATE", "CORROBORATED"}


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
    leads = []
    for outcome in ("ESCALATE", "CORROBORATED", "INVESTIGATE"):
        leads.extend(store.query(outcome=outcome, limit=500))

    # Sort by confidence descending, then published_at descending.
    leads.sort(key=lambda r: (r.confidence, r.published_at), reverse=True)

    page = leads[offset : offset + limit]
    return RecordPage(total=len(leads), limit=limit, offset=offset, items=page)
