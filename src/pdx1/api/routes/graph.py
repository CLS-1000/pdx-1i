"""
GET /graph — the political web: jurisdictions, official seats, monitored entities.

Serves the registry in `pdx1.graph` so a renderer can draw the force-directed web map
without reaching into the package. Node and tie taxonomies come across intact, because
they are what the drawing encodes: node shape by `group`, line style by `kind`, dashed
for `disclosure`.

Every node carries `record_count` — how many stored records mention it — so the map can
reflect actual activity rather than a static diagram. A count is the only claim this
endpoint makes. It describes how often a body appears in public filings and says nothing
about why, which is the same discipline the neutrality gates enforce on published prose.

Officials are role-based seats throughout ("Metro Councilor · D2"), never named
individuals. A renderer must not attach a person to one.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from ...graph import DISTRICTS, NODES, TIES, ties_for
from ...models import Node
from ..auth import require_api_key
from ..schemas import GraphNode, GraphResponse, GraphTie, Jurisdiction, NodeDetail

router = APIRouter(prefix="/graph", tags=["graph"])


def _to_node(node: Node, counts: dict[str, int]) -> GraphNode:
    return GraphNode(
        id=node.id,
        label=node.label,
        group=node.group,
        weight=node.weight,
        flag=node.flag,
        record_count=counts.get(node.id, 0),
    )


def _to_tie(tie) -> GraphTie:
    return GraphTie(
        source=tie.source, target=tie.target, kind=tie.kind, flagged=tie.flagged
    )


@router.get("", response_model=GraphResponse, dependencies=[Depends(require_api_key)])
def get_graph(request: Request) -> GraphResponse:
    """Return every node and tie, annotated with record activity."""
    counts = request.app.state.store.entity_record_counts()
    nodes = [_to_node(n, counts) for n in NODES]
    ties = [_to_tie(t) for t in TIES]
    return GraphResponse(
        nodes=nodes, ties=ties, node_count=len(nodes), tie_count=len(ties)
    )


@router.get(
    "/districts",
    response_model=list[Jurisdiction],
    dependencies=[Depends(require_api_key)],
)
def get_districts() -> list[Jurisdiction]:
    """
    The district roster — each jurisdiction and its elected seats.

    What the District Map panel draws. Seats carry a status (`seated`, `vacant`); where
    no verified holder is on record, the seat is listed by role.
    """
    return list(DISTRICTS)


@router.get(
    "/{node_id}", response_model=NodeDetail, dependencies=[Depends(require_api_key)]
)
def get_node(
    request: Request,
    node_id: str,
    limit: int = Query(default=50, ge=1, le=500),
) -> NodeDetail:
    """One node with its ties, its neighbours, and the records that mention it."""
    node = next((n for n in NODES if n.id == node_id), None)
    if node is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No node with id {node_id!r}.",
        )

    store = request.app.state.store
    counts = store.entity_record_counts()
    ties = ties_for(node_id)

    neighbor_ids = {t.target if t.source == node_id else t.source for t in ties}
    neighbors = [_to_node(n, counts) for n in NODES if n.id in neighbor_ids]

    return NodeDetail(
        node=_to_node(node, counts),
        ties=[_to_tie(t) for t in ties],
        neighbors=neighbors,
        records=store.records_for_entity(node_id, limit=limit),
    )
