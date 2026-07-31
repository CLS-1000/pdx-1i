"""POST /cycle/run — trigger one full intelligence cycle."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..auth import require_api_key
from ..schemas import CycleResponse

router = APIRouter(prefix="/cycle", tags=["cycle"])


@router.post("/run", response_model=CycleResponse, dependencies=[Depends(require_api_key)])
def run_cycle(request: Request) -> CycleResponse:
    """
    Trigger one full PDX-1i intelligence cycle synchronously.

    Runs harvest → parse → score → investigate → verify → analyze → store. Any brief
    that publication assembles is persisted by the pipeline, so it is readable from
    GET /brief afterwards. Returns a summary of what the cycle did.
    """
    from pdx1.pipeline import run_cycle as _run_cycle

    settings = request.app.state.settings
    store = request.app.state.store

    result = _run_cycle(settings=settings, store=store)

    return CycleResponse(
        run_id=result.run_id,
        harvested=result.harvested,
        parsed=result.parsed,
        opportunities=result.opportunities,
        written=result.written,
        dropped=result.dropped,
        errors=result.errors,
        brief_id=result.brief.brief_id if result.brief else None,
    )
