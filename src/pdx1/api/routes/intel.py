"""GET /intel — analyzed IntelligenceRecords."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from ..auth import require_api_key
from ..schemas import RecordPage

router = APIRouter(prefix="/intel", tags=["intel"])


@router.get("", response_model=RecordPage, dependencies=[Depends(require_api_key)])
def list_intel(
    request: Request,
    outcome: str | None = Query(default=None, description="Filter by outcome value"),
    source: str | None = Query(default=None, description="Filter by source name"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> RecordPage:
    """Return IntelligenceRecords from the store, newest first."""
    store = request.app.state.store
    records = store.query(outcome=outcome, source=source, limit=limit + offset)
    page = records[offset : offset + limit]
    return RecordPage(total=len(records), limit=limit, offset=offset, items=page)
