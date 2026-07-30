"""
Political-web registry.

A dangling tie would produce a record linked to a node that does not exist, which must
never reach publication -- hence `validate` runs in CI rather than only by hand.
"""

from __future__ import annotations

from pdx1.graph import (
    ALIASES,
    DISTRICTS,
    ENTITIES,
    JURISDICTIONS,
    NODES,
    OFFICIALS,
    TIES,
    neighbors,
    nodes_by_group,
    ties_for,
    validate,
)
from pdx1.models import NodeGroup, TieKind


def test_registry_has_no_dangling_ties_or_duplicates():
    assert validate() == []


def test_validate_catches_a_dangling_tie():
    from pdx1.models import Tie

    problems = validate(NODES, [*TIES, Tie(source="pge", target="ghost", kind=TieKind.TIE)])
    assert any("ghost" in p for p in problems)


def test_validate_catches_duplicate_ids():
    from pdx1.models import Node

    dupe = Node(id="pge", label="Duplicate", group=NodeGroup.ENTITY)
    problems = validate([*NODES, dupe], TIES)
    assert any("duplicate node id" in p for p in problems)


# ── Composition ──────────────────────────────────────────────────────────────


def test_node_counts_match_the_documented_taxonomy():
    assert len(JURISDICTIONS) == 8
    assert len(nodes_by_group(NodeGroup.JURISDICTION)) == 8
    assert len(nodes_by_group(NodeGroup.OFFICIAL)) == len(OFFICIALS)
    assert len(nodes_by_group(NodeGroup.ENTITY)) == len(ENTITIES)
    assert len(NODES) == len(JURISDICTIONS) + len(OFFICIALS) + len(ENTITIES)


def test_node_ids_are_unique():
    ids = [n.id for n in NODES]
    assert len(ids) == len(set(ids))


def test_every_tie_kind_is_represented():
    assert {t.kind for t in TIES} == set(TieKind)


def test_officials_are_role_based_not_named():
    """
    The module describes seats, not people. Every official label must read as a role.

    This is the constraint the whole module is built around -- a named individual here
    would turn a structural description into a characterisation of a person.
    """
    for official in OFFICIALS:
        assert any(
            token in official.label
            for token in ("Councilor", "Mayor", "Chair", "Commissioner", "President")
        ), official.label


def test_the_vacant_seat_is_flagged():
    vacant = [n for n in OFFICIALS if n.flag == "VACANT"]
    assert len(vacant) == 1
    assert vacant[0].id == "mcp"


# ── Ties ─────────────────────────────────────────────────────────────────────


def test_every_official_holds_exactly_one_seat():
    for official in OFFICIALS:
        seats = [t for t in ties_for(official.id) if t.kind is TieKind.SEAT]
        assert len(seats) == 1, official.id


def test_seat_ties_point_at_jurisdictions():
    jurisdiction_ids = {n.id for n in JURISDICTIONS}
    for tie in TIES:
        if tie.kind is TieKind.SEAT:
            assert tie.target in jurisdiction_ids


def test_ties_for_finds_both_directions():
    found = ties_for("pge")
    assert any(t.source == "pge" for t in found)
    assert any(t.target == "pge" for t in found)


def test_neighbors_can_filter_by_kind():
    regulated = neighbors("portland", TieKind.REGULATES)
    assert "pge" in regulated
    assert "pwb" not in regulated  # operated, not regulated


def test_neighbors_deduplicates():
    result = neighbors("metro")
    assert len(result) == len(set(result))


def test_unknown_node_has_no_ties():
    assert ties_for("no_such_node") == ()
    assert neighbors("no_such_node") == ()


def test_disclosure_ties_exist_and_some_are_flagged():
    disclosures = [t for t in TIES if t.kind is TieKind.DISCLOSURE]
    assert disclosures
    assert any(t.flagged for t in disclosures)


# ── Aliases ──────────────────────────────────────────────────────────────────


def test_every_alias_points_at_a_real_node():
    ids = {n.id for n in NODES}
    for alias, node_id in ALIASES.items():
        assert node_id in ids, f"{alias} -> {node_id}"


# ── District roster ──────────────────────────────────────────────────────────


def test_roster_covers_the_bistate_metro():
    names = {j.name for j in DISTRICTS}
    assert {
        "Clark County",
        "Washington County",
        "City of Portland",
        "Multnomah County",
        "Clackamas County",
        "Metro Council",
    } <= names


def test_roster_spans_both_states():
    assert {j.state for j in DISTRICTS} == {"OR", "WA", "REGIONAL"}


def test_metro_council_president_is_recorded_vacant():
    metro = next(j for j in DISTRICTS if j.name == "Metro Council")
    president = next(s for s in metro.seats if s.district == "Pres")
    assert president.status == "vacant"


def test_every_jurisdiction_has_seats():
    assert all(j.seats for j in DISTRICTS)
