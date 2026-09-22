"""
The SPEC-1 brief format, and the network diagram that rides in it.

Two things are pinned here. The **format**: a brief renders as
`title · date`, a synopsis, a verified-signal count, `[i/total]` numbered
sections and a `[total/total]` footer carrying `run_id` and cycle -- the shape
SPEC-1's `WorldStateBrief` publisher emits, so a reader moving between the two
products reads the same document. And the **diagram**: what it draws, what it
refuses to draw, and that it draws the same picture twice for the same input.

Assertions run against text extracted from a real rendered PDF rather than
against the story list, because the story is an implementation detail and the
page is the artifact. Nothing touches the network; every file lands in tmp_path.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytest.importorskip("reportlab", reason="PDF rendering needs the pdf extra")


def _brief(**over):
    from pdx1.models import Brief, BriefSection

    fields = dict(
        brief_id="brief_fmt_001",
        run_id="pdx1_2026_0922_060000",
        date="2026-09-22",
        headline="Two bodies filed this cycle",
        summary="Routine filings cleared the four-gate filter.",
        sections=[
            BriefSection(title="Under review", body="- [INVESTIGATE] rec_abc"),
            BriefSection(title="Watch list", body="- [MONITOR] rec_def"),
        ],
        confidence=0.6,
        sources=["OLIS", "WA_PDC"],
        produced_at=datetime(2026, 9, 22, 6, 0, 0, tzinfo=timezone.utc),
    )
    fields.update(over)
    return Brief(**fields)


def _text(tmp_path, brief, **kw) -> str:
    """Render *brief* and return the text of page one."""
    from pdx1.publication.pdf_renderer import render_brief_pdf

    reader = pytest.importorskip("pypdf", reason="text extraction needs pypdf")
    out = render_brief_pdf(brief, tmp_path / "brief.pdf", **kw)
    return "\n".join(p.extract_text() for p in reader.PdfReader(str(out)).pages)


# ── The SPEC-1 brief format ──────────────────────────────────────────────────


def test_masthead_pairs_title_with_date(tmp_path):
    from pdx1.publication.pdf_renderer import BRIEF_TITLE

    assert f"{BRIEF_TITLE} · 2026-09-22" in _text(tmp_path, _brief())


def test_synopsis_follows_the_masthead(tmp_path):
    assert "Two bodies filed this cycle" in _text(tmp_path, _brief())


def test_verified_signal_count_matches_sections(tmp_path):
    assert "↓ 2 verified signals" in _text(tmp_path, _brief())


def test_verified_signal_count_is_singular_for_one(tmp_path):
    from pdx1.models import BriefSection

    brief = _brief(sections=[BriefSection(title="Only", body="- one")])
    text = _text(tmp_path, brief)
    assert "↓ 1 verified signal" in text
    assert "verified signals" not in text


def test_sections_are_numbered_over_the_total(tmp_path):
    """Two sections plus the footer is a total of three; the header is unnumbered."""
    text = _text(tmp_path, _brief())
    assert "[1/3] UNDER REVIEW" in text
    assert "[2/3] WATCH LIST" in text


def test_footer_closes_the_count_and_carries_provenance(tmp_path):
    text = _text(tmp_path, _brief())
    assert "[3/3] run_id: pdx1_202" in text          # run_id truncated to 8
    assert "cycle: 2026-09-22T06:00:00Z" in text
    assert "sources: OLIS, WA_PDC" in text


def test_archive_line_is_omitted_while_no_url_is_set(tmp_path):
    """No invented URL ships in a brief. See ARCHIVE_URL's comment."""
    from pdx1.publication import pdf_renderer

    assert pdf_renderer.ARCHIVE_URL == ""
    assert "methodology + archive" not in _text(tmp_path, _brief())


def test_archive_line_appears_once_a_url_exists(tmp_path, monkeypatch):
    from pdx1.publication import pdf_renderer

    monkeypatch.setattr(pdf_renderer, "ARCHIVE_URL", "https://example.invalid/method")
    assert "methodology + archive: https://example.invalid/method" in _text(
        tmp_path, _brief()
    )


