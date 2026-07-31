"""
ORESTAR -- Oregon campaign-finance contributions.

Payload is a JSON array of contribution transactions. Each becomes one Signal whose
text is a flat description of the transaction: who filed, what was received, from whom,
and when. Descriptive only -- the adapter states what the filing says and attributes
nothing.
"""

from __future__ import annotations

import json
from datetime import datetime

from ..models import Signal, SourceType
from .base import LiveSourceAdapter


class OrestarAdapter(LiveSourceAdapter):
    """Parses ORESTAR contribution filings."""

    name = "ORESTAR"
    source_type = SourceType.ORESTAR
    # A filed contribution report is a primary public record.
    credibility = 0.9
    # Oregon Secretary of State campaign finance API (JSON export).
    feed_url = "https://sos.oregon.gov/elections/Documents/orestar_contributions.json"

    def parse(self, raw: str) -> list[Signal]:
        records = json.loads(raw)
        signals: list[Signal] = []

        for rec in records:
            filed_at = datetime.fromisoformat(rec["filed_at"])
            amount = float(rec["amount"])
            text = (
                f"ORESTAR transaction {rec['tran_id']} filed by committee "
                f"{rec['committee']} ({rec['committee_id']}) records a contribution of "
                f"${amount:,.2f} of type {rec['contribution_type']} received from "
                f"{rec['contributor']} of {rec['contributor_city']}, "
                f"{rec['contributor_state']}, employer or affiliation "
                f"{rec.get('contributor_employer') or 'not stated'}. The transaction "
                f"date is {rec['transaction_date']} and the filing was received by the "
                f"Secretary of State on {rec['filed_at']}. Aggregate contributions from "
                f"this contributor to this committee for the cycle total "
                f"${float(rec.get('aggregate', amount)):,.2f}. Purpose recorded as "
                f"{rec.get('purpose') or 'not stated'}."
            )

            signals.append(
                Signal(
                    source=self.name,
                    source_type=self.source_type,
                    text=text,
                    url=rec.get("url"),
                    author=rec.get("committee"),
                    published_at=filed_at,
                    credibility=self.credibility,
                )
            )

        return signals
