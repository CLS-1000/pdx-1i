"""
ORESTAR -- Oregon campaign-finance contributions.

Each transaction becomes one Signal whose text is a flat description of the filing:
who filed, what was received, from whom, and when. Descriptive only -- the adapter
states what the filing says and attributes nothing.

Two payload shapes are accepted, and `parse` detects which it has:

- **JSON array** — the checked-in fixture shape, already in canonical field names.
- **CSV** — the live shape. There is no bulk file (the old
  `{year}_report_transactions.zip` path 404s). The live fetch runs ORESTAR's public
  transaction search for a rolling date window, then pulls `XcelCNESearch` -- the
  "Export To Excel" link -- in the same session. That returns an .xlsx, which is
  flattened to CSV text so the cache and `parse` see one format.

ORESTAR caps a search at 5,000 rows, so a window that hits the cap is halved and
fetched in pieces. A single day still over the cap is logged as truncated.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import zipfile
from datetime import date, datetime, timedelta, timezone
from xml.etree import ElementTree as ET  # nosec B405 -- parses the SOS's own export
from typing import Any

from ..models import Signal, SourceType
from .base import LiveSourceAdapter
from .normalize import build_column_map, parse_money, parse_timestamp

logger = logging.getLogger(__name__)

_ZIP_MAGIC = b"PK\x03\x04"

ORESTAR_BASE = "https://secure.sos.state.or.us/orestar/"
# The "Export To Excel Format" link on a results page. It exports whatever search the
# session last ran, so it only works with the search response's cookies.
EXPORT_URL = ORESTAR_BASE + "XcelCNESearch"
# Contributions (TranType=C) filed in [start, end]. Dates are MM/DD/YYYY.
SEARCH_URL = (
    ORESTAR_BASE + "cneSearch.do?cneSearchButtonName=search&cneSearchTranType=C"
    "&cneSearchTranStartDate={start}&cneSearchTranEndDate={end}"
)
ROW_CAP = 5000
_RECORDS_FOUND = re.compile(r"([\d,]+)\s+records\s+found", re.IGNORECASE)
_XLSX_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# Header spellings accepted for each canonical field, in preference order.
#
# VERIFIED 2026-09-22 against a live XcelCNESearch export. Its headers are:
# Tran Id, Original Id, Tran Date, Tran Status, Filer, Contributor/Payee, Sub Type,
# Amount, Aggregate Amount, Filer Id, Filed Date, Book Type, Occptn Txt, Emp Name,
# Emp City, Emp State, Addr Line1, City, State, Zip, Purp Desc, ... (45 in all).
# The first alias in each tuple is the live spelling; the rest are kept so older
# exports and hand-made files still map. Matching is case- and punctuation-insensitive
# (see `normalize.header_key`).
_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "tran_id": ("tran id", "tranid", "transaction id", "id"),
    "committee": ("filer", "filer name", "committee", "committee name"),
    "committee_id": ("filer id", "filerid", "committee id"),
    "contributor": ("contributor payee", "contributor", "contributor name", "payee"),
    "contributor_city": ("city", "contributor city"),
    "contributor_state": ("state", "contributor state"),
    "contributor_employer": ("emp name", "employer", "contributor employer", "occupation"),
    "contribution_type": ("sub type", "subtype", "contribution type", "book type"),
    "amount": ("amount", "transaction amount"),
    "aggregate": ("aggregate amount", "aggregate"),
    "transaction_date": ("tran date", "trandate", "transaction date", "date"),
    "filed_at": ("filed date", "filed at", "filed", "received date"),
    "purpose": ("purp desc", "purpose", "purpose of expenditure", "description"),
    "url": ("url", "link"),
}


class OrestarAdapter(LiveSourceAdapter):
    """Parses ORESTAR contribution filings."""

    name = "ORESTAR"
    source_type = SourceType.ORESTAR
    # A filed contribution report is a primary public record.
    credibility = 0.9
    # ORESTAR public transaction search; `{start}`/`{end}` are filled per fetch from a
    # rolling window. An override may be any URL: a `cneSearch.do` URL uses the
    # search-then-export flow, anything else (a CSV, JSON or ZIP file) is one GET.
    # `{year}` is still accepted in an override for backward compatibility.
    feed_url = SEARCH_URL

    def __init__(
        self, *args, year: int | None = None, lookback_days: int = 7, **kwargs
    ) -> None:
        super().__init__(*args, **kwargs)
        self._year = year or datetime.now(timezone.utc).year
        # Late and amended filings land days after the transaction date, so the window
        # overlaps day to day; the novelty gate drops what has already been seen.
        self.lookback_days = max(1, int(lookback_days))
        # Bind into whichever URL is in effect -- the class default or an override the
        # base class already applied. Not str.format: `{start}`/`{end}` stay unbound.
        if "{year}" in self.feed_url:
            self.feed_url = self.feed_url.replace("{year}", str(self._year))

    # ── Live fetch: search, then export ──────────────────────────────────────

    def _is_search(self) -> bool:
        return "cneSearch.do" in self.feed_url

    def _fetch_live(self) -> str:
        if not self._is_search():
            return super()._fetch_live()

        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=self.lookback_days - 1)
        sheets = self._export_window(start, end)

        header: list[str] | None = None
        body: list[list[str]] = []
        for rows in sheets:
            if not rows:
                continue
            if header is None:
                header = rows[0]
            body.extend(rows[1:])

        out = io.StringIO()
        if header is not None:
            writer = csv.writer(out)
            writer.writerow(header)
            writer.writerows(body)
        logger.info("%s: %d transaction(s) for %s..%s", self.name, len(body), start, end)
        return out.getvalue()

    def _window_url(self, start: date, end: date) -> str:
        return self.feed_url.replace("{start}", start.strftime("%m/%d/%Y")).replace(
            "{end}", end.strftime("%m/%d/%Y")
        )

    def _export_window(self, start: date, end: date) -> list[list[list[str]]]:
        """Search one window and export it, splitting it while it exceeds the row cap."""
        search = self._get(self._window_url(start, end))
        search.raise_for_status()

        match = _RECORDS_FOUND.search(search.text or "")
        found = int(match.group(1).replace(",", "")) if match else None
        if found == 0:
            return []
        if found is not None and found >= ROW_CAP:
            templated = "{start}" in self.feed_url and "{end}" in self.feed_url
            if templated and start < end:
                mid = start + timedelta(days=(end - start).days // 2)
                return self._export_window(start, mid) + self._export_window(
                    mid + timedelta(days=1), end
                )
            logger.warning(
                "%s: %s..%s has %d records; ORESTAR exports only the first %d",
                self.name, start, end, found, ROW_CAP,
            )

        export = self._get(EXPORT_URL, cookies=search.cookies)
        export.raise_for_status()
        content = export.content or b""
        if not content.startswith(_ZIP_MAGIC):
            raise ValueError(
                f"{self.name}: export returned {export.headers.get('content-type')!r}, "
                "not a workbook -- the search session was probably not carried over"
            )
        return [_xlsx_rows(content)]

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
            if "xl/workbook.xml" in archive.namelist():
                out = io.StringIO()
                csv.writer(out).writerows(_xlsx_rows(content))
                return out.getvalue()
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


# ── .xlsx without a dependency ───────────────────────────────────────────────


def _col_index(ref: str | None) -> int | None:
    """`"AB12"` -> 27. None when the cell carries no reference."""
    if not ref:
        return None
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + (ord(ch.upper()) - 64)
    return n - 1 if n else None


def _xlsx_rows(content: bytes) -> list[list[str]]:
    """
    First worksheet of an .xlsx as rows of strings.

    Stdlib only, so a live install does not need openpyxl for one adapter. Handles
    shared strings, inline strings and numbers -- everything the ORESTAR export uses.
    """
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        names = z.namelist()
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))  # nosec B314
            for si in root.iter(_XLSX_NS + "si"):
                shared.append("".join(t.text or "" for t in si.iter(_XLSX_NS + "t")))
        sheets = sorted(n for n in names if n.startswith("xl/worksheets/") and n.endswith(".xml"))
        if not sheets:
            raise ValueError(f"ORESTAR: workbook has no worksheet (members: {names})")
        sheet = ET.fromstring(z.read(sheets[0]))  # nosec B314

    rows: list[list[str]] = []
    for row in sheet.iter(_XLSX_NS + "row"):
        cells: dict[int, str] = {}
        pos = 0
        for c in row.iter(_XLSX_NS + "c"):
            idx = _col_index(c.get("r"))
            pos = pos if idx is None else idx
            kind = c.get("t")
            v = c.find(_XLSX_NS + "v")
            if kind == "s":
                val = shared[int(v.text)] if v is not None and v.text else ""
            elif kind == "inlineStr":
                val = "".join(t.text or "" for t in c.iter(_XLSX_NS + "t"))
            else:
                val = v.text if v is not None and v.text else ""
            # Integral numbers come back as "20.0" from some writers; keep them clean.
            if kind in (None, "n") and val.endswith(".0"):
                val = val[:-2]
            cells[pos] = val
            pos += 1
        width = max(cells) + 1 if cells else 0
        rows.append([cells.get(i, "") for i in range(width)])
    return rows
