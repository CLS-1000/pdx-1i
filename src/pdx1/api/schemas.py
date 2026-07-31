"""
Response schemas for the PDX-1i API.

Thin wrappers around the engine's Pydantic models that add pagination metadata.
The underlying models are re-exported so routes import from one place.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from ..models import (  # re-export for routes
    Brief,
    IntelligenceRecord,
    Jurisdiction,
    NodeGroup,
    Signal,
    TieKind,
    utcnow,
)


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


# ── Political web ────────────────────────────────────────────────────────────


class GraphNode(BaseModel):
    """
    One node of the political web, with how often the record set mentions it.

    `record_count` is a count and nothing more. A renderer may size or weight a node by
    it, but it carries no claim about the body it counts -- appearing often in public
    filings is what public bodies do.
    """

    id: str
    label: str
    group: NodeGroup
    weight: float
    flag: str | None = None
    record_count: int = 0


class GraphTie(BaseModel):
    """One edge. `kind` drives line style; `flagged` marks a declared interest."""

    source: str
    target: str
    kind: TieKind
    flagged: bool = False


class GraphResponse(BaseModel):
    """
    The full registry: every node and every tie.

    Small and fixed -- 31 nodes, 40 ties -- so it ships in one response rather than
    paginating. A renderer can lay the whole thing out without a second round trip.
    """

    nodes: list[GraphNode]
    ties: list[GraphTie]
    node_count: int
    tie_count: int
    generated_at: datetime = Field(default_factory=utcnow)


class NodeDetail(BaseModel):
    """
    One node, its edges, and the records that mention it.

    This is what a click on the web map needs: who the node is, what it connects to,
    and which published records touch it.
    """

    node: GraphNode
    ties: list[GraphTie]
    neighbors: list[GraphNode]
    records: list[IntelligenceRecord]


__all__ = [
    "Brief",
    "CycleResponse",
    "GraphNode",
    "GraphResponse",
    "GraphTie",
    "IntelligenceRecord",
    "Jurisdiction",
    "NodeDetail",
    "Page",
    "RecordPage",
    "Signal",
    "SignalPage",
]
