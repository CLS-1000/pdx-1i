"""
SEI -- Statements of Economic Interest, filed with the Oregon Government Ethics
Commission.

An SEI declares a public official's economic interests. Declaring an interest is what
the form is for: a disclosure is a completed legal obligation, not a finding. Adapter
text says what was declared and when, and stops there.

Officials are carried as the seat they hold, not as named individuals -- consistent with
the rest of the module.
"""

from __future__ import annotations

import json
from datetime import datetime

from ..models import Signal, SourceType
from .base import LiveSourceAdapter


class SeiAdapter(LiveSourceAdapter):
    """Parses SEI filings and amendments."""

    name = "SEI"
    source_type = SourceType.SEI
    credibility = 0.85
    # Oregon Government Ethics Commission SEI public filings (JSON export).
    feed_url = "https://ogec.oregon.gov/sei/api/public/filings.json"

    def parse(self, raw: str) -> list[Signal]:
        records = json.loads(raw)
        signals: list[Signal] = []

        for rec in records:
            filed_at = datetime.fromisoformat(rec["filed_at"])
            interests = rec.get("interests", [])
            rendered = (
                "; ".join(
                    f"{i['kind']} -- {i['description']} ({i.get('entity', 'not stated')})"
                    for i in interests
                )
                or "none declared"
            )
            text = (
                f"Statement of economic interest {rec['filing_id']} for the seat "
                f"{rec['seat']} on {rec['jurisdiction']}, covering calendar year "
                f"{rec['year']}, filed {rec['filed_at']} as a "
                f"{rec.get('filing_type', 'original')} filing. Declared interests: "
                f"{rendered}. The filing lists "
                f"{len(interests)} declared interest entries. "
                f"Prior-year filing for the same seat is "
                f"{rec.get('prior_filing_id') or 'not on record'}. "
                f"Filing status is recorded as {rec.get('status', 'accepted')}."
            )

            signals.append(
                Signal(
                    source=self.name,
                    source_type=self.source_type,
                    text=text,
                    url=rec.get("url"),
                    author=rec.get("jurisdiction"),
                    published_at=filed_at,
                    credibility=self.credibility,
                )
            )

        return signals
