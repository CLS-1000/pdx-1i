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

# Socrata column names for each canonical field, in preference order.
#
# UNVERIFIED. The dataset could not be fetched from the environment this mapping was
# written in, so these names come from a prior PDX-1i implementation rather than from
# a real response. Matching is case- and punctuation-insensitive. Verify against a live
# response before treating output as authoritative; a name matching nothing leaves its
# field empty rather than raising.
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "receipt_id": ("id", "receipt id", "transaction id", "report number"),
    "recipient": ("filer name", "filer", "recipient", "candidate"),
    "recipient_type": ("filer type", "recipient type", "office"),
    "jurisdiction": ("jurisdiction", "legislative district", "county"),
    "election_cycle": ("election year", "election cycle", "cycle"),
    "contributor": ("contributor name", "contributor", "donor"),
    "contributor_city": ("contributor city", "city"),
    "contributor_state": ("contributor state", "state"),
    "contributor_type": ("contributor type", "cash or in kind", "code"),
    "amount": ("amount", "contribution amount"),
    "aggregate": ("aggregate amount", "aggregate"),
    "contribution_date": ("receipt date", "contribution date", "date"),
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
    # VERIFIED WRONG: HTTP 404 on a live run, 2026-08-06. `data.wa.gov` is the right
    # host and Socrata is the right shape, but this dataset identifier is not. Find the
    # current one in the portal's dataset catalogue; the paging and mapping below are
    # independent of it.
    feed_url = "https://data.wa.gov/resource/tijg-9uu3.json"

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
                params={"$limit": PAGE_SIZE, "$offset": page * PAGE_SIZE},
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
            url=rec.get("url") or None,
            author=rec.get("recipient") or None,
            published_at=filed_at,
            credibility=self.credibility,
        )
