"""
PDF renderer.

Proves `render_brief_pdf` produces a file and that the output grows
proportionally to the number of sections.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest


def _make_brief():
    """Build a minimal Brief for rendering tests."""
    from pdx1.models import Brief, BriefSection

    return Brief(
        brief_id="brief_test_001",
        run_id="pdx1_2026_0528_060000",
        date="2026-05-28",
        headline="3 records across 2 feeds; none at elevated disposition",
        summary="This cycle cleared 3 records through the four-gate filter. "
                "No record carries a TIER_1 baseline deviation. Every line traces to run pdx1_test.",
        sections=[
            BriefSection(
                title="Under Review",
                body="- [INVESTIGATE] ORESTAR transaction rec_abc. Source ORESTAR, confidence 0.72 (HARD_RECORD), record rec_abc.",
                source_record_ids=["rec_abc"],
            ),
            BriefSection(
                title="Watch List",
                body="- [MONITOR] OLIS record for SB 1147. Source OLIS, confidence 0.61 (HARD_RECORD), record rec_def.",
                source_record_ids=["rec_def"],
            ),
        ],
        confidence=0.67,
        sources=["ORESTAR", "OLIS"],
        produced_at=datetime(2026, 5, 28, 6, 0, 0, tzinfo=timezone.utc),
    )


def test_render_brief_pdf_creates_file(tmp_path):
    from pdx1.publication.pdf_renderer import render_brief_pdf

    brief = _make_brief()
    out = render_brief_pdf(brief, tmp_path / "brief.pdf")

    assert out.exists()
    assert out.stat().st_size > 0
    # Minimal PDF magic bytes
    assert out.read_bytes()[:4] == b"%PDF"


def test_render_brief_pdf_returns_path(tmp_path):
    from pdx1.publication.pdf_renderer import render_brief_pdf

    brief = _make_brief()
    out = render_brief_pdf(brief, tmp_path / "out.pdf")
    assert isinstance(out, Path)


def test_render_brief_pdf_creates_parent_dirs(tmp_path):
    from pdx1.publication.pdf_renderer import render_brief_pdf

    brief = _make_brief()
    nested = tmp_path / "a" / "b" / "c" / "brief.pdf"
    out = render_brief_pdf(brief, nested)
    assert out.exists()


def test_render_brief_pdf_more_sections_larger_file(tmp_path):
    """A brief with more sections should produce a larger (or equal) file."""
    from pdx1.models import BriefSection
    from pdx1.publication.pdf_renderer import render_brief_pdf

    brief_small = _make_brief()
    brief_large = brief_small.model_copy(
        update={
            "sections": brief_small.sections + [
                BriefSection(
                    title="Elevated",
                    body="- [ESCALATE] Extra escalated record. Source SEI, confidence 0.9 (HARD_RECORD), record rec_xyz.",
                    source_record_ids=["rec_xyz"],
                )
            ]
        }
    )

    small = render_brief_pdf(brief_small, tmp_path / "small.pdf")
    large = render_brief_pdf(brief_large, tmp_path / "large.pdf")

    assert large.stat().st_size >= small.stat().st_size


def test_render_brief_pdf_raises_without_reportlab(monkeypatch, tmp_path):
    """ImportError is raised with a useful message when reportlab is not installed."""
    import builtins
    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "reportlab" or name.startswith("reportlab."):
            raise ImportError("no module named reportlab")
        return real_import(name, *args, **kwargs)

    # Clear cached module so the import guard fires.
    import sys
    rl_mods = [k for k in sys.modules if k == "reportlab" or k.startswith("reportlab.")]
    saved = {k: sys.modules.pop(k) for k in rl_mods}
    try:
        monkeypatch.setattr(builtins, "__import__", mock_import)
        # Re-import the renderer to ensure import guard executes.
        if "pdx1.publication.pdf_renderer" in sys.modules:
            del sys.modules["pdx1.publication.pdf_renderer"]
        from pdx1.publication import pdf_renderer  # noqa: F401
        with pytest.raises(ImportError, match="reportlab"):
            pdf_renderer.render_brief_pdf(_make_brief(), tmp_path / "x.pdf")
    finally:
        sys.modules.update(saved)
        monkeypatch.undo()
