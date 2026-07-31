"""
Response schemas for the PDX-1i API.

Thin wrappers around the engine's Pydantic models that add pagination metadata.
The underlying models are re-exported so routes import from one place.
"""

from __future__ import annotations

from pydantic import BaseModel

from ..models import Brief, IntelligenceRecord, Signal  # re-export for routes


class Page(BaseModel):
    """Pagination envelope used by list endpoints."""

    total: int
    limit: int
    offset: int


class SignalPage(Page):
    items: list[Signal]


class RecordPage(Page):
    items: list[IntelligenceRecord]


class CycleResponse(BaseModel):
    """Summary returned by POST /cycle/run."""

    run_id: str
    harvested: int
    parsed: int
    opportunities: int
    written: int
    dropped: dict[str, int]
    errors: list[str]
    brief_id: str | None = None


__all__ = [
    "Brief",
    "CycleResponse",
    "IntelligenceRecord",
    "Page",
    "RecordPage",
    "Signal",
    "SignalPage",
]
