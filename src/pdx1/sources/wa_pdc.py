"""
WA PDC -- Washington Public Disclosure Commission.

Cross-border contributions. Clark County sits inside the Portland media market and
shares utilities and transport with the Oregon side, so Washington filings are part of
the same metro picture even though they clear a different regulator.
"""

from __future__ import annotations

import json
from datetime import datetime

from ..models import Signal, SourceType
from .base import LiveSourceAdapter


class WaPdcAdapter(LiveSourceAdapter):
    """Parses WA PDC contribution filings."""

    name = "WA_PDC"
    source_type = SourceType.WA_PDC
    credibility = 0.85
    # Washington PDC contributions API (JSON export).
    feed_url = "https://api.pdc.wa.gov/public/v1/contributions?format=json"

    def parse(self, raw: str) -> list[Signal]:
        records = json.loads(raw)
        signals: list[Signal] = []

        for rec in records:
            filed_at = datetime.fromisoformat(rec["filed_at"])
            amount = float(rec["amount"])
            text = (
                f"Washington PDC receipt {rec['receipt_id']} reports a contribution of "
                f"${amount:,.2f} to {rec['recipient']} ({rec['recipient_type']}) in "
                f"{rec['jurisdiction']}, Washington, for election cycle "
                f"{rec['election_cycle']}. The contributor is {rec['contributor']} of "
                f"{rec['contributor_city']}, {rec['contributor_state']}, recorded as "
                f"{rec.get('contributor_type', 'not stated')}. The contribution date is "
                f"{rec['contribution_date']} and the filing was received "
                f"{rec['filed_at']}. Cross-border status: contributor state is "
                f"{rec['contributor_state']} against recipient state WA. Cycle aggregate "
                f"from this contributor is ${float(rec.get('aggregate', amount)):,.2f}."
            )

            signals.append(
                Signal(
                    source=self.name,
                    source_type=self.source_type,
                    text=text,
                    url=rec.get("url"),
                    author=rec.get("recipient"),
                    published_at=filed_at,
                    credibility=self.credibility,
                )
            )

        return signals
