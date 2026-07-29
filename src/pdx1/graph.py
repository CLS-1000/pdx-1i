"""
The PDX-1i political web: jurisdictions, official seats, monitored entities, and the
ties between them.

The graph exists to make conflict-of-interest *structure* visible and legible. It shows
who sits on what, which bodies regulate or operate which entities, and where declared
interests cross those lines. It does not allege anything, and a tie is not a finding.

Officials are role-based seats -- "Metro Councilor - District 2", not a person's name.
That is a deliberate constraint carried through the whole module: a seat can be
described structurally without characterising whoever holds it.

The district roster is public record. Where a seat has no verified holder it is listed
by role.
"""

from __future__ import annotations

from collections.abc import Iterable

from .models import Jurisdiction, Node, NodeGroup, Seat, Tie, TieKind

# ── Nodes ────────────────────────────────────────────────────────────────────

JURISDICTIONS: tuple[Node, ...] = (
    Node(id="metro", label="Metro Council", group=NodeGroup.JURISDICTION, weight=1.0),
    Node(id="multco", label="Multnomah County", group=NodeGroup.JURISDICTION, weight=0.9),
    Node(id="washco", label="Washington County", group=NodeGroup.JURISDICTION, weight=0.8),
    Node(id="clackco", label="Clackamas County", group=NodeGroup.JURISDICTION, weight=0.7),
    Node(id="portland", label="City of Portland", group=NodeGroup.JURISDICTION, weight=1.0),
    Node(id="clarkwa", label="Clark County - WA", group=NodeGroup.JURISDICTION, weight=0.6),
    Node(id="trimet_b", label="TriMet Board", group=NodeGroup.JURISDICTION, weight=0.7),
    Node(id="port", label="Port of Portland", group=NodeGroup.JURISDICTION, weight=0.7),
)

OFFICIALS: tuple[Node, ...] = (
    Node(
        id="mcp",
        label="Metro Council President",
        group=NodeGroup.OFFICIAL,
        weight=1.0,
        flag="VACANT",
    ),
    Node(id="mc_d2", label="Metro Councilor - D2", group=NodeGroup.OFFICIAL, weight=0.6),
    Node(id="mc_d4", label="Metro Councilor - D4", group=NodeGroup.OFFICIAL, weight=0.6),
    Node(id="pdx_mayor", label="Portland Mayor", group=NodeGroup.OFFICIAL, weight=0.9),
    Node(id="pdx_c1", label="City Councilor - District 1", group=NodeGroup.OFFICIAL, weight=0.6),
    Node(id="pdx_c3", label="City Councilor - District 3", group=NodeGroup.OFFICIAL, weight=0.6),
    Node(id="mult_ch", label="Multnomah Chair", group=NodeGroup.OFFICIAL, weight=0.7),
    Node(id="wash_c2", label="Washington Commissioner - Position 2", group=NodeGroup.OFFICIAL, weight=0.5),
    Node(id="clack_c", label="Clackamas Chair", group=NodeGroup.OFFICIAL, weight=0.5),
    Node(id="clark_c", label="Clark Councilor - WA", group=NodeGroup.OFFICIAL, weight=0.4),
)

ENTITIES: tuple[Node, ...] = (
    Node(id="pge", label="Portland General Electric", group=NodeGroup.ENTITY, weight=0.9),
    Node(id="nwn", label="NW Natural", group=NodeGroup.ENTITY, weight=0.8),
    Node(id="pwb", label="Portland Water Bureau", group=NodeGroup.ENTITY, weight=0.7),
    Node(id="trimet", label="TriMet", group=NodeGroup.ENTITY, weight=0.9),
    Node(id="ppb", label="Portland Police Bureau", group=NodeGroup.ENTITY, weight=0.8),
    Node(id="ohsu", label="OHSU", group=NodeGroup.ENTITY, weight=0.8),
    Node(id="schn", label="Schnitzer / Radius", group=NodeGroup.ENTITY, weight=0.7),
    Node(id="pcef", label="Portland Clean Energy Fund", group=NodeGroup.ENTITY, weight=0.6),
    Node(id="home_fwd", label="Home Forward", group=NodeGroup.ENTITY, weight=0.5),
    Node(id="prosper", label="Prosper Portland", group=NodeGroup.ENTITY, weight=0.6),
    Node(id="zenith", label="Zenith Energy", group=NodeGroup.ENTITY, weight=0.6),
    Node(id="nike", label="Nike", group=NodeGroup.ENTITY, weight=0.5),
    Node(id="intel", label="Intel Corporation", group=NodeGroup.ENTITY, weight=0.6),
)

