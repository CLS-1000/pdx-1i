"""
GET /brief — the latest assembled Metro Citizens Brief, plus the archive.

The brief is served two ways. The JSON routes return the `Brief` model: the
structured record, with every section's `source_record_ids` intact so a caller can
trace any published line back to the records behind it.

The `/pdf` routes return the same brief rendered in the SPEC-1 WorldStateBrief
format — masthead and date, synopsis, verified-signal count, numbered sections, and
a footer carrying the `run_id`. It is a rendering of the stored brief and nothing
more: the renderer produces no prose of its own, so the PDF and the JSON are the
same document in two formats. Neither is assembled on request — both read a brief a
cycle already published and the attribution gate already cleared.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

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


def _pdf_response(request: Request, brief: Brief) -> Response:
    """
    Render *brief* to a PDF and return it as the response body.

    The network diagram is included whenever the registry supports one. That is the
    reason these routes exist rather than the caller rendering for itself:
    `render_brief_pdf` takes `entity_ids` as a parameter because a `Brief` carries
    record ids and resolving them needs the store, and this is the layer that holds
    both. `build_network_drawing` draws nothing for fewer than two known nodes or
    when they share no tie, so a brief whose bodies are unconnected gets a document
    without a diagram rather than an empty frame implying they are unrelated.

    Rendered per request rather than cached. A brief is immutable once written, so a
    cache would be sound -- but nothing here has measured that the render is worth
    caching, and a stale PDF served beside a correct JSON brief is the kind of
    divergence this codebase spends its effort avoiding.
    """
    record_ids = [rid for section in brief.sections for rid in section.source_record_ids]
    entity_ids = request.app.state.store.entity_ids_for_records(record_ids)

    # Both the wrapper import and the render call have to be inside this, because
    # `pdf_renderer` imports reportlab lazily -- inside `render_brief_pdf`, not at
    # module scope -- and raises ImportError from there when the extra is absent.
    # Guarding only the `from ... import` catches the case where pdx1's own module is
    # missing, which is not the case anyone hits; the one that actually happens,
    # reportlab not installed, escaped as a 500 while the route advertised 503.
    try:
        from ...publication.pdf_renderer import render_brief_pdf

        with tempfile.TemporaryDirectory() as tmp:
            out = render_brief_pdf(brief, Path(tmp) / "brief.pdf", entity_ids=entity_ids)
            body = out.read_bytes()
    except ImportError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "PDF rendering is not available: reportlab is not installed. "
                "Install it with: pip install 'pdx-1i[pdf]'"
            ),
        ) from exc

    # The id is store-controlled, but it lands in a response header, so anything
    # outside this set is dropped rather than trusted to be harmless there.
    safe_id = re.sub(r"[^A-Za-z0-9._-]", "_", brief.brief_id) or "brief"
    return Response(
        content=body,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{safe_id}.pdf"'},
    )


@router.get(
    "/pdf",
    dependencies=[Depends(require_api_key)],
    response_class=Response,
    responses={
        200: {"content": {"application/pdf": {}}, "description": "The rendered brief."},
        404: {"description": "No brief has been assembled yet."},
        503: {"description": "reportlab is not installed."},
    },
)
def get_latest_brief_pdf(request: Request) -> Response:
    """
    The most recently produced brief, rendered in the SPEC-1 WorldStateBrief format.

    Declared before `/{brief_id}` so the literal path is matched first; routed the
    other way round, `pdf` would be read as a brief id and this would 404.
    """
    brief = request.app.state.store.latest_brief()
    if brief is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No brief has been assembled yet. Run a cycle via POST /cycle/run.",
        )
    return _pdf_response(request, brief)


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


@router.get(
    "/{brief_id}/pdf",
    dependencies=[Depends(require_api_key)],
    response_class=Response,
    responses={
        200: {"content": {"application/pdf": {}}, "description": "The rendered brief."},
        404: {"description": "No brief with that id."},
        503: {"description": "reportlab is not installed."},
    },
)
def get_brief_pdf_by_id(request: Request, brief_id: str) -> Response:
    """One archived brief by id, rendered in the SPEC-1 WorldStateBrief format."""
    brief = request.app.state.store.brief(brief_id)
    if brief is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No brief with id {brief_id!r}.",
        )
    return _pdf_response(request, brief)
