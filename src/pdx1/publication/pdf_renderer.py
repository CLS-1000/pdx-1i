"""
PDF newsletter renderer.

Renders a `Brief` as a letter-format PDF using ReportLab. The output is a
single file; the function is stateless (pure input → file on disk).

Requires the `pdf` extra::

    pip install "pdx-1i[pdf]"

Usage::

    from pdx1.publication.pdf_renderer import render_brief_pdf
    path = render_brief_pdf(brief, Path("output/brief.pdf"))

Layout
------
- Header  : brief_id, date, headline (bold)
- Body    : one section per BriefSection; each record as a bullet line
- Footer  : produced_at, run_id (small, right-aligned)

The renderer never produces prose of its own. Section bodies come from the
Brief model, which was assembled by IssueBuilder after passing the tone and
attribution gates. The PDF is a faithful rendering of that content.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from xml.sax.saxutils import escape

from ..models import Brief

#: Masthead. Paired with the date as "title · date", per the SPEC-1 brief format.
BRIEF_TITLE = "Metro Citizens Brief"

#: Trailing footer line, matching SPEC-1's "methodology + archive" pointer.
#:
#: Empty, and the line is omitted while it stays empty. SPEC-1 publishes to a
#: known archive; this project has no equivalent published URL, and inventing a
#: plausible one would put an address into every brief that nobody has checked
#: resolves. Set it when there is a real page to point at.
ARCHIVE_URL = ""


def esc(text: object) -> str:
    """
    Escape *text* for reportlab's `Paragraph`, which parses a mini-HTML markup.

    Brief content is drawn from public records, so an ampersand in an agency name
    or a `<` in quoted text is ordinary and must not be read as markup. The
    renderer escaped nothing before the brief format landed; a record containing
    one would have raised out of `doc.build` or silently dropped the rest of the
    line.
    """
    return escape(str(text))


def render_brief_pdf(
    brief: Brief,
    path: Path | str,
    *,
    entity_ids: Sequence[str] | None = None,
) -> Path:
    """
    Render *brief* to *path* as a letter-format PDF.

    Returns the resolved output path. Creates parent directories if needed.
    Raises `ImportError` if reportlab is not installed.

    Pass *entity_ids* -- the `entity_ids` of the records backing the brief -- to
    append a network diagram of the registry ties among them. Omitted, the PDF is
    byte-for-byte what it was before the diagram existed. It is a parameter rather
    than something derived from the brief because `Brief` carries record ids, not
    entity ids, and resolving one to the other needs the store the renderer
    deliberately does not hold.

    The diagram is skipped silently when it would claim nothing -- fewer than two
    known nodes, or no tie between them. See `network_diagram.build_network_drawing`.
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT, TA_RIGHT
        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as exc:
        raise ImportError(
            "reportlab is required for PDF rendering -- "
            "install it with: pip install 'pdx-1i[pdf]'"
        ) from exc

    out = Path(path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    styles = _make_styles(ParagraphStyle, colors, TA_LEFT, TA_RIGHT)

    doc = SimpleDocTemplate(
        str(out),
        pagesize=LETTER,
        leftMargin=inch,
        rightMargin=inch,
        topMargin=inch,
        bottomMargin=inch,
        title=f"Metro Citizens Brief — {brief.date}",
        author="PDX-1i Intelligence Engine",
        subject=brief.headline,
    )

    story = _build_story(
        brief, styles, Paragraph, Spacer, Table, TableStyle, colors, inch, entity_ids
    )
    doc.build(story)
    return out


def _append_network_diagram(story, entity_ids, styles, Paragraph, Spacer) -> None:
    """Append the diagram and its caption, or leave *story* untouched."""
    if not entity_ids:
        return

    from .network_diagram import CAPTION, build_network_drawing

    drawing = build_network_drawing(entity_ids)
    if drawing is None:
        return

    story.append(Spacer(1, 14))
    story.append(Paragraph("Relationship map", styles["section_title"]))
    story.append(Spacer(1, 6))
    story.append(drawing)
    story.append(Spacer(1, 4))
    story.append(Paragraph(CAPTION, styles["footer"]))


# ── Story builders ────────────────────────────────────────────────────────────


def _build_story(
    brief, styles, Paragraph, Spacer, Table, TableStyle, colors, inch, entity_ids=None
):
    story = []
    #: Sections plus the footer. The header is unnumbered, matching SPEC-1.
    total = len(brief.sections) + 1
    #: Distinct records the brief cites. This counted sections instead, which made a
    #: two-section brief over ten records announce "2 verified signals" directly under
    #: a synopsis saying it cleared ten -- the same page contradicting itself. Counted
    #: as a set because one record cited by two sections is one signal, and drawn from
    #: `source_record_ids` because that is what the document can vouch for: every id in
    #: the count also appears in a section body.
    signals = len({rid for s in brief.sections for rid in s.source_record_ids})

    # ── Header ────────────────────────────────────────────────────────────────
    story.append(Paragraph(f"{BRIEF_TITLE} · {esc(brief.date)}", styles["title"]))
    story.append(Spacer(1, 6))
    story.append(Paragraph(esc(brief.headline), styles["headline"]))
    story.append(Spacer(1, 4))
    story.append(Paragraph(esc(brief.summary), styles["body"]))
    story.append(Spacer(1, 6))
    story.append(
        Paragraph(
            f"↓ {signals} verified signal{'s' if signals != 1 else ''}",
            styles["subtitle"],
        )
    )
    story.append(Spacer(1, 12))

    # ── Sections ──────────────────────────────────────────────────────────────
    for i, section in enumerate(brief.sections, start=1):
        story.append(
            Paragraph(f"[{i}/{total}] {esc(section.title).upper()}", styles["section_title"])
        )
        story.append(Spacer(1, 4))
        for line in section.body.splitlines():
            line = line.strip()
            if line:
                story.append(Paragraph(esc(line), styles["bullet"]))
        story.append(Spacer(1, 10))

    _append_network_diagram(story, entity_ids, styles, Paragraph, Spacer)

    # ── Footer ────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 12))
    footer_lines = [
        f"[{total}/{total}] run_id: {esc(brief.run_id[:8])}",
        f"cycle: {brief.produced_at.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"sources: {esc(', '.join(brief.sources))}",
    ]
    if ARCHIVE_URL:
        footer_lines.append(f"methodology + archive: {esc(ARCHIVE_URL)}")

    footer_data = [[Paragraph("<br/>".join(footer_lines), styles["footer"])]]
    footer_table = Table(footer_data, colWidths=[7.0 * inch])
    footer_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEABOVE", (0, 0), (-1, 0), 0.5, colors.grey),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(footer_table)
    return story


def _make_styles(ParagraphStyle, colors, TA_LEFT, TA_RIGHT) -> dict:
    """Build the paragraph style dictionary used by the renderer."""
    navy = colors.HexColor("#1a1a2e")
    slate = colors.HexColor("#2c3e50")

    return {
        "title": ParagraphStyle(
            "PDXTitle",
            fontSize=20,
            leading=24,
            textColor=navy,
            spaceAfter=2,
            alignment=TA_LEFT,
            fontName="Helvetica-Bold",
        ),
        "subtitle": ParagraphStyle(
            "PDXSubtitle",
            fontSize=10,
            leading=13,
            textColor=colors.grey,
            spaceAfter=4,
            alignment=TA_LEFT,
        ),
        "headline": ParagraphStyle(
            "PDXHeadline",
            fontSize=13,
            leading=16,
            fontName="Helvetica-Bold",
            spaceAfter=4,
        ),
        "section_title": ParagraphStyle(
            "PDXSectionTitle",
            fontSize=11,
            leading=14,
            fontName="Helvetica-Bold",
            textColor=slate,
            spaceAfter=2,
        ),
        "body": ParagraphStyle(
            "PDXBody",
            fontSize=9,
            leading=13,
            spaceAfter=2,
        ),
        "bullet": ParagraphStyle(
            "PDXBullet",
            fontSize=8,
            leading=12,
            leftIndent=12,
            spaceAfter=1,
        ),
        "footer": ParagraphStyle(
            "PDXFooter",
            fontSize=7,
            textColor=colors.grey,
            alignment=TA_LEFT,
        ),
        "footer_right": ParagraphStyle(
            "PDXFooterRight",
            fontSize=7,
            textColor=colors.grey,
            alignment=TA_RIGHT,
        ),
    }
