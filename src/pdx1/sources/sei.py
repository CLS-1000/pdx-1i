"""
SEI -- Statements of Economic Interest, filed with the Oregon Government Ethics
Commission.

An SEI declares a public official's economic interests. Declaring an interest is what
the form is for: a disclosure is a completed legal obligation, not a finding. Adapter
text says what was declared and when, and stops there.

Officials are carried as the seat they hold, not as named individuals -- consistent with
the rest of the module.

**Live source: OGEC's Electronic Filing System (EFS) public records.** There is no
bulk export, but the public-records page runs on three JSON/HTML endpoints that need
no login and no session:

1. ``Records/GetJurisdictionLookupData`` -- every jurisdiction and office, with ids.
2. ``Records/GetGridData`` -- SEI filers for a jurisdiction and year (JSON).
3. ``Records/GetUserProfile`` -- one filer's offices held and every SEI report they
   have filed (year, date, status, report id).
4. ``SEIReport/ViewReport/{id}`` -- the report itself. The page embeds the whole
   report as a JSON ``model`` object; ``SEIReport`` holds the declared interests.

The live fetch walks those for the configured jurisdictions and emits records in the
same canonical shape as the fixture, so `parse` and the cache are unchanged.

**Privacy.** The ViewReport ``model`` also carries the filer's account record
(``SEIUser``: home address, personal email, phone). The adapter never reads it, never
caches the page, and drops street addresses and household member names from declared
interests -- only entity, description and city/state are kept. Officials are carried
as the seat they hold, as before.

An override ``feed_url`` that is not the EFS base is fetched with one GET and must be
a JSON or JSONL export in the fixture shape.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

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
# The live fetch emits these canonical names directly (see `_filer_records`); the
# aliases are for downloaded or hand-made exports. Matching is case- and
# punctuation-insensitive. A name matching nothing leaves its field empty.
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


EFS_BASE = "https://apps.oregon.gov/OGEC/EFS/"

# Portland-metro bodies, spelled as OGEC's jurisdiction lookup spells them. Matched
# case-insensitively against `JurisdictionName`; an unknown name is logged and skipped.
DEFAULT_JURISDICTIONS: tuple[str, ...] = (
    "PORTLAND",
    "MULTNOMAH CO",
    "WASHINGTON CO",
    "CLACKAMAS CO",
    "METRO",
    "PORT OF PORTLAND",
    "TRI-MET BOARD",
    "PORTLAND SD 1J",
)

# One row per report in the profile page's "SEI Reports" table.
_REPORT_ROW = re.compile(
    r"ViewReport/(\d+)['\"]>\s*(\d{4})\s*</a>\s*</td>\s*<td>\s*([^<]*?)\s*</td>"
    r"\s*<td>\s*([^<]*?)\s*</td>",
    re.IGNORECASE,
)
_OFFICES_TABLE = re.compile(r'(?s)id="officesTable".*?<tbody>(.*?)</tbody>')
_TABLE_ROW = re.compile(r"(?s)<tr>(.*?)</tr>")
_TABLE_CELL = re.compile(r"(?s)<td>\s*(.*?)\s*</td>")
_MODEL = re.compile(r"var model = (\{.*?\});\s*\n", re.DOTALL)

# Declared-interest sections of the SEI form: (model key, kind label, entity fields,
# description fields). Street addresses, ZIPs and `HeldByWhom` (household member
# names) are deliberately absent.
_SECTIONS: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("BusinessOfficeOrDirectorship", "Business office or directorship",
     ("BusinessName",), ("TitleOfOffice", "DescriptionOfBusiness")),
    ("BusinessOfficeOrDirectorshipHousehold", "Household member business office or directorship",
     ("BusinessName",), ("TitleOfOffice", "DescriptionOfBusiness")),
    ("SourcesOfIncome", "Source of income", ("NameOfSource",), ("DescriptionOfSource",)),
    ("IncomeOfThousandOrMore", "Income of $1,000 or more", ("IncomeSource",), ("Description",)),
    ("BusinessInvestmentOfThousandOrMore", "Business investment of $1,000 or more",
     ("BusinessName",), ("DescriptionOfBusiness",)),
    ("RealProperty", "Real property", (), ("Description",)),
    ("DebtOfThousandOrMore", "Debt of $1,000 or more",
     ("NameOfCreditor", "Creditor"), ("DateOfLoan", "InterestRate", "InterestRateOfLoan")),
    ("Honoraria", "Honorarium", ("OrganizationName",), ("NatureOfEvent", "Date", "ViewAmount", "Amount")),
    ("OfficeRelatedEventsSectionA", "Office-related event (A)",
     ("OrganizationName",), ("NatureOfEvent", "Date", "ViewAmount", "Amount")),
    ("OfficeRelatedEventsSectionB", "Office-related event (B)",
     ("OrganizationName",), ("NatureOfEvent", "Date", "ViewAmount", "Amount")),
    ("SharedBusinessWithLobbyist", "Business shared with a lobbyist",
     ("NameOfBusiness",), ("TypeOfBusiness", "NameOfLobbyist")),
    ("ServiceFeeOfThousandOrMore", "Service fee of $1,000 or more", ("Name",), ()),
)


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value)).strip() if value not in (None, "") else ""


def _interests(report: dict[str, Any]) -> list[dict[str, str]]:
    """Flatten the report's sections into kind/description/entity entries."""
    out: list[dict[str, str]] = []
    for key, kind, entity_keys, desc_keys in _SECTIONS:
        for item in report.get(key) or []:
            if not isinstance(item, dict):
                continue
            if key == "RealProperty":
                entity = ", ".join(v for v in (_clean(item.get("City")), _clean(item.get("State"))) if v)
            else:
                entity = next((_clean(item.get(k)) for k in entity_keys if _clean(item.get(k))), "")
            parts: list[str] = []
            for k in desc_keys:
                v = _clean(item.get(k))
                if k in ("Amount",) and _clean(item.get("ViewAmount")):
                    continue
                if k == "NameOfLobbyist" and v:
                    v = f"lobbyist {v}"
                if v and v not in parts:
                    parts.append(v)
            out.append({
                "kind": kind,
                "description": "; ".join(parts) or "not stated",
                "entity": entity or "not stated",
            })
    return out


