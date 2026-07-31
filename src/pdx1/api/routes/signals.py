"""GET /signals — raw harvested signals from the JSONL store."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from ..auth import require_api_key
from ..schemas import SignalPage

router = APIRouter(prefix="/signals", tags=["signals"])


@router.get("", response_model=SignalPage, dependencies=[Depends(require_api_key)])
def list_signals(
    request: Request,
    source: str | None = Query(default=None, description="Filter by source name"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> SignalPage:
    """
    Return raw Signal objects collected by the last cycle.

    Signals are read from the JSONL store via IntelligenceRecord.signal_id; the
    full Signal payload is reconstructed from the stored record fields.
    """
    store = request.app.state.store
    total = store.count_query(source=source)
    records = store.query(source=source, limit=limit, offset=offset)

    # Reconstruct minimal signal-like views from stored records.
    from pdx1.models import Signal

    signals = []
    for r in records:
        signals.append(
            Signal(
                signal_id=r.signal_id,
                source=r.source,
                source_type=r.source_type,
                text=r.pattern,
                url=r.url,
                published_at=r.published_at,
                credibility=r.confidence,
            )
        )

    return SignalPage(total=total, limit=limit, offset=offset, items=signals)
