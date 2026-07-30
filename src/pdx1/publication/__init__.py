"""
Brief assembly.

Two entry points at different altitudes:

`IssueBuilder` is the real publication path -- it takes analyzed IntelligenceRecords and
runs every section through the tone and attribution gates before emitting a Brief.

`BriefPublisher` renders a plain listing of collected signals. It is deliberately
ungated, because it only restates titles of records already held and makes no claim of
its own. Anything that characterises or correlates goes through IssueBuilder.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..models import Brief, Signal
from .issue_builder import IssueBuilder, RejectedSection


class BriefPublisher:
    """Renders a signal listing, and assembled briefs, as markdown."""

    def render(self, signals: Sequence[Signal]) -> str:
        lines = [f"# Metro Citizens Brief ({len(signals)} signals)"]
        lines.extend(f"- {signal.display_title} [{signal.source}]" for signal in signals)
        return "\n".join(lines)

    def render_brief(self, brief: Brief) -> str:
        """Render an assembled, gate-cleared Brief."""
        lines = [
            f"# Metro Citizens Brief -- {brief.date}",
            "",
            brief.headline,
            "",
            brief.summary,
        ]
        for section in brief.sections:
            lines += ["", f"## {section.title}", "", section.body]
        lines += ["", f"Sources: {', '.join(brief.sources)}", f"Run: {brief.run_id}"]
        return "\n".join(lines)


__all__ = ["BriefPublisher", "IssueBuilder", "RejectedSection"]
