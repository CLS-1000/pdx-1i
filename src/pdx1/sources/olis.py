"""
OLIS -- Oregon Legislative Information System.

Bills, hearings and markup timing. Markup timing matters to this module: the interval
between a committee action and a related public announcement is a measurable structural
fact, and the pipeline records the interval without characterising it.

Two payload shapes are accepted, and `parse` detects which it has:

- **JSON array** — the checked-in fixture shape, already in canonical field names.
- **OData envelope** — the live shape. `api.oregonlegislature.gov` serves an OData
  service whose responses wrap rows under `value` and page via `@odata.nextLink`.
  `_fetch_live` walks those pages and hands `parse` a single combined array.

Real measures do not use the fixture's field names, so `_FIELD_ALIASES` maps the
canonical names onto the OData spellings. See the note on that table before trusting
live output.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urljoin

from ..models import Signal, SourceType
from .base import LiveSourceAdapter
from .normalize import first_present, parse_timestamp

logger = logging.getLogger(__name__)

#: Stop walking pages here. A session's Measures collection is a few thousand rows;
#: this only exists so a malformed nextLink cannot loop forever.
MAX_PAGES = 50

# OData field spellings for each canonical field, in preference order.
#
# ENDPOINT AND FIELD NAMES VERIFIED against a live response on 2026-08-21: the
# Measures collection answered 200 with 31,405 rows over 7 pages, and these spellings
# were checked against the union of keys on a 5,000-row page. Four map exactly
# (CatchLine, SessionKey, CurrentLocation, MeasureSummary; each non-null on 5,000/5,000
# rows). Two do not, and no name was guessed to cover them:
#
#   committee  the collection carries CurrentCommitteeCode (a code, non-null on
#              2,983/5,000) and no committee *name* field at all.
#   action     the collection carries no action field. Measure actions are a separate
#              OData collection; `_to_signal` falls back to the measure's status,
#              which is what the record then says.
#   url        no URL field either -- `_measure_url` builds the OLIS web link instead.
#
# The unmatched aliases are left in place: they cost nothing, and a fixture or a future
# schema may still carry them.
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("CatchLine", "RelatingTo", "MeasureTitle", "title"),
    "session": ("SessionKey", "Session", "session"),
    "status": ("CurrentLocation", "CurrentStatus", "MeasureStatus", "status"),
    "summary": ("MeasureSummary", "Summary", "summary"),
    # CurrentCommitteeCode is the only committee field the live collection has -- a
    # code rather than a name. Preferred over the unobserved name spellings so the
    # record says which committee holds the measure instead of "not stated".
    "committee": ("CurrentCommitteeCode", "CurrentCommitteeName", "CommitteeName", "committee"),
    "action": ("CurrentAction", "LastAction", "action"),
    "url": ("MeasureUrl", "WebSiteUrl", "url"),
}

# Timestamp fields, tried in order. A measure with none of these cannot be placed on
# the timeline and is dropped rather than dated to now.
#
# Live measured 2026-08-21: the collection carries ModifiedDate and CreatedDate (both
# non-null on 5,000/5,000 rows) and none of the three action-date spellings above them,
# so a live measure is dated by ModifiedDate.
_DATE_FIELDS = (
    "action_at",
    "ActionDate",
    "MeasureHistoryActionDate",
    "ModifiedDate",
    "IntroducedDate",
    "CreatedDate",
)

_OLIS_WEB_BASE = "https://olis.oregonlegislature.gov/liz"


def _with_json_format(url: str) -> str:
    """
    Ensure a page URL still asks for JSON.

    OLIS serves its nextLink without the `$format=json` the first request carried, and
    OData content negotiation then falls back to the service default -- Atom XML. Page
    two answers 200 with 26 MB of XML, `response.json()` raises on it, and the walk is
    discarded along with every page that did parse. Measured against the live service
    on 2026-08-21: with the parameter restored the same URL returns a JSON envelope of
    5,000 rows.

    Appended textually rather than through urlencode because the skiptoken carries
    literal commas and quotes (`$skiptoken=312,'SB','2019R1'`) that a re-encode would
    rewrite.
    """
    if "$format=" in url:
        return url
    return f"{url}{'&' if '?' in url else '?'}$format=json"


class OlisAdapter(LiveSourceAdapter):
    """Parses OLIS bill and committee-action records."""

    name = "OLIS"
    source_type = SourceType.OLIS
    credibility = 0.9
    # Oregon Legislative Information System OData service. `$format=json` is required;
    # without it the service answers in Atom XML.
    # VERIFIED REACHABLE: HTTP 200 on a live run, 2026-08-06.
    feed_url = "https://api.oregonlegislature.gov/odata/odataservice.svc/Measures?$format=json"

    # ── Live fetch ───────────────────────────────────────────────────────────

    def _fetch_live(self) -> str:
        """
        Walk the OData pages and return one combined JSON array.

        Overrides the single-GET base implementation because OData paginates. The
        base class still owns caching and the fixture path, so a partial walk that
        raises falls back to the last-good cache exactly as a single failed GET would.
        """
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                f"{self.name}: httpx is required for live fetch -- "
                "install it with: pip install 'pdx-1i[live]'"
            ) from exc

        rows: list[dict[str, Any]] = []
        url: str | None = self.feed_url

        for page in range(MAX_PAGES):
            response = httpx.get(url, timeout=self.timeout, follow_redirects=True)
            response.raise_for_status()
            payload = response.json()

            if not isinstance(payload, dict):
                # Not an envelope -- a plain array endpoint. Take it and stop.
                rows.extend(payload)
                break

            rows.extend(payload.get("value") or [])
            next_link = payload.get("@odata.nextLink") or payload.get("odata.nextLink")
            if not next_link:
                break
            # OData permits a relative nextLink, and OLIS serves one. Handing it to
            # httpx unresolved raises UnsupportedProtocol -- which is what happened on
            # the first real run against the live service: page 1 returned 200 and the
            # walk then died on page 2.
            url = _with_json_format(urljoin(str(response.url), str(next_link)))
            if page == MAX_PAGES - 1:
                logger.warning(
                    "%s: stopped at the %d-page ceiling with a nextLink still set",
                    self.name,
                    MAX_PAGES,
                )

        logger.info("%s: fetched %d measure(s)", self.name, len(rows))
        return json.dumps(rows)

    # ── Parsing ──────────────────────────────────────────────────────────────

    def parse(self, raw: str) -> list[Signal]:
        """Turn a fixture array or an OData payload into signals."""
        records = self._to_records(raw)
        signals: list[Signal] = []
        undated = 0

        for rec in records:
            signal = self._to_signal(rec)
            if signal is None:
                undated += 1
                continue
            signals.append(signal)

        if undated:
            logger.info("%s: skipped %d measure(s) with no readable date", self.name, undated)
        if records and not signals:
            # Every row dropped means the date mapping missed, not that the session
            # was quiet. Loud enough to notice in a cycle log.
            logger.warning(
                "%s: %d record(s) fetched but none carried a readable date -- "
                "check _DATE_FIELDS against the live schema",
                self.name,
                len(records),
            )
        return signals

    def _to_records(self, raw: str) -> list[dict[str, Any]]:
        loaded = json.loads(raw)
        if isinstance(loaded, dict):
            return loaded.get("value") or []
        return loaded

    def _to_signal(self, rec: dict[str, Any]) -> Signal | None:
        """Render one measure as a Signal, or None when it cannot be dated."""
        action_at = None
        for field in _DATE_FIELDS:
            action_at = parse_timestamp(rec.get(field))
            if action_at is not None:
                break
        if action_at is None:
            return None

        bill_id = self._bill_id(rec)
        session = self._field(rec, "session") or "not stated"
        title = self._field(rec, "title") or "not stated"
        status = self._field(rec, "status") or "not stated"
        summary = self._field(rec, "summary") or "not stated"
        committee = self._field(rec, "committee") or "not stated"
        action = self._field(rec, "action") or status

        sponsors = ", ".join(rec.get("sponsors") or []) or "not stated"
        subjects = ", ".join(rec.get("subjects") or []) or "not stated"
        jurisdictions = ", ".join(rec.get("jurisdictions") or []) or "not stated"

        text = (
            f"OLIS record for {bill_id}, {title}, in the {session} session. The measure "
            f"is sponsored by {sponsors} and referred to the {committee} committee. The "
            f"most recent recorded action is {action} on {action_at.isoformat()}, moving "
            f"the measure to status {status}. Summary as published: {summary} "
            f"Subject areas recorded are {subjects}. "
            f"The measure affects jurisdictions {jurisdictions}."
        )

        return Signal(
            source=self.name,
            source_type=self.source_type,
            text=text,
            url=self._field(rec, "url") or self._measure_url(rec),
            author=committee if committee != "not stated" else None,
            published_at=action_at,
            credibility=self.credibility,
        )

    # ── Field access ─────────────────────────────────────────────────────────

    def _field(self, rec: dict[str, Any], canonical: str) -> Any:
        """Read a canonical field, trying each alias in preference order."""
        return first_present(rec, *_FIELD_ALIASES[canonical])

    def _bill_id(self, rec: dict[str, Any]) -> str:
        """
        The measure's public identifier.

        Fixtures carry `bill_id` directly; OData splits it into a prefix and a number
        (`SB` + `1147`), which is how the measure is cited everywhere else.
        """
        direct = first_present(rec, "bill_id", "MeasureNo", "measure_no")
        if direct:
            return str(direct)
        prefix = rec.get("MeasurePrefix") or ""
        number = rec.get("MeasureNumber") or ""
        combined = f"{prefix}{number}".strip()
        return combined or "not stated"

    def _measure_url(self, rec: dict[str, Any]) -> str | None:
        """Construct the OLIS overview link when the payload does not carry one."""
        session = self._field(rec, "session")
        prefix = rec.get("MeasurePrefix")
        number = rec.get("MeasureNumber")
        if not (session and prefix and number):
            return None
        return f"{_OLIS_WEB_BASE}/{session}/Measures/Overview/{prefix}{number}"
