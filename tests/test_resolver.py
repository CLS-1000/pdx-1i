"""
Entity resolution.

The three passes must stay distinguishable -- `method` on the result is what makes a
resolution auditable after the fact.
"""

from __future__ import annotations

import pytest

from pdx1.graph import ALIASES, NODES
from pdx1.models import Node, NodeGroup
from pdx1.resolver import EntityResolver, normalize, token_key


@pytest.fixture
def resolver() -> EntityResolver:
    return EntityResolver(NODES, ALIASES)


# ── Normalization ────────────────────────────────────────────────────────────


def test_normalize_strips_punctuation_and_case():
    assert normalize("  NW Natural, Inc.  ") == "nw natural inc"


def test_token_key_is_order_insensitive():
    assert token_key("County of Multnomah") == token_key("Multnomah County")


def test_token_key_drops_noise_words():
    assert token_key("Portland Water Bureau") == token_key("Portland Water")


# ── The three passes ─────────────────────────────────────────────────────────


def test_exact_pass(resolver):
    hit = resolver.resolve("Portland General Electric")
    assert hit is not None
    assert hit.node_id == "pge"
    assert hit.method == "exact"


def test_exact_pass_is_case_insensitive(resolver):
    assert resolver.resolve("portland general electric").node_id == "pge"


def test_alias_resolves_exactly(resolver):
    hit = resolver.resolve("PGE")
    assert hit.node_id == "pge"
    assert hit.method == "exact"


def test_token_sort_pass(resolver):
    hit = resolver.resolve("County of Multnomah")
    assert hit.node_id == "multco"
    assert hit.method == "token-sort"


def test_substring_pass(resolver):
    hit = resolver.resolve("the OHSU Foundation board")
    assert hit.node_id == "ohsu"
    assert hit.method == "substring"


def test_unresolvable_name_returns_none(resolver):
    """Guessing is worse than admitting the name is unknown."""
    assert resolver.resolve("Acme Holdings of Nevada") is None


def test_empty_name_returns_none(resolver):
    assert resolver.resolve("   ") is None


def test_ambiguous_substring_returns_none():
    """Two plausible candidates means no answer, not a coin flip."""
    nodes = [
        Node(id="a", label="Riverside Energy Partners", group=NodeGroup.ENTITY),
        Node(id="b", label="Riverside Energy Holdings", group=NodeGroup.ENTITY),
    ]
    assert EntityResolver(nodes).resolve("Riverside Energy") is None


def test_node_ids_resolve_to_themselves(resolver):
    assert resolver.resolve("pge").node_id == "pge"


def test_alias_to_unknown_node_is_rejected():
    with pytest.raises(ValueError, match="unknown node"):
        EntityResolver(NODES, {"Ghost": "no_such_node"})


# ── Extraction ───────────────────────────────────────────────────────────────


def test_extract_finds_registry_entities(resolver):
    text = (
        "The Metro Council reviewed a filing naming Portland General Electric "
        "and NW Natural in the same reporting period."
    )
    found = {r.node_id for r in resolver.extract(text)}
    assert {"metro", "pge", "nwn"} <= found


def test_extract_prefers_the_longer_name(resolver):
    """'Portland General Electric' must not be swallowed by 'Portland'."""
    found = {r.node_id for r in resolver.extract("Portland General Electric filed.")}
    assert "pge" in found


def test_extract_respects_word_boundaries(resolver):
    """A name embedded inside a longer word is not a mention."""
    assert resolver.extract("Metrocentric planning theory") == []


def test_extract_returns_each_node_once(resolver):
    found = resolver.extract("TriMet and TriMet and TriMet again")
    assert [r.node_id for r in found].count("trimet") == 1


def test_extract_on_unrelated_text(resolver):
    assert resolver.extract("A recipe for sourdough bread.") == []


def test_resolve_all_drops_misses(resolver):
    hits = resolver.resolve_all(["PGE", "Nonexistent Corp", "NW Natural"])
    assert [h.node_id for h in hits] == ["pge", "nwn"]


def test_resolver_length_matches_node_count(resolver):
    assert len(resolver) == len(NODES)
