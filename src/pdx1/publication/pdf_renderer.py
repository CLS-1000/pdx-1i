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

from pathlib import Path

from ..models import Brief


def render_brief_pdf(brief: Brief, path: Path | str) -> Path:
    """
    Render *brief* to *path* as a letter-format PDF.

    Returns the resolved output path. Creates parent directories if needed.
    Raises `ImportError` if reportlab is not installed.
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

    story = _build_story(brief, styles, Paragraph, Spacer, Table, TableStyle, colors, inch)
    doc.build(story)
    return out


# ── Story builders ────────────────────────────────────────────────────────────


def _build_story(brief, styles, Paragraph, Spacer, Table, TableStyle, colors, inch):
    story = []

    # ── Header ────────────────────────────────────────────────────────────────
    story.append(Paragraph("Metro Citizens Brief", styles["title"]))
    story.append(Paragraph(brief.date, styles["subtitle"]))
    story.append(Spacer(1, 6))
    story.append(Paragraph(brief.headline, styles["headline"]))
    story.append(Spacer(1, 4))
    story.append(Paragraph(brief.summary, styles["body"]))
    story.append(Spacer(1, 12))

    # ── Sections ──────────────────────────────────────────────────────────────
    for section in brief.sections:
        story.append(Paragraph(section.title, styles["section_title"]))
        story.append(Spacer(1, 4))
        for line in section.body.splitlines():
            line = line.strip()
            if line:
                story.append(Paragraph(line, styles["bullet"]))
        story.append(Spacer(1, 10))

    # ── Footer ────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 12))
    footer_data = [
        [
            Paragraph(f"Sources: {', '.join(brief.sources)}", styles["footer"]),
            Paragraph(
                f"Run: {brief.run_id} | {brief.produced_at.strftime('%Y-%m-%dT%H:%M:%SZ')}",
                styles["footer_right"],
            ),
        ]
    ]
    col = 3.5 * inch
    footer_table = Table(footer_data, colWidths=[col, col])
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