# ── Record text is data, not markup ──────────────────────────────────────────


def test_markup_characters_in_a_brief_survive_rendering(tmp_path):
    """
    `Paragraph` parses a mini-HTML markup, and public records contain `&` and `<`.

    Before the format landed nothing was escaped, so an agency name with an
    ampersand either raised out of `doc.build` or silently swallowed the rest of
    the line.
    """
    from pdx1.models import BriefSection

    brief = _brief(
        headline="Parks & Recreation filed",
        summary="Budget <draft> circulated.",
        sections=[BriefSection(title="Ways & Means", body="- A & B <redacted>")],
    )
    text = _text(tmp_path, brief)

    assert "Parks & Recreation filed" in text
    assert "Budget <draft> circulated." in text
    assert "WAYS & MEANS" in text
    assert "A & B <redacted>" in text


# ── The network diagram ──────────────────────────────────────────────────────


def test_diagram_is_absent_unless_entity_ids_are_passed(tmp_path):
    assert "Relationship map" not in _text(tmp_path, _brief())


def test_diagram_appears_for_connected_entities(tmp_path):
    text = _text(tmp_path, _brief(), entity_ids=["metro", "multco", "washco", "trimet"])
    assert "Relationship map" in text


def test_diagram_caption_says_a_line_is_not_a_finding(tmp_path):
    from pdx1.publication.network_diagram import CAPTION

    text = _text(tmp_path, _brief(), entity_ids=["metro", "multco", "washco", "trimet"])
    assert "not a finding" in CAPTION
    assert CAPTION.split(". ")[-1].rstrip(".") in text


def test_diagram_sits_above_the_footer(tmp_path):
    """The footer closes the document; a diagram after it would read as an appendix."""
    text = _text(tmp_path, _brief(), entity_ids=["metro", "multco", "washco", "trimet"])
    assert text.index("Relationship map") < text.index("[3/3] run_id:")


def test_unknown_entity_ids_are_dropped_not_invented(tmp_path):
    from pdx1.publication.network_diagram import nodes_for

    assert nodes_for(["metro", "not_a_real_body"]) == nodes_for(["metro"])


def test_no_drawing_for_fewer_than_two_known_nodes():
    from pdx1.publication.network_diagram import build_network_drawing

    assert build_network_drawing(["metro"]) is None
    assert build_network_drawing([]) is None


def test_no_drawing_when_known_nodes_share_no_tie():
    """
    An empty frame would read as "these bodies are unconnected", which is a
    claim. Drawing nothing says only that this brief has no map.
    """
    from itertools import combinations

    from pdx1.graph import NODES
    from pdx1.publication.network_diagram import build_network_drawing, ties_among

    pair = next(
        (
            (a.id, b.id)
            for a, b in combinations(NODES, 2)
            if not ties_among([a.id, b.id])
        ),
        None,
    )
    assert pair is not None, "registry has no untied pair to exercise this branch"
    assert build_network_drawing(list(pair)) is None


def test_ties_among_requires_both_ends_present():
    from pdx1.graph import TIES
    from pdx1.publication.network_diagram import ties_among

    tie = TIES[0]
    assert tie in ties_among([tie.source, tie.target])
    assert tie not in ties_among([tie.source])


def test_layout_is_deterministic():
    """A published artifact must not redraw differently between identical runs."""
    from pdx1.publication.network_diagram import build_network_drawing

    ids = ["metro", "multco", "washco", "trimet"]
    first = build_network_drawing(ids)
    second = build_network_drawing(list(reversed(ids)))

    assert first is not None and second is not None
    assert _geometry(first) == _geometry(second)


def _geometry(drawing) -> list[tuple]:
    """Positional signature of a drawing, rounded past float noise."""
    out = []
    for shape in drawing.contents:
        coords = tuple(
            round(getattr(shape, attr), 3)
            for attr in ("x", "y", "cx", "cy", "x1", "y1", "x2", "y2")
            if hasattr(shape, attr)
        )
        out.append((type(shape).__name__, coords))
    return out