NODES: tuple[Node, ...] = JURISDICTIONS + OFFICIALS + ENTITIES

#: How sources name these bodies in practice. Registered explicitly rather than
#: inferred, so a resolution can always be traced to a declared alias.
ALIASES: dict[str, str] = {
    "PGE": "pge",
    "Portland General Electric Company": "pge",
    "Northwest Natural": "nwn",
    "Northwest Natural Gas Company": "nwn",
    "Oregon Health & Science University": "ohsu",
    "Oregon Health and Science University": "ohsu",
    "Tri-County Metropolitan Transportation District": "trimet",
    "Tri-Met": "trimet",
    "Metro": "metro",
    "Oregon Metro": "metro",
    "Metropolitan Service District": "metro",
    "Portland": "portland",
    "City of Portland, Oregon": "portland",
    "Multnomah": "multco",
    "Washington County, Oregon": "washco",
    "Clackamas": "clackco",
    "Clark County": "clarkwa",
    "Clark County, Washington": "clarkwa",
    "Port of Portland Commission": "port",
    "Intel": "intel",
    "Nike, Inc.": "nike",
    "PCEF": "pcef",
    "Clean Energy Fund": "pcef",
}

# ── Ties ─────────────────────────────────────────────────────────────────────

TIES: tuple[Tie, ...] = (
    # Seats
    Tie(source="mcp", target="metro", kind=TieKind.SEAT),
    Tie(source="mc_d2", target="metro", kind=TieKind.SEAT),
    Tie(source="mc_d4", target="metro", kind=TieKind.SEAT),
    Tie(source="pdx_mayor", target="portland", kind=TieKind.SEAT),
    Tie(source="pdx_c1", target="portland", kind=TieKind.SEAT),
    Tie(source="pdx_c3", target="portland", kind=TieKind.SEAT),
    Tie(source="mult_ch", target="multco", kind=TieKind.SEAT),
    Tie(source="wash_c2", target="washco", kind=TieKind.SEAT),
    Tie(source="clack_c", target="clackco", kind=TieKind.SEAT),
    Tie(source="clark_c", target="clarkwa", kind=TieKind.SEAT),
    # Operation. The TriMet Board is the governing body of the district it runs --
    # a governance relationship, not a seat. Seats connect an official to a body.
    Tie(source="trimet_b", target="trimet", kind=TieKind.OPERATES),
    Tie(source="metro", target="trimet", kind=TieKind.OPERATES),
    Tie(source="metro", target="pcef", kind=TieKind.OPERATES),
    Tie(source="portland", target="pwb", kind=TieKind.OPERATES),
    Tie(source="portland", target="ppb", kind=TieKind.OPERATES),
    Tie(source="portland", target="pcef", kind=TieKind.OPERATES),
    Tie(source="portland", target="prosper", kind=TieKind.OPERATES),
    Tie(source="multco", target="home_fwd", kind=TieKind.OPERATES),
    Tie(source="port", target="schn", kind=TieKind.OPERATES),
    Tie(source="port", target="zenith", kind=TieKind.OPERATES),
    # Regulation
    Tie(source="portland", target="pge", kind=TieKind.REGULATES),
    Tie(source="portland", target="nwn", kind=TieKind.REGULATES),
    Tie(source="portland", target="zenith", kind=TieKind.REGULATES),
    Tie(source="washco", target="nwn", kind=TieKind.REGULATES),
    Tie(source="clackco", target="pge", kind=TieKind.REGULATES),
    Tie(source="pge", target="pcef", kind=TieKind.REGULATES),
    # Affiliation
    Tie(source="multco", target="ohsu", kind=TieKind.TIE),
    Tie(source="multco", target="ppb", kind=TieKind.TIE),
    Tie(source="washco", target="nike", kind=TieKind.TIE),
    Tie(source="washco", target="intel", kind=TieKind.TIE),
    Tie(source="clarkwa", target="nwn", kind=TieKind.TIE),
    Tie(source="metro", target="trimet_b", kind=TieKind.TIE),
    Tie(source="port", target="intel", kind=TieKind.TIE),
    Tie(source="mult_ch", target="schn", kind=TieKind.TIE),
    Tie(source="mc_d4", target="trimet", kind=TieKind.TIE),
    Tie(source="ohsu", target="pcef", kind=TieKind.TIE),
    # Declared interests. A disclosure is a completed obligation, not a finding.
    Tie(source="mcp", target="pge", kind=TieKind.DISCLOSURE, flagged=True),
    Tie(source="pdx_mayor", target="ohsu", kind=TieKind.DISCLOSURE),
    Tie(source="pdx_c3", target="zenith", kind=TieKind.DISCLOSURE, flagged=True),
    Tie(source="wash_c2", target="intel", kind=TieKind.DISCLOSURE),
)

