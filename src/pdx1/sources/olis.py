"""
OLIS -- Oregon Legislative Information System.

Bills, hearings and markup timing. Markup timing matters to this module: the interval
between a committee action and a related public announcement is a measurable structural
fact, and the pipeline records the interval without characterising it.
"""

from __future__ import annotations

import json
from datetime import datetime

from ..models import Signal, SourceType
from .base import SourceAdapter


class OlisAdapter(SourceAdapter):
    """Parses OLIS bill and committee-action records."""

    name = "OLIS"
    source_type = SourceType.OLIS
    credibility = 0.9

    def parse(self, raw: str) -> list[Signal]:
        records = json.loads(raw)
        signals: list[Signal] = []

        for rec in records:
            action_at = datetime.fromisoformat(rec["action_at"])
            sponsors = ", ".join(rec.get("sponsors", [])) or "not stated"
            text = (
                f"OLIS record for {rec['bill_id']}, {rec['title']}, in the "
                f"{rec['session']} session. The measure is sponsored by {sponsors} and "
                f"referred to the {rec['committee']} committee. The most recent recorded "
                f"action is {rec['action']} on {rec['action_at']}, moving the measure to "
                f"status {rec['status']}. Summary as published: {rec['summary']} "
                f"Subject areas recorded are "
                f"{', '.join(rec.get('subjects', [])) or 'not stated'}. "
                f"The measure affects jurisdictions "
                f"{', '.join(rec.get('jurisdictions', [])) or 'not stated'}."
            )

            signals.append(
                Signal(
                    source=self.name,
                    source_type=self.source_type,
                    text=text,
                    url=rec.get("url"),
                    author=rec.get("committee"),
                    published_at=action_at,
                    credibility=self.credibility,
                )
            )

        return signals
