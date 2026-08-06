"""
ORESTAR -- Oregon campaign-finance contributions.

Each transaction becomes one Signal whose text is a flat description of the filing:
who filed, what was received, from whom, and when. Descriptive only -- the adapter
states what the filing says and attributes nothing.

Two payload shapes are accepted, and `parse` detects which it has:

- **JSON array** — the checked-in fixture shape, already in canonical field names.
- **CSV** — the live shape. The Secretary of State publishes transactions as a bulk
  ZIP containing one CSV, so `_decode` unwraps the archive and `parse` reads the CSV.

Real exports do not use the fixture's field names, so `_COLUMN_ALIASES` maps the
canonical names onto the header spellings a real export may carry. See the note on
that table before trusting live output.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import zipfile
from datetime import datetime, timezone
from typing import Any

from ..models import Signal, SourceType
from .base import LiveSourceAdapter
from .normalize import build_column_map, parse_money, parse_timestamp

logger = logging.getLogger(__name__)

_ZIP_MAGIC = b"PK\x03\x04"

# Header spellings accepted for each canonical field, in preference order.
#
# UNVERIFIED. The bulk export could not be fetched from the development environment
# used to write this mapping, so these spellings are drawn from the two prior PDX-1i
# implementations rather than from a downloaded file. Matching is case- and
# punctuation-insensitive (see `normalize.header_key`), which absorbs spacing and
# casing differences but not genuinely different column names. Verify against a real
# export before treating live ORESTAR output as authoritative; a header that matches
# nothing leaves its field empty rather than raising.
_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "tran_id": ("tran id", "tranid", "transaction id", "id"),
    "committee": ("filer", "filer name", "committee", "committee name"),
    "committee_id": ("filer id", "filerid", "committee id"),
    "contributor": ("contributor payee", "contributor", "contributor name", "payee"),
    "contributor_city": ("city", "contributor city"),
    "contributor_state": ("state", "contributor state"),
    "contributor_employer": ("employer", "contributor employer", "occupation"),
    "contribution_type": ("sub type", "subtype", "contribution type", "book type"),
    "amount": ("amount", "transaction amount"),
    "aggregate": ("aggregate amount", "aggregate"),
    "transaction_date": ("tran date", "trandate", "transaction date", "date"),
    "filed_at": ("filed date", "filed at", "filed", "received date"),
    "purpose": ("purpose", "purpose of expenditure", "description"),
    "url": ("url", "link"),
}


class OrestarAdapter(LiveSourceAdapter):
    """Parses ORESTAR contribution filings."""

    name = "ORESTAR"
    source_type = SourceType.ORESTAR
    # A filed contribution report is a primary public record.
    credibility = 0.9
    # Oregon Secretary of State bulk transaction export -- a ZIP containing one CSV.
    # `{year}` is filled in at fetch time from the current calendar year.
    #
    # VERIFIED WRONG: HTTP 404 on a live run, 2026-08-06, for the 2026 file. Either the
    # path or the filename convention differs from what the prior implementation
    # recorded, or the annual file is not published under this name mid-year. The ZIP
    # and CSV handling below is independent of the URL and stays valid once it is
    # corrected; pass `year=` to try another year without a code change.
    feed_url = "https://sos.oregon.gov/elections/Documents/orestar/{year}_report_transactions.zip"

    def __init__(self, *args, year: int | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._year = year or datetime.now(timezone.utc).year
        # Bind the year into the instance URL so the base class fetches a real path.
        if "{year}" in type(self).feed_url:
            self.feed_url = type(self).feed_url.format(year=self._year)

    def _decode(self, response) -> str:
        """
        Unwrap the bulk ZIP into CSV text.

        A response that is not a ZIP is passed through as text, so a plain-CSV or
        JSON endpoint still works without a code change.
        """
        content = getattr(response, "content", None)
        if not isinstance(content, (bytes, bytearray)) or not content.startswith(_ZIP_MAGIC):
            return response.text

        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
            if not names:
                raise ValueError(
                    f"{self.name}: bulk archive contains no CSV (members: {archive.namelist()})"
                )
            if len(names) > 1:
                logger.info("%s: archive holds %d CSVs, reading %s", self.name, len(names), names[0])
            # utf-8-sig: government exports routinely carry a BOM.
            return archive.read(names[0]).decode("utf-8-sig", errors="replace")

    # ── Parsing ──────────────────────────────────────────────────────────────

    def parse(self, raw: str) -> list[Signal]:
        """Turn a JSON array (fixture) or CSV (live) payload into signals."""
        records = self._to_records(raw)
        signals: list[Signal] = []
        skipped = 0

        for rec in records:
            signal = self._to_signal(rec)
            if signal is None:
                skipped += 1
                continue
            signals.append(signal)

        if skipped:
            # Failure-first: a malformed row is dropped and counted, never fatal.
            logger.info("%s: skipped %d unreadable row(s)", self.name, skipped)
        return signals

    def _to_records(self, raw: str) -> list[dict[str, Any]]:
        """Normalise either payload shape into canonical-keyed dicts."""
        text = raw.lstrip()
        if text.startswith(("[", "{")):
            loaded = json.loads(text)
            # A JSON object payload may wrap the rows under a key.
            if isinstance(loaded, dict):
                for key in ("records", "transactions", "value", "data"):
                    if isinstance(loaded.get(key), list):
                        return loaded[key]
                return []
            return loaded
        return self._read_csv(text)

    def _read_csv(self, text: str) -> list[dict[str, Any]]:
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            return []

        columns = build_column_map(reader.fieldnames, _COLUMN_ALIASES)
        missing = [c for c in ("committee", "contributor", "amount") if c not in columns]
        if missing:
            # Loud, because it means the alias table has drifted from the real export
            # and every row will come out hollow.
            logger.warning(
                "%s: CSV headers matched no alias for %s -- headers were %s",
                self.name,
                ", ".join(missing),
                reader.fieldnames,
            )

        return [{canonical: row.get(header, "") for canonical, header in columns.items()} for row in reader]

    def _to_signal(self, rec: dict[str, Any]) -> Signal | None:
        """
        Render one transaction as a Signal, or None when it cannot be dated.

        A record with no readable timestamp is dropped rather than dated to now:
        the velocity gate would otherwise treat an undated filing as fresh.
        """
        filed_at = parse_timestamp(rec.get("filed_at")) or parse_timestamp(rec.get("transaction_date"))
        if filed_at is None:
            return None

        amount = parse_money(rec.get("amount"))
        aggregate = parse_money(rec.get("aggregate")) or amount
        transaction_date = rec.get("transaction_date") or filed_at.date().isoformat()

        text = (
            f"ORESTAR transaction {rec.get('tran_id') or 'not stated'} filed by committee "
            f"{rec.get('committee') or 'not stated'} ({rec.get('committee_id') or 'not stated'}) "
            f"records a contribution of ${amount:,.2f} of type "
            f"{rec.get('contribution_type') or 'not stated'} received from "
            f"{rec.get('contributor') or 'not stated'} of "
            f"{rec.get('contributor_city') or 'not stated'}, "
            f"{rec.get('contributor_state') or 'not stated'}, employer or affiliation "
            f"{rec.get('contributor_employer') or 'not stated'}. The transaction "
            f"date is {transaction_date} and the filing was received by the "
            f"Secretary of State on {filed_at.isoformat()}. Aggregate contributions from "
            f"this contributor to this committee for the cycle total "
            f"${aggregate:,.2f}. Purpose recorded as "
            f"{rec.get('purpose') or 'not stated'}."
        )

        return Signal(
            source=self.name,
            source_type=self.source_type,
            text=text,
            url=rec.get("url") or None,
            author=rec.get("committee") or None,
            published_at=filed_at,
            credibility=self.credibility,
        )
