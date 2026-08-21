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
# ENDPOINT VERIFIED, FIELD NAMES NOT. A live run on 2026-08-06 got HTTP 200 from
# `feed_url` and a response carrying a nextLink, so the URL and the OData envelope are
# confirmed. No row was ever parsed on that run -- the walk aborted on page 2 (see
# `_fetch_live`) -- so these spellings still come from the two prior PDX-1i
# implementations rather than an observed response. They are the last unconfirmed
# piece of this adapter; check them against a real payload.
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("CatchLine", "RelatingTo", "MeasureTitle", "title"),
    "session": ("SessionKey", "Session", "session"),
    "status": ("CurrentLocation", "CurrentStatus", "MeasureStatus", "status"),
    "summary": ("MeasureSummary", "Summary", "summary"),
    "committee": ("CurrentCommitteeName", "CommitteeName", "committee"),
    "action": ("CurrentAction", "LastAction", "action"),
    "url": ("MeasureUrl", "WebSiteUrl", "url"),
}

# Timestamp fields, tried in order. A measure with none of these cannot be placed on
# the timeline and is dropped rather than dated to now.
_DATE_FIELDS = (
    "action_at",
    "ActionDate",
    "MeasureHistoryActionDate",
    "ModifiedDate",
    "IntroducedDate",
    "CreatedDate",
)

_OLIS_WEB_BASE = "https://olis.oregonlegislature.gov/liz"


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
        rows: list[dict[str, Any]] = []
        url: str | None = self.feed_url

        for page in range(MAX_PAGES):
            response = self._get(url)
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
            url = urljoin(str(response.url), str(next_link))
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