# ── District roster ──────────────────────────────────────────────────────────

DISTRICTS: tuple[Jurisdiction, ...] = (
    Jurisdiction(
        name="Clark County",
        state="WA",
        zone="north",
        seats=(
            Seat(district="Chair", role="Council Chair - at-large"),
            Seat(district="D1", role="Councilor"),
            Seat(district="D2", role="Councilor"),
            Seat(district="D3", role="Councilor"),
            Seat(district="D4", role="Councilor"),
        ),
    ),
    Jurisdiction(
        name="Washington County",
        state="OR",
        zone="west",
        seats=(
            Seat(district="Chair", role="Board Chair - at-large"),
            Seat(district="D1", role="Commissioner"),
            Seat(district="D2", role="Commissioner"),
            Seat(district="D3", role="Commissioner"),
            Seat(district="D4", role="Commissioner"),
        ),
    ),
    Jurisdiction(
        name="City of Portland",
        state="OR",
        zone="center",
        seats=(
            Seat(district="Mayor", role="Mayor - citywide"),
            Seat(district="D1", role="Councilors x3 - East"),
            Seat(district="D2", role="Councilors x3 - N/NE"),
            Seat(district="D3", role="Councilors x3 - SE"),
            Seat(district="D4", role="Councilors x3 - W/SW"),
        ),
    ),
    Jurisdiction(
        name="Multnomah County",
        state="OR",
        zone="east",
        seats=(
            Seat(district="Chair", role="Board Chair - at-large"),
            Seat(district="D1", role="Commissioner"),
            Seat(district="D2", role="Commissioner"),
            Seat(district="D3", role="Commissioner"),
            Seat(district="D4", role="Commissioner"),
        ),
    ),
    Jurisdiction(
        name="Clackamas County",
        state="OR",
        zone="south",
        seats=(
            Seat(district="Chair", role="Board Chair - at-large"),
            Seat(district="D1", role="Commissioner"),
            Seat(district="D2", role="Commissioner"),
            Seat(district="D3", role="Commissioner"),
            Seat(district="D4", role="Commissioner"),
        ),
    ),
    Jurisdiction(
        name="Metro Council",
        state="REGIONAL",
        zone="band",
        seats=(
            Seat(district="Pres", role="President - at-large", status="vacant"),
            Seat(district="D1", role="Councilor"),
            Seat(district="D2", role="Councilor"),
            Seat(district="D3", role="Councilor"),
            Seat(district="D4", role="Councilor"),
            Seat(district="D5", role="Councilor"),
            Seat(district="D6", role="Councilor - acting President"),
        ),
    ),
)


# ── Queries ──────────────────────────────────────────────────────────────────


def nodes_by_group(group: NodeGroup) -> tuple[Node, ...]:
    return tuple(n for n in NODES if n.group is group)


def ties_for(node_id: str) -> tuple[Tie, ...]:
    """Every tie touching a node, in either direction."""
    return tuple(t for t in TIES if t.source == node_id or t.target == node_id)


def neighbors(node_id: str, kind: TieKind | None = None) -> tuple[str, ...]:
    """IDs adjacent to a node, optionally filtered by tie kind."""
    out: list[str] = []
    for tie in ties_for(node_id):
        if kind is not None and tie.kind is not kind:
            continue
        out.append(tie.target if tie.source == node_id else tie.source)
    return tuple(dict.fromkeys(out))


def validate(nodes: Iterable[Node] = NODES, ties: Iterable[Tie] = TIES) -> list[str]:
    """
    Check the registry for dangling ties and duplicate node IDs.

    Called by the test suite. A tie pointing at a node that does not exist would produce
    a record linked to nothing, which must never reach publication.
    """
    problems: list[str] = []
    seen: set[str] = set()

    for node in nodes:
        if node.id in seen:
            problems.append(f"duplicate node id {node.id!r}")
        seen.add(node.id)

    for tie in ties:
        if tie.source not in seen:
            problems.append(f"tie source {tie.source!r} is not a known node")
        if tie.target not in seen:
            problems.append(f"tie target {tie.target!r} is not a known node")

    return problems
