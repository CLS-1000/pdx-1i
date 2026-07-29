from __future__ import annotations

from collections.abc import Sequence

from pdx1.models import Signal


class BriefPublisher:
    def render(self, signals: Sequence[Signal]) -> str:
        lines = [f"# Metro Citizens Brief ({len(signals)} signals)"]
        lines.extend(f"- {signal.title} [{signal.source}]" for signal in signals)
        return "\n".join(lines)
