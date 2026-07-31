"""
ui/webmap.html — structural guards.

The page is not exercised end to end here (no browser in CI), but two properties
are worth pinning in code because losing either would be easy and consequential.

1. It must render only what the engine serves. A renderer with its own baked-in
   node list drifts from the registry silently, and the drift is invisible: the
   page keeps drawing a plausible graph long after it stops matching what the
   store holds.

2. It must not carry names of individuals or characterising language. The registry
   deliberately holds role-based seats, and every neutrality guarantee upstream is
   void if the last mile re-attaches a person, a party, or an adjective to a node.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pdx1.graph import NODES

WEBMAP = Path(__file__).resolve().parents[1] / "ui" / "webmap.html"


@pytest.fixture(scope="module")
def source() -> str:
    assert WEBMAP.exists(), f"{WEBMAP} is missing"
    return WEBMAP.read_text(encoding="utf-8")


# ── Data comes from the API ──────────────────────────────────────────────────


def test_it_fetches_the_graph_endpoint(source):
    assert "/graph" in source
    assert "fetch(" in source


def test_it_fetches_node_detail(source):
    assert re.search(r"\$\{API\}/graph/\$\{encodeURIComponent\(id\)\}", source)


def test_no_node_registry_is_hardcoded(source):
    """
    The registry's own ids must not appear as literals.

    `GET /graph` supplies them. Finding several inlined here would mean someone
    pasted a snapshot of the graph into the page.
    """
    inlined = [n.id for n in NODES if f'"{n.id}"' in source or f"'{n.id}'" in source]
    assert not inlined, f"node ids inlined instead of fetched: {inlined}"


def test_no_static_fallback_dataset(source):
    """
    When the API is unreachable the page must say so, not draw something else.

    A fallback dataset is worse than an error: it renders confidently while being
    disconnected from the store it claims to represent.
    """
    assert "there is no built-in dataset" in source
    assert "const nodes = [" not in source
    assert "const links = [" not in source


def test_no_external_scripts_or_fonts(source):
    """Self-contained, like ui/index.html — it has to work offline and air-gapped."""
    assert "<script src=" not in source
    assert "cdnjs" not in source and "cdn.jsdelivr" not in source
    assert "fonts.googleapis" not in source


# ── Neutrality ───────────────────────────────────────────────────────────────


def test_no_political_affiliation_labels(source):
    """Faction and party labels characterise the people holding a seat."""
    lowered = source.lower()
    for term in ("dsa", "democrat", "republican", "socialist", "progressive caucus"):
        assert term not in lowered, f"political affiliation label present: {term!r}"


def test_no_characterising_vocabulary(source):
    """
    Same discipline the tone gate applies to prose, applied to the last mile.

    The map draws structure. An adjective here would smuggle in the conclusion the
    whole pipeline refuses to draw.
    """
    lowered = source.lower()
    for word in (
        "controversial",
        "suspicious",
        "corrupt",
        "scandal",
        "wrongdoing",
        "under investigation",
        "recall effort",
        "allegation",
    ):
        assert word not in lowered, f"characterising language present: {word!r}"


def test_record_count_is_presented_without_ranking_language(source):
    """
    A count may be shown. It may not be dressed up as a score or a risk level.
    """
    lowered = source.lower()
    for word in ("risk score", "risk level", "threat", "severity", "suspicion"):
        assert word not in lowered, f"count reframed as a judgement: {word!r}"


# ── Taxonomy ─────────────────────────────────────────────────────────────────


def test_all_three_node_groups_are_encoded(source):
    """Shape carries the group, so each group needs a branch."""
    assert "polygon" in source  # jurisdiction, diamond
    assert "rect" in source  # official seat, square
    assert "circle" in source  # monitored entity


def test_all_five_tie_kinds_are_styled(source):
    from pdx1.models import TieKind

    for kind in TieKind:
        assert f"{kind.value}:" in source, f"tie kind not styled: {kind.value}"


def test_declared_interests_render_dashed(source):
    """The one tie kind that must be visually distinct — a disclosure is not a finding."""
    kind_block = source[source.index("const KIND"): source.index("const svg")]
    disclosure = kind_block[kind_block.index("disclosure:"):]
    assert "dash: '4 3'" in disclosure


def test_the_vacancy_flag_is_the_only_hue(source):
    """
    SPEC-1 is monochrome: hierarchy by white opacity, one hue for live status.
    """
    hexes = set(re.findall(r"#[0-9a-fA-F]{3,6}\b", source))
    allowed = {"#000", "#fff", "#ccc", "#999", "#666", "#00ff00", "#f66"}
    unexpected = {h for h in hexes if h.lower() not in allowed}
    assert not unexpected, f"non-monochrome colours introduced: {unexpected}"
