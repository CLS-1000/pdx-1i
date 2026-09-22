"""
Network diagram for the PDF brief.

Draws the political web among a given set of node ids: shape carries `group`,
line style carries `kind`, and a disclosure tie is dashed. The layout is a
deterministic ring -- node ids are sorted and placed by index, so the same input
draws the same picture on every run. That matters more here than an aesthetically
optimal layout would: a brief is a published artifact, and two runs over the same
records must not produce visibly different diagrams.

Nodes come from `pdx1.graph`, which is role-based throughout -- "Metro Councilor
- D2", never a person. The diagram inherits that constraint rather than
restating it, because it never sees a name to draw.

What the picture claims is deliberately narrow. An edge is a documented
relationship between two bodies; it is not a finding, an allegation, or a
measure of anything. The caption says so on every render, because a diagram
invites a reading that prose does not.

Requires the `pdf` extra (reportlab), like the renderer that embeds it.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from ..graph import NODES, TIES
from ..models import Node, NodeGroup, Tie, TieKind

#: Caption rendered beneath every diagram. Not optional, and not configurable:
#: it is the sentence that keeps the drawing descriptive.
CAPTION = (
    "Documented relationships between public bodies and role-based seats. "
    "A line is a relationship of record, not a finding."
)

#: Ring geometry, in points.
_NODE_RADIUS = 13.0
_RING_INSET = 34.0

#: Dash pattern for disclosure ties. Everything else is solid.
_DISCLOSURE_DASH = (3, 2)


def nodes_for(entity_ids: Sequence[str]) -> tuple[Node, ...]:
    """
    Registry nodes matching *entity_ids*, in a stable order.

    Ids the registry does not hold are dropped rather than invented -- a brief may
    name an entity the political web does not model, and drawing a placeholder for
    it would assert a body that does not exist.
    """
    wanted = set(entity_ids)
    return tuple(sorted((n for n in NODES if n.id in wanted), key=lambda n: n.id))


def ties_among(node_ids: Sequence[str]) -> tuple[Tie, ...]:
    """Ties whose *both* ends are in *node_ids*, in a stable order."""
    present = set(node_ids)
    return tuple(
        sorted(
            (t for t in TIES if t.source in present and t.target in present),
            key=lambda t: (t.source, t.target, t.kind.value),
        )
    )


def _ring_positions(count: int, width: float, height: float) -> list[tuple[float, float]]:
    """Evenly spaced points on a ring, starting at twelve o'clock, going clockwise."""
    cx, cy = width / 2.0, height / 2.0
    if count == 1:
        return [(cx, cy)]
    radius = max(min(width, height) / 2.0 - _RING_INSET, 1.0)
    out = []
    for i in range(count):
        angle = math.pi / 2.0 - (2.0 * math.pi * i / count)
        out.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return out


def _shape_for(node: Node, x: float, y: float, shapes, colors):
    """Diamond for a jurisdiction, square for a seat, circle for an entity."""
    r = _NODE_RADIUS
    common = {"fillColor": colors.white, "strokeColor": colors.black, "strokeWidth": 0.9}
    if node.group is NodeGroup.JURISDICTION:
        return shapes.Polygon([x, y + r, x + r, y, x, y - r, x - r, y], **common)
    if node.group is NodeGroup.OFFICIAL:
        side = r * 1.6
        return shapes.Rect(x - side / 2, y - side / 2, side, side, **common)
    return shapes.Circle(x, y, r, **common)


def build_network_drawing(
    entity_ids: Sequence[str],
    *,
    width: float = 6.5 * 72,
    height: float = 3.2 * 72,
):
    """
    A reportlab `Drawing` of the registry ties among *entity_ids*.

    Returns `None` when there is nothing worth drawing -- fewer than two known
    nodes, or no tie between the ones that are known. An empty frame would read as
    "no relationships exist" rather than "this brief names bodies the web does not
    connect", and those are different statements.

    Raises `ImportError` if reportlab is missing, matching `render_brief_pdf`.
    """
    try:
        from reportlab.graphics import shapes
        from reportlab.lib import colors
    except ImportError as exc:  # pragma: no cover - mirrors render_brief_pdf
        raise ImportError(
            "reportlab is required for network diagrams -- "
            "install it with: pip install 'pdx-1i[pdf]'"
        ) from exc

    nodes = nodes_for(entity_ids)
    if len(nodes) < 2:
        return None

    ties = ties_among([n.id for n in nodes])
    if not ties:
        return None

    drawing = shapes.Drawing(width, height)
    at = dict(zip([n.id for n in nodes], _ring_positions(len(nodes), width, height)))

    # Edges first, so node glyphs sit on top of the lines rather than under them.
    for tie in ties:
        x1, y1 = at[tie.source]
        x2, y2 = at[tie.target]
        line = shapes.Line(x1, y1, x2, y2)
        line.strokeColor = colors.black
        line.strokeWidth = 1.1 if tie.kind is TieKind.SEAT else 0.7
        if tie.kind is TieKind.DISCLOSURE:
            line.strokeDashArray = list(_DISCLOSURE_DASH)
        drawing.add(line)

    for node in nodes:
        x, y = at[node.id]
        drawing.add(_shape_for(node, x, y, shapes, colors))
        label = shapes.String(x, y - _NODE_RADIUS - 9.0, node.label, textAnchor="middle")
        label.fontName = "Helvetica"
        label.fontSize = 6.0
        label.fillColor = colors.black
        drawing.add(label)

    return drawing
