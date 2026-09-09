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
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from ..models import Signal, SourceType
from . import olis_actions
from .base import LiveSourceAdapter
from .normalize import first_present, parse_timestamp

logger = logging.getLogger(__name__)

#: Stop walking pages here. OData serves 5,000 rows per page and a long session's
#: MeasureHistoryActions runs to six pages (2025R1: 27,488 rows), so the ceiling has to
#: clear that with room to spare. It only exists so a malformed nextLink cannot loop
#: forever.
MAX_PAGES = 50

#: Interim session keys (2025I1 and friends). They carry no measure actions, so
#: harvesting one produces a live adapter that reports healthy and returns nothing.
_INTERIM_KEY = re.compile(r"^\d{4}I\d+$")

#: ActionDate is a naive local timestamp, not UTC. Reading it as UTC would shift every
#: transition seven or eight hours and quietly change which side of the velocity gate
#: an evening action falls on.
_OLIS_TZ = ZoneInfo("America/Los_Angeles")

#: Trailing UTC offset on an ISO timestamp, e.g. "+00:00" or "-07:00".
_HAS_OFFSET = re.compile(r"[+-]\d{2}:?\d{2}$")

# OData field spellings for each canonical field, in preference order.
#
# ENDPOINT AND FIELD NAMES VERIFIED, 2026-09-07, against a live 2026R1 Measures pull
# (304 rows). `union_keys` over that payload is exactly:
#
#   AtTheRequestOf, CatchLine, ChapterNumber, CreatedDate, CurrentCommitteeCode,
#   CurrentLocation, CurrentSubCommittee, CurrentVersion, EffectiveDate,
#   EmergencyClause, FiscalAnalyst, FiscalImpact, LCNumber, MeasureNumber,
#   MeasurePrefix, MeasureSummary, MinorityCatchLine, ModifiedDate, PrefixMeaning,
#   RelatingTo, RelatingToFull, RevenueEconomist, RevenueImpact, SessionKey, Vetoed
#
# Three corrections fell out of that check, and each changes what the adapter emits:
#
#   - `CurrentCommitteeName` does not exist. The committee field had been resolving to
#     "not stated" on every live row. The real spellings are `CurrentCommitteeCode`
#     and `CurrentSubCommittee`.
#   - `CurrentStatus` / `MeasureStatus` do not exist; `CurrentLocation` is the only
#     status spelling served. It is also the field this module exists to stop relying
#     on -- see the class docstring.
#   - `CurrentAction` / `LastAction` do not exist, so `action` falls through to status
#     exactly as the resolution order intends.
#
# Unverified spellings are kept as trailing fallbacks rather than deleted: they cost
# one dict lookup and they keep the fixture shape working.
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("CatchLine", "RelatingTo", "MeasureTitle", "title"),
    "session": ("SessionKey", "Session", "session"),
    "status": ("CurrentLocation", "CurrentStatus", "MeasureStatus", "status"),
    "summary": ("MeasureSummary", "Summary", "summary"),
    "committee": (
        "CurrentCommitteeCode",
        "CurrentSubCommittee",
        "CurrentCommitteeName",
        "CommitteeName",
        "committee",
    ),
    "action": ("CurrentAction", "LastAction", "action"),
    "url": ("MeasureUrl", "WebSiteUrl", "url"),
}

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

    # ── Constructor ──────────────────────────────────────────────────────────

    def __init__(
        self,
        *args: Any,
        sessions: list[str] | None = None,
        session_lookback_days: int = 540,
        store: Any = None,
        bootstrap: bool = False,
        **kwargs: Any,
    ) -> None:
        """
        `store` is any object exposing `olis_emitted` / `record_olis_emitted` -- in
        practice the pipeline's DualWriteStore. Without one the adapter still parses
        Measures; it just cannot emit transitions, because it has nowhere to record
        what it already emitted and would re-emit the whole session every cycle.
        """
        super().__init__(*args, **kwargs)
        self.sessions = sessions
        self.session_lookback_days = session_lookback_days
        self.store = store
        #: Replay and record without emitting. The first run against a new session must
        #: be a bootstrap run -- see `_harvest_transitions`.
        self.bootstrap = bootstrap
        #: Populated by the last transition harvest, for the CLI and for tests.
        self.last_halts: list[tuple[str, str]] = []
        self._resolved_sessions: list[str] = list(sessions or [])

    # ── OData plumbing ───────────────────────────────────────────────────────

    @staticmethod
    def _with_json(url: str) -> str:
        """
        Force `$format=json` onto a URL.

        OLIS's nextLink carries `$filter` and `$skiptoken` but drops `$format`, so
        page 2 onwards comes back as Atom XML and `response.json()` raises. A single
        page of Measures hides this -- a session fits in one page -- but
        MeasureHistoryActions runs to six, so the walk died on page 2 every time.
        Re-adding the parameter to every URL is what makes paging work at all.
        """
        parts = urlsplit(url)
        query = parse_qsl(parts.query, keep_blank_values=True)
        if not any(key == "$format" for key, _ in query):
            query.append(("$format", "json"))
        return urlunsplit(parts._replace(query=urlencode(query)))

    def _service_root(self) -> str:
        """The OData service root, derived from `feed_url` so an override moves both."""
        parts = urlsplit(self.feed_url)
        path = parts.path.rsplit("/", 1)[0]
        return urlunsplit((parts.scheme, parts.netloc, path + "/", "", ""))

    def _httpx(self):
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                f"{self.name}: httpx is required for live fetch -- "
                "install it with: pip install 'pdx-1i[live]'"
            ) from exc
        return httpx

    def _walk(self, url: str, what: str) -> list[dict[str, Any]]:
        """Walk an OData collection's pages and return every row."""
        httpx = self._httpx()
        rows: list[dict[str, Any]] = []
        next_url: str | None = self._with_json(url)

        for page in range(MAX_PAGES):
            response = httpx.get(next_url, timeout=self.timeout, follow_redirects=True)
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
            # httpx unresolved raises UnsupportedProtocol.
            next_url = self._with_json(urljoin(str(response.url), str(next_link)))
            if page == MAX_PAGES - 1:
                logger.warning(
                    "%s: %s stopped at the %d-page ceiling with a nextLink still set",
                    self.name,
                    what,
                    MAX_PAGES,
                )
        return rows

    # ── Session resolution ───────────────────────────────────────────────────

    def resolve_sessions(self, now: datetime | None = None) -> list[str]:
        """
        The session keys this cycle should harvest.

        `DefaultSession` is deliberately never consulted. It currently points at
        `2025I1` -- the interim -- which carries no measure actions, so trusting it
        yields an adapter that reports healthy and harvests nothing. Session keys come
        in three flavours (`R` regular, `S` special, `I` interim), not two.
        """
        if self.sessions:
            return list(self.sessions)
        rows = self._walk(urljoin(self._service_root(), "LegislativeSessions"), "sessions")
        return _select_sessions(rows, self.session_lookback_days, now)

    # ── Live fetch ───────────────────────────────────────────────────────────

    def _measures_url(self, sessions: list[str]) -> str:
        """`Measures` constrained to the resolved sessions."""
        base = self.feed_url
        if not sessions:
            return base
        clause = " or ".join(f"SessionKey eq '{s}'" for s in sessions)
        parts = urlsplit(base)
        query = [kv for kv in parse_qsl(parts.query, keep_blank_values=True) if kv[0] != "$filter"]
        query.append(("$filter", clause))
        return urlunsplit(parts._replace(query=urlencode(query)))

    def _fetch_live(self) -> str:
        """
        Walk the Measures pages and return one combined JSON array.

        Overrides the single-GET base implementation because OData paginates. The
        base class still owns caching and the fixture path, so a partial walk that
        raises falls back to the last-good cache exactly as a single failed GET would.
        """
        sessions = self.resolve_sessions()
        logger.info("%s: resolved sessions %s", self.name, ", ".join(sessions) or "(none)")
        self._resolved_sessions = sessions
        rows = self._walk(self._measures_url(sessions), "measures")
        logger.info("%s: fetched %d measure(s)", self.name, len(rows))
        return json.dumps(rows)

    def _fetch_actions(self, session: str) -> list[dict[str, Any]]:
        """
        Every action row for one session.

        A full-session pull, every cycle. There is deliberately no `ActionDate`
        watermark: it would work and it would be wrong. OLIS inserts and edits rows
        retroactively -- 2025R1 has a row created 399.9 days after its own ActionDate
        -- so a date-bounded pull silently skips backdated inserts, which are exactly
        the rows a from-scratch replay exists to catch. The only safe incremental
        cursor is `ModifiedDate OR CreatedDate` with a lookback wide enough to cover
        that lag, and at this size (six pages for a long session) that saves nothing
        over the full pull. Leave it full.
        """
        url = urljoin(self._service_root(), "MeasureHistoryActions")
        url = f"{url}?{urlencode({'$filter': f"SessionKey eq '{session}'"})}"
        rows = self._walk(url, f"actions/{session}")
        logger.info("%s: fetched %d action row(s) for %s", self.name, len(rows), session)
        return rows

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

    # ── Procedural transitions ───────────────────────────────────────────────

    def fetch(self):
        """
        Measures plus procedural transitions.

        The Measures pass is unchanged and still runs first: it is what supplies a
        transition its title and sponsors, and it is the path fixture mode uses.
        """
        from .base import FetchResult

        raw = self._read_raw()
        signals = self.parse(raw)
        try:
            signals.extend(self._harvest_transitions(self._to_records(raw)))
        except Exception as exc:  # noqa: BLE001 - see below
            # The transition pass is additive, so its failure must not cost the
            # Measures signals that were already parsed -- including the ones the
            # last-good cache just served during an outage. Letting this propagate
            # would turn a partial degradation into a dead feed, which is the one
            # thing an adapter is not allowed to do.
            logger.warning("%s: transition harvest failed (%s)", self.name, exc)
        return FetchResult(source=self.name, signals=signals)

    def _harvest_transitions(self, measure_records: list[dict[str, Any]]) -> list[Signal]:
        """
        Replay every session's action history and emit the transitions not yet seen.

        One Signal per *procedural transition*, not one per measure and not one per
        measure whose final state changed. A measure can cross several emittable
        states in a single cycle, and a final-state comparison collapses those to one.
        """
        if not (self._live and self.store is not None):
            return []

        index = self._measure_index(measure_records)
        signals: list[Signal] = []
        self.last_halts = []

        for session in self._resolved_sessions or self.resolve_sessions():
            rows = self._fetch_actions(session)
            computed = self._replay_session(session, rows)
            already = self.store.olis_emitted(session)
            fresh = computed.keys() - already

            if self.bootstrap:
                recorded = self.store.record_olis_emitted(
                    fresh, olis_actions.RULES_VERSION
                )
                logger.info(
                    "%s: bootstrap recorded %d transition(s) for %s, emitted none",
                    self.name,
                    recorded,
                    session,
                )
                continue

            for key in sorted(fresh):
                signal = self._transition_signal(key, computed[key], index)
                if signal is not None:
                    signals.append(signal)
            self.store.record_olis_emitted(fresh, olis_actions.RULES_VERSION)
            logger.info(
                "%s: %s -- %d transition(s) computed, %d new, %d halted measure(s)",
                self.name,
                session,
                len(computed),
                len(fresh),
                len(self.last_halts),
            )

        if self.last_halts:
            logger.warning(
                "%s: %d measure(s) halted during replay: %s",
                self.name,
                len(self.last_halts),
                ", ".join(f"{mid} ({why})" for mid, why in self.last_halts[:20]),
            )
        return signals

    def _replay_session(
        self, session: str, rows: list[dict[str, Any]]
    ) -> dict[tuple[str, str, int, str, str], olis_actions.Transition]:
        """
        Group, sort and replay one session, keyed by the emitted-transition tuple.

        A halted measure is tallied and skipped. One anomalous measure must not take
        down the adapter -- the corpus already contains at least one (SB579 in 2023R1,
        whose "Rescission of the subsequent referral denied" no rule claims).
        """
        groups: dict[tuple[str, int], list[dict[str, Any]]] = {}
        for row in rows:
            try:
                key = (str(row["MeasurePrefix"]), int(row["MeasureNumber"]))
            except (KeyError, TypeError, ValueError):
                continue
            groups.setdefault(key, []).append(row)

        computed: dict[tuple[str, str, int, str, str], olis_actions.Transition] = {}
        for (prefix, number), measure_rows in groups.items():
            # MeasureHistoryId does not sort in ActionDate order -- 2025R1 has id
            # 654676 at 08:33 ahead of 654677 at 08:32 -- so date leads and the id
            # only breaks ties.
            measure_rows.sort(key=lambda r: (str(r.get("ActionDate") or ""), r.get("MeasureHistoryId") or 0))
            try:
                _item, found = olis_actions.transitions(measure_rows)
            except olis_actions.Halt as exc:
                self.last_halts.append((f"{prefix}{number}", str(exc)))
                continue
            for transition in found:
                if transition.to_state not in olis_actions.EMITTABLE:
                    continue
                computed[
                    (session, prefix, number, transition.action_id, transition.chamber, transition.to_state)
                ] = transition
        return computed

    def _transition_signal(
        self,
        key: tuple[str, str, int, str, str],
        transition: olis_actions.Transition,
        index: dict[tuple[str, int], dict[str, Any]],
    ) -> Signal | None:
        """Render one transition, or None when its ActionDate cannot be read."""
        session, prefix, number, _action_id, chamber, _state = key
        published_at = _action_datetime(transition.action_date)
        if published_at is None:
            return None

        record = index.get((prefix, number), {})
        measure = f"{prefix} {number}"
        title = self._field(record, "title") or "not stated"
        house = "House" if chamber == "H" else "Senate" if chamber == "S" else chamber
        frm = transition.from_state or "no recorded prior state"

        # `AtTheRequestOf` is served with its own parenthetical wording already attached
        # -- "(at the request of Representative Nathan Sosa)" -- so it is appended
        # verbatim rather than introduced, and omitted entirely when absent (it is
        # populated on 139 of 2026R1's 304 measures). Measures carries no sponsor field
        # at all, so there is no sponsor clause to write: saying "sponsored by not
        # stated" on every line states nothing and reads as missing data.
        requested_by = first_present(record, "AtTheRequestOf")
        request_clause = f" The measure was introduced {requested_by.strip()}." if requested_by else ""
        location = self._field(record, "status") or "not stated"

        text = (
            f"OLIS procedural transition for {measure} in the {session} session, "
            f"titled: {title} The {house} record moves the measure from {frm} to "
            f"{transition.to_state}, recorded at {published_at.isoformat()}. The action "
            f"text as published reads, verbatim: {transition.action_text}"
            f"{request_clause} The measure's CurrentLocation field reads {location}. "
            f"This state is derived instead from the measure's full action history under "
            f"rule set {olis_actions.RULES_VERSION}, rule {transition.rule_id}, because "
            f"CurrentLocation reports one current position rather than the sequence that "
            f"produced it and reports a vetoed measure as tabled."
        )

        return Signal(
            source=self.name,
            source_type=self.source_type,
            text=text,
            title=f"{measure}: {frm} to {transition.to_state} ({house})",
            url=self._field(record, "url") or self._measure_url(record) or _overview_url(session, prefix, number),
            author=house,
            published_at=published_at,
            credibility=self.credibility,
            meta={
                "session": session,
                "measure": measure,
                "chamber": chamber,
                "from_state": transition.from_state,
                "to_state": transition.to_state,
                "action_id": transition.action_id,
                "action_text": transition.action_text,
                "rules_version": olis_actions.RULES_VERSION,
            },
        )

    @staticmethod
    def _measure_index(records: list[dict[str, Any]]) -> dict[tuple[str, int], dict[str, Any]]:
        """
        Index Measures rows by `(prefix, number)` for the join onto action rows.

        `MeasureNumber` is served as a *string* by Measures ("201") and as an *int* by
        MeasureHistoryActions (1), so the key is coerced. Without that every join
        misses and every transition publishes with "not stated" for its title.
        """
        index: dict[tuple[str, int], dict[str, Any]] = {}
        for rec in records:
            prefix, number = rec.get("MeasurePrefix"), rec.get("MeasureNumber")
            if prefix is None or number is None:
                continue
            try:
                index[(str(prefix), int(number))] = rec
            except (TypeError, ValueError):
                continue
        return index

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