class SeiAdapter(LiveSourceAdapter):
    """Parses SEI filings and amendments."""

    name = "SEI"
    source_type = SourceType.SEI
    credibility = 0.85
    # EFS public-records base. See the module docstring for the endpoints walked.
    feed_url = EFS_BASE

    def __init__(
        self,
        *args,
        jurisdictions: tuple[str, ...] | list[str] | None = None,
        year: int | None = None,
        request_delay_s: float = 0.2,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.jurisdictions = tuple(jurisdictions or DEFAULT_JURISDICTIONS)
        # SEIs are filed by April 15 for the prior calendar year; `year` is the
        # filing year, as EFS labels it.
        self._year = year or datetime.now(timezone.utc).year
        # A courtesy pause between the per-filer page loads.
        self.request_delay_s = max(0.0, float(request_delay_s))

    # ── Live fetch: EFS public records ──────────────────────────────────────

    def _is_efs(self) -> bool:
        return self.feed_url.rstrip("/").lower() == EFS_BASE.rstrip("/").lower()

    def _efs_get(self, path: str, **params: Any):
        url = EFS_BASE + path + (("?" + urlencode(params)) if params else "")
        response = self._get(url)
        response.raise_for_status()
        return response

    def _resolve_jurisdictions(self) -> dict[int, tuple[int, str]]:
        """JurisdictionID -> (CategoryID, name) for each configured metro body."""
        lookup = self._efs_get("Records/GetJurisdictionLookupData").json()
        wanted = {name.upper() for name in self.jurisdictions}
        found: dict[int, tuple[int, str]] = {}
        for row in lookup:
            name = _clean(row.get("JurisdictionName")).upper()
            if name in wanted:
                found[row["JurisdictionID"]] = (row["CategoryID"], name)
        for missing in sorted(wanted - {n for _, n in found.values()}):
            logger.warning("%s: jurisdiction %r not in OGEC lookup -- skipped", self.name, missing)
        return found

    def _list_filers(self, found: dict[int, tuple[int, str]]) -> dict[int, set[str]]:
        """FilerID -> jurisdiction names the filer was listed under."""
        filers: dict[int, set[str]] = {}
        for juris_id, (cat_id, name) in found.items():
            criteria = {
                "ResultType": "Filers", "FilerType": "SEI",
                "FromYear": str(self._year), "ToYear": str(self._year),
                "Quarter": "0", "TrustProfileStatusID": "0",
                "JurisdictionCategoryID": str(cat_id), "JurisdictionID": str(juris_id),
            }
            grid = self._efs_get(
                "Records/GetGridData",
                current=1, rowCount=-1, **{"sort[LastName]": "asc"},
                searchPhrase=json.dumps(criteria),
            ).json()
            for row in grid.get("rows") or []:
                filers.setdefault(int(row["FilerID"]), set()).add(name)
        return filers

    def _fetch_live(self) -> str:
        if not self._is_efs():
            return super()._fetch_live()

        filers = self._list_filers(self._resolve_jurisdictions())

        records: list[dict[str, Any]] = []
        failed = 0
        for filer_id, names in sorted(filers.items()):
            try:
                records.extend(self._filer_records(filer_id, names))
            except Exception as exc:  # one bad page must not cost the whole feed
                failed += 1
                logger.warning("%s: filer %s skipped: %s", self.name, filer_id, exc)
            if self.request_delay_s:
                time.sleep(self.request_delay_s)

        if filers and failed == len(filers):
            raise RuntimeError(f"{self.name}: every filer page failed ({failed})")
        logger.info(
            "%s: %d filer(s), %d report(s) for %d, %d failed",
            self.name, len(filers), len(records), self._year, failed,
        )
        return json.dumps(records, ensure_ascii=False)

    def _filer_records(self, filer_id: int, jurisdictions: set[str]) -> list[dict[str, Any]]:
        page = self._efs_get(
            "Records/GetUserProfile", filerID=filer_id, reportType="Filers", filerType="SEI"
        ).text

        offices: list[tuple[str, str]] = []
        table = _OFFICES_TABLE.search(page)
        for row in _TABLE_ROW.findall(table.group(1) if table else ""):
            cells = [_clean(re.sub(r"<[^>]+>", "", c)) for c in _TABLE_CELL.findall(row)]
            if len(cells) >= 2 and cells[0].upper() in jurisdictions:
                offices.append((cells[0], cells[1]))

        reports = sorted(
            (int(rid), int(yr), date, status) for rid, yr, date, status in _REPORT_ROW.findall(page)
        )
        prior = [r for r in reports if r[1] == self._year - 1]
        prior_id = str(prior[-1][0]) if prior else ""

        out: list[dict[str, Any]] = []
        for report_id, yr, date_filed, status in reports:
            if yr != self._year:
                continue
            report = self._report(report_id)
            if report is None:
                continue
            out.append({
                "filing_id": f"SEI-{report_id}",
                "seat": "; ".join(sorted({o for _, o in offices})) or "not stated",
                "jurisdiction": "; ".join(sorted({j for j, _ in offices} or jurisdictions)),
                # The form reports the calendar year before the filing year.
                "year": yr - 1,
                "filing_type": "amendment" if status.lower().startswith("amend") else "original",
                "filed_at": report.get("DateFiled") or date_filed,
                "prior_filing_id": f"SEI-{prior_id}" if prior_id else "",
                "status": status or "Filed",
                "interests": _interests(report),
                "url": f"{EFS_BASE}SEIReport/ViewReport/{report_id}",
            })
        return out

    def _report(self, report_id: int) -> dict[str, Any] | None:
        """The `SEIReport` part of the page model only -- `SEIUser` is never touched."""
        page = self._efs_get(f"SEIReport/ViewReport/{report_id}").text
        match = _MODEL.search(page)
        if not match:
            logger.warning("%s: report %s has no embedded model", self.name, report_id)
            return None
        report = json.loads(match.group(1)).get("SEIReport")
        return report if isinstance(report, dict) else None

    def parse(self, raw: str) -> list[Signal]:
        text = raw.lstrip()
        if text[:1] not in ("[", "{"):
            # HTML from the landing page, or anything else non-JSON. Say so plainly
            # rather than returning an empty list that reads as "nothing was filed".
            raise ValueError(
                f"{self.name}: payload is not a JSON or JSONL export. The default feed_url "
                f"walks OGEC's EFS records; an override must point at an export in the "
                f"fixture shape. See the module docstring."
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
