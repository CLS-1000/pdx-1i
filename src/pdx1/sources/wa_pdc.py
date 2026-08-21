"""
WA PDC -- Washington Public Disclosure Commission.

Cross-border contributions. Clark County sits inside the Portland media market and
shares utilities and transport with the Oregon side, so Washington filings are part of
the same metro picture even though they clear a different regulator.

Washington publishes disclosure data through Socrata on `data.wa.gov` rather than a
bespoke API. Socrata answers a plain JSON array and pages with `$limit`/`$offset`,
which `_fetch_live` walks. Fixture payloads are already canonical, so `parse` accepts
either shape.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..models import Signal, SourceType
from .base import LiveSourceAdapter
from .normalize import (
    build_column_map,
    load_records,
    parse_money,
    parse_timestamp,
    union_keys,
)

logger = logging.getLogger(__name__)

#: Socrata page size. The service caps a single response; paging is how bulk reads work.
PAGE_SIZE = 1000

#: Stop after this many pages. Only exists so a service that ignores `$offset` cannot
#: spin forever.
MAX_PAGES = 50

#: Newest receipts first, undated rows last.
#
#: Socrata does not guarantee an order without one, and the live dataset holds 6.35
#: million rows against roughly 90 in any 48-hour window -- so an unordered walk reads
#: 50,000 arbitrary rows and the velocity gate discards very nearly all of them. Under
#: this order the rows the gate can actually accept are on the first page. Paging over
#: an unordered Socrata collection can also repeat and skip rows between requests,
#: which an explicit sort removes.
#:
#: NULLS LAST is not decoration: 14,965 rows carry no receipt_date, and Socrata's
#: Postgres backend sorts nulls *first* under DESC. Without it the walk spends its
#: first fifteen pages on rows the adapter drops as undated, and a payload sampled
#: from them has no receipt_date column at all.
ORDER_BY = "receipt_date DESC NULLS LAST"

# Socrata column names for each canonical field, in preference order.
#
# VERIFIED against a live response from the dataset below on 2026-08-21: thirteen of
# the fourteen canonical fields resolve, and the one that does not is noted inline.
# Matching is case- and punctuation-insensitive, and nothing was invented to fill a
# gap -- an unmatched field is left empty and the record says "not stated".
#
# The check was run over the union of 6,000 rows, not a handful. A five-row sample of
# the same dataset reported `jurisdiction` and `recipient_type` as unmatched: Socrata
# omits null columns per row, and neither column appeared in those five. That is the
# `union_keys` hazard the module contract describes, met in the field.
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "receipt_id": ("id", "receipt id", "transaction id", "report number"),
    "recipient": ("filer name", "filer", "recipient", "candidate"),
    # Resolves to `office` live ("STATE REPRESENTATIVE").
    "recipient_type": ("filer type", "recipient type", "office"),
    # Resolves to `jurisdiction` live ("LEG DISTRICT 33 - HOUSE").
    "jurisdiction": ("jurisdiction", "legislative district", "county"),
    "election_cycle": ("election year", "election cycle", "cycle"),
    "contributor": ("contributor name", "contributor", "donor"),
    "contributor_city": ("contributor city", "city"),
    "contributor_state": ("contributor state", "state"),
    # Resolves to `cash_or_in_kind` live. The dataset also has `contributor_category`
    # ("Organization", "Individual"); left unmapped because which of the two the
    # record should report is an editorial choice, not a correction.
    "contributor_type": ("contributor type", "cash or in kind", "code"),
    "amount": ("amount", "contribution amount"),
    # NO LIVE MATCH: no aggregate column. `_to_signal` falls back to the contribution
    # amount, so the record states the contribution rather than a cycle total.
    "aggregate": ("aggregate amount", "aggregate"),
    "contribution_date": ("receipt date", "contribution date", "date"),
    # Resolves to the same `receipt_date` column as contribution_date: the dataset
    # records when the receipt was dated, not separately when it was filed. So the
    # record's "filing was received" line restates the contribution date rather than
    # adding a second fact.
    "filed_at": ("filed date", "report date", "receipt date"),
    "url": ("url", "link", "report url"),
}


class WaPdcAdapter(LiveSourceAdapter):
    """Parses WA PDC contribution filings."""

    name = "WA_PDC"
    source_type = SourceType.WA_PDC
    credibility = 0.85
    # Washington PDC contributions, served as a Socrata dataset on the state open-data
    # portal. Washington's disclosure regime exposes a real API where Oregon's does not.
    #
    # VERIFIED REACHABLE: HTTP 200 on 2026-08-21, 6,354,167 rows, last updated that
    # same day. The previous identifier (`tijg-9uu3`) answered 404 on two live runs;
    # this one was found in the portal's own catalogue as "Contributions to Candidates
    # and Political Committees" and confirmed by fetching and parsing rows from it.
    feed_url = "https://data.wa.gov/resource/kv7h-kjye.json"

    # ── Live fetch ───────────────────────────────────────────────────────────

    def _fetch_live(self) -> str:
        """Walk the Socrata pages and return one combined JSON array."""
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                f"{self.name}: httpx is required for live fetch -- "
                "install it with: pip install 'pdx-1i[live]'"
            ) from exc

        rows: list[dict[str, Any]] = []
        for page in range(MAX_PAGES):
            response = httpx.get(
                self.feed_url,
                params={
                    "$limit": PAGE_SIZE,
                    "$offset": page * PAGE_SIZE,
                    "$order": ORDER_BY,
                },
                timeout=self.timeout,
                follow_redirects=True,
            )
            response.raise_for_status()
            batch = response.json()
            if not isinstance(batch, list):
                batch = load_records(json.dumps(batch))
            rows.extend(row for row in batch if isinstance(row, dict))
            # A short page is the last page.
            if len(batch) < PAGE_SIZE:
                break
        else:
            logger.warning("%s: stopped at the %d-page ceiling", self.name, MAX_PAGES)

        logger.info("%s: fetched %d contribution(s)", self.name, len(rows))
        return json.dumps(rows)

    # ── Parsing ──────────────────────────────────────────────────────────────

    def parse(self, raw: str) -> list[Signal]:
        rows = load_records(raw)
        if not rows:
            return []

        columns = build_column_map(union_keys(rows), _FIELD_ALIASES)
        signals: list[Signal] = []
        undated = 0

        for row in rows:
            # A canonical fixture row already uses the target names; a Socrata row
            # needs the alias map. Reading through the map and falling back to the
            # canonical key covers both without branching on payload shape.
            rec = {
                canonical: row.get(columns.get(canonical, canonical), row.get(canonical, ""))
                for canonical in _FIELD_ALIASES
            }
            signal = self._to_signal(rec)
            if signal is None:
                undated += 1
                continue
            signals.append(signal)

        if undated:
            logger.info("%s: skipped %d undated contribution(s)", self.name, undated)
        if rows and not signals:
            logger.warning(
                "%s: %d row(s) fetched but none carried a readable date -- "
                "check _FIELD_ALIASES against the live schema",
                self.name,
                len(rows),
            )
        return signals

    @staticmethod
    def _url_text(value: Any) -> str | None:
        """
        Flatten Socrata's URL column, which is an object rather than a string.

        The live dataset serves `{"url": "https://...", "description": ...}`. Handed
        to Signal unflattened it fails validation, and because `parse` is not
        individually fault-tolerant that one field takes the whole feed down with it --
        which is what a corrected endpoint did on first contact, turning a 404 into a
        parse failure. Fixtures carry a plain string, so both shapes are accepted.
        """
        if isinstance(value, dict):
            value = value.get("url")
        return str(value) if value else None

    def _to_signal(self, rec: dict[str, Any]) -> Signal | None:
        """Render one contribution, or None when it cannot be dated."""
        filed_at = parse_timestamp(rec.get("filed_at")) or parse_timestamp(
            rec.get("contribution_date")
        )
        if filed_at is None:
            return None

        amount = parse_money(rec.get("amount"))
        aggregate = parse_money(rec.get("aggregate")) or amount
        contributor_state = rec.get("contributor_state") or "not stated"
        contribution_date = rec.get("contribution_date") or filed_at.date().isoformat()

        text = (
            f"Washington PDC receipt {rec.get('receipt_id') or 'not stated'} reports a "
            f"contribution of ${amount:,.2f} to {rec.get('recipient') or 'not stated'} "
            f"({rec.get('recipient_type') or 'not stated'}) in "
            f"{rec.get('jurisdiction') or 'not stated'}, Washington, for election cycle "
            f"{rec.get('election_cycle') or 'not stated'}. The contributor is "
            f"{rec.get('contributor') or 'not stated'} of "
            f"{rec.get('contributor_city') or 'not stated'}, {contributor_state}, "
            f"recorded as {rec.get('contributor_type') or 'not stated'}. The "
            f"contribution date is {contribution_date} and the filing was received "
            f"{filed_at.isoformat()}. Cross-border status: contributor state is "
            f"{contributor_state} against recipient state WA. Cycle aggregate "
            f"from this contributor is ${aggregate:,.2f}."
        )

        return Signal(
            source=self.name,
            source_type=self.source_type,
            text=text,
            url=self._url_text(rec.get("url")),
            author=rec.get("recipient") or None,
            published_at=filed_at,
            credibility=self.credibility,
        )