# ── Module helpers ───────────────────────────────────────────────────────────


def _select_sessions(
    rows: list[dict[str, Any]],
    lookback_days: int,
    now: datetime | None = None,
) -> list[str]:
    """
    Pick the session keys worth harvesting from a LegislativeSessions payload.

    Pure, so the selection rule is testable without a network round trip.

    `DefaultSession` is never read. It points at the interim, and an adapter that
    followed it would report healthy while harvesting nothing -- which is the failure
    mode this whole path guards against.

    `EndDate` is not read either: it is null for every recent session (24 of the 41 the
    service returns), so it cannot bound anything. `BeginDate` is the only usable
    filter.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=lookback_days)

    candidates: list[tuple[datetime, str]] = []
    for row in rows:
        key = str(row.get("SessionKey") or "").strip()
        if not key or _INTERIM_KEY.match(key):
            continue
        begins = parse_timestamp(row.get("BeginDate"))
        if begins is None:
            continue
        candidates.append((begins, key))

    if not candidates:
        return []

    candidates.sort()
    recent = [key for begins, key in candidates if begins >= cutoff]
    if recent:
        return recent
    # Nothing inside the window -- a long gap between sessions, or a lookback set
    # short. Harvesting nothing is worse than harvesting one stale session, so fall
    # back to the newest non-interim key rather than returning empty.
    return [candidates[-1][1]]


def _action_datetime(raw: Any) -> datetime | None:
    """
    Read an `ActionDate` as an aware timestamp.

    ActionDate is naive *local* time (America/Los_Angeles), not UTC. `parse_timestamp`
    reads a naive value as UTC, which would shift every transition by seven or eight
    hours; this localizes first and converts after. An unreadable date returns None and
    the transition is dropped -- never defaulted to now, which would make an undated
    row look fresh and slip it past the velocity gate.
    """
    parsed = parse_timestamp(raw)
    if parsed is None:
        return None
    text = str(raw).strip()
    # parse_timestamp stamps a naive value as UTC. Anything that arrived naive is
    # local, so re-stamp it; anything that carried an offset is left alone.
    if not (text.endswith("Z") or _HAS_OFFSET.search(text)):
        parsed = parsed.replace(tzinfo=_OLIS_TZ)
    return parsed.astimezone(timezone.utc)


def _overview_url(session: str, prefix: str, number: int) -> str:
    """OLIS overview link for a measure the Measures payload did not carry."""
    return f"{_OLIS_WEB_BASE}/{session}/Measures/Overview/{prefix}{number}"
