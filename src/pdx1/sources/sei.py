"""
SEI -- Statements of Economic Interest, filed with the Oregon Government Ethics
Commission.

An SEI declares a public official's economic interests. Declaring an interest is what
the form is for: a disclosure is a completed legal obligation, not a finding. Adapter
text says what was declared and when, and stops there.

Officials are carried as the seat they hold, not as named individuals -- consistent with
the rest of the module.

**OGEC publishes no machine-readable endpoint.** Oregon serves SEI data as periodic
downloads from a landing page, not as an API a single GET can consume, which is the
opposite of Washington's Socrata dataset next door in `wa_pdc.py`. So live mode for
this feed means pointing `fixture_path` at a downloaded export rather than letting the
adapter fetch: `feed_url` is the landing page, and a live fetch of it returns HTML that
`parse` will correctly reject. `parse` accepts the export shapes -- JSON array, JSONL,
or an object wrapping rows -- so a downloaded file drops straight in.
"""

from __future__ import annotations

import logging
from typing import Any

from ..models import Signal, SourceType
from .base import LiveSourceAdapter
from .normalize import (
    build_column_map,
    first_present,
    load_records,
    parse_timestamp,
    union_keys,
)

logger = logging.getLogger(__name__)

# Export column names for each canonical field, in preference order.
#
# UNVERIFIED. No export was available to the environment this mapping was written in,
# so these names come from a prior PDX-1i implementation rather than a real file.
# Matching is case- and punctuation-insensitive. A name matching nothing leaves its
# field empty rather than raising.
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "filing_id": ("filing id", "id", "statement id"),
    "seat": ("seat", "position", "role", "office"),
    "jurisdiction": ("jurisdiction", "agency", "public body"),
    "year": ("year", "calendar year", "reporting year"),
    "filing_type": ("filing type", "type", "statement type"),
    "filed_at": ("filed at", "filed date", "date filed", "submitted"),
    "prior_filing_id": ("prior filing id", "previous filing id"),
    "status": ("status", "filing status"),
    "url": ("url", "link", "source url"),
}

# Nested-interest field names. An export may spell the list or its members differently
# from the fixture without changing what the record means.
_INTEREST_KEYS = ("interests", "declared_interests", "entries")
_INTEREST_KIND = ("kind", "type", "category", "interest_type")
_INTEREST_DESC = ("description", "detail", "value", "name")
_INTEREST_ENTITY = ("entity", "organization", "source", "business")


class SeiAdapter(LiveSourceAdapter):
    """Parses SEI filings and amendments."""

    name = "SEI"
    source_type = SourceType.SEI
    credibility = 0.85
    # Landing page, not an API. See the module docstring -- a live fetch of this
    # returns HTML and `parse` will reject it rather than invent records.
    feed_url = "https://www.oregon.gov/ogec/pages/sei.aspx"

    def parse(self, raw: str) -> list[Signal]:
        text = raw.lstrip()
        if text[:1] not in ("[", "{"):
            # HTML from the landing page, or anything else non-JSON. Say so plainly
            # rather than returning an empty list that reads as "nothing was filed".
            raise ValueError(
                f"{self.name}: payload is not a JSON or JSONL export. OGEC publishes no "
                f"API -- download an export and pass it as fixture_path. See the module "
                f"docstring."
            )

        rows = load_records(raw)
        if not rows:
            return []

        columns = build_column_map(union_keys(rows), _FIELD_ALIASES)
        signals: list[Signal] = []
        undated = 0

        for row in rows:
            rec = {
                canonical: row.get(columns.get(canonical, canonical), row.get(canonical, ""))
                for canonical in _FIELD_ALIASES
            }
            signal = self._to_signal(rec, row)
            if signal is None:
                undated += 1
                continue
            signals.append(signal)

        if undated:
            logger.info("%s: skipped %d undated filing(s)", self.name, undated)
        if rows and not signals:
            logger.warning(
                "%s: %d row(s) read but none carried a readable date -- "
                "check _FIELD_ALIASES against the export schema",
                self.name,
                len(rows),
            )
        return signals

    def _to_signal(self, rec: dict[str, Any], row: dict[str, Any]) -> Signal | None:
        """Render one filing, or None when it cannot be dated."""
        filed_at = parse_timestamp(rec.get("filed_at"))
        if filed_at is None:
            return None

        interests = self._interests(row)
        rendered = (
            "; ".join(
                f"{i['kind']} -- {i['description']} ({i['entity']})" for i in interests
            )
            or "none declared"
        )

        text = (
            f"Statement of economic interest {rec.get('filing_id') or 'not stated'} for "
            f"the seat {rec.get('seat') or 'not stated'} on "
            f"{rec.get('jurisdiction') or 'not stated'}, covering calendar year "
            f"{rec.get('year') or 'not stated'}, filed {filed_at.isoformat()} as a "
            f"{rec.get('filing_type') or 'original'} filing. Declared interests: "
            f"{rendered}. The filing lists {len(interests)} declared interest entries. "
            f"Prior-year filing for the same seat is "
            f"{rec.get('prior_filing_id') or 'not on record'}. "
            f"Filing status is recorded as {rec.get('status') or 'accepted'}."
        )

        return Signal(
            source=self.name,
            source_type=self.source_type,
            text=text,
            url=rec.get("url") or None,
            author=rec.get("jurisdiction") or None,
            published_at=filed_at,
            credibility=self.credibility,
        )

    def _interests(self, row: dict[str, Any]) -> list[dict[str, str]]:
        """
        Normalise the declared-interest list.

        A malformed entry is rendered as "not stated" rather than dropped: the count of
        declared entries is itself part of what the filing says, so silently shrinking
        the list would misreport the record.
        """
        raw_list = first_present(row, *_INTEREST_KEYS) or []
        if not isinstance(raw_list, list):
            return []

        normalised: list[dict[str, str]] = []
        for entry in raw_list:
            if not isinstance(entry, dict):
                normalised.append({"kind": "not stated", "description": str(entry), "entity": "not stated"})
                continue
            normalised.append(
                {
                    "kind": str(first_present(entry, *_INTEREST_KIND) or "not stated"),
                    "description": str(first_present(entry, *_INTEREST_DESC) or "not stated"),
                    "entity": str(first_present(entry, *_INTEREST_ENTITY) or "not stated"),
                }
            )
        return normalised
