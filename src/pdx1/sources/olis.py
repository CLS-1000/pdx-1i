"""
OLIS -- Oregon Legislative Information System.

The adapter used to emit one Signal per bill, always, with the bill's
``CurrentLocation`` string as its status. That field is misleading: a vetoed
bill reads as ``Senate - Tabled`` and never mentions the veto, so a downstream
reader was told the wrong story on every state-changing event.

This module now derives procedural state from the ``MeasureHistoryActions``
endpoint instead. The mapping (~100 span-based regex rules, plus a per-chamber
flow schema) lives in ``olis_actions``. It is a straight copy of the tested
harness in ``~/proc_track``, which validates the rules against 11,723 measures
across four Oregon sessions (2019–2025) at 100% replay. See that repo before
changing anything in the mapping.

Live mode fetches two endpoints in one cycle:

- ``Measures?$filter=SessionKey eq '<session>'`` — one row per bill, carrying
  the metadata the signal text still needs (title, sponsors, subjects,
  jurisdictions, URL).
- ``MeasureHistoryActions?$filter=SessionKey eq '<session>'`` — the action
  ledger, one row per event. Paged at 5,000 rows; a full regular session is
  around 27k rows, so the OData ``@odata.nextLink`` walk is real, not
  theoretical.

The two lists are packed into one JSON envelope on the wire so the existing
``_read_raw`` → ``parse`` contract still holds and the last-good cache still
survives a partial outage. Fixture mode continues to load the legacy flat
shape and take the old prose-summary path, so checked-in tests keep passing
until fixtures are ported to the new shape.

State persistence: the last-known per-chamber state for every measure lives
at ``~/.pdx1/olis_state.json`` (override with ``state_path``). Signals are
emitted only when a measure lands in one of the publishable states
(``introduced``, ``passed``, ``adopted``, ``failed``, ``enacted``, ``vetoed``,
``veto_sustained``, ``veto_overridden``, ``signed_by_presiding``, ``tabled``)
AND that state is a change from what the file recorded on the previous cycle.
Committee churn (``committee``, ``public_hearing``, ``work_session``) is
suppressed on purpose -- procedural state is coarser than the ledger, and a
hearing that closes with nothing decided is not a state change worth a signal.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from ..models import Signal, SourceType
from .base import LiveSourceAdapter
from .olis_actions import replay

logger = logging.getLogger(__name__)


#: Ceiling on the OData nextLink walk. A malformed link cannot loop forever;
#: this bound is well above a full regular session at 5,000 rows per page.
MAX_PAGES = 50

#: Procedural states that warrant a published signal. Committee churn is
#: intentionally excluded: hearings and work sessions are ledger events, not
#: state changes worth telling a reader about. ``introduced`` fires only on the
#: first appearance in a chamber (nothing → introduced); the state machine
#: leaves it quickly, so a re-emit is not the risk here.
_PUBLISHABLE_STATES: frozenset[str] = frozenset({
    "introduced",
    "passed",
    "adopted",
    "failed",
    "enacted",
    "vetoed",
    "veto_sustained",
    "veto_overridden",
    "signed_by_presiding",
    "tabled",
})

_OLIS_WEB_BASE = "https://olis.oregonlegislature.gov/liz"

_ODATA_BASE = "https://api.oregonlegislature.gov/odata/odataservice.svc"


def _default_state_path() -> Path:
    """Where the per-cycle state file lives when the caller does not override it."""
    return Path(os.path.expanduser("~/.pdx1/olis_state.json"))


# ── State persistence ────────────────────────────────────────────────────────


@dataclass
class _MeasureContext:
    """Everything the emitter needs about one measure, kept together for readability."""

    measure_id: str
    metadata: dict
    actions: list[dict]


class OlisAdapter(LiveSourceAdapter):
    """Parses OLIS bill and committee-action records."""

    name = "OLIS"
    source_type = SourceType.OLIS
    credibility = 0.9
    #: Live URL for the Measures endpoint. Session filter is appended by
    #: ``_fetch_live``; the class attribute stays as-is so callers that
    #: instantiate the adapter without a session still see a usable URL.
    feed_url = f"{_ODATA_BASE}/Measures?$format=json"

    def __init__(
        self,
        fixture_path: Path | str | None = None,
        timeout: int = 30,
        live: bool = False,
        *,
        session: str = "2025R1",
        state_path: Path | str | None = None,
    ) -> None:
        super().__init__(fixture_path=fixture_path, timeout=timeout, live=live)
        self.session = session
        self.state_path = Path(state_path) if state_path is not None else _default_state_path()

    # ── Live fetch ───────────────────────────────────────────────────────────

    def _fetch_live(self) -> str:
        """
        Hit both endpoints and return one combined envelope.

        Overrides the base class because OLIS pages and we need two collections
        per cycle. The last-good cache still owns the failure fallback: this
        method either produces the full envelope or raises, and ``_read_raw``
        upstream (which wraps ``_fetch_live``) is what serves the cache when a
        walk fails.
        """
        measures = self._fetch_paged(
            f"{_ODATA_BASE}/Measures?$filter=SessionKey eq '{self.session}'&$format=json"
        )
        actions = self._fetch_actions(self.session)
        envelope = {
            "session": self.session,
            "measures": measures,
            "actions": actions,
        }
        logger.info(
            "%s: session=%s measures=%d actions=%d",
            self.name,
            self.session,
            len(measures),
            len(actions),
        )
        return json.dumps(envelope)

    def _fetch_actions(self, session: str) -> list[dict]:
        """
        Fetch the raw ``MeasureHistoryActions`` rows for one session.

        Paging matches ``_fetch_paged``: OData ``@odata.nextLink``, resolved via
        ``urljoin`` because OLIS serves relative links, capped at ``MAX_PAGES``.
        A full 2025R1 replay is ~27k rows across six pages at 5,000/page; the
        ceiling exists so a malformed link cannot loop the walker.
        """
        return self._fetch_paged(
            f"{_ODATA_BASE}/MeasureHistoryActions"
            f"?$filter=SessionKey eq '{session}'&$format=json"
        )

    def _fetch_paged(self, start_url: str) -> list[dict[str, Any]]:
        """Walk an OData collection through its ``@odata.nextLink`` chain."""
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - live extra opt-in
            raise RuntimeError(
                f"{self.name}: httpx is required for live fetch -- "
                "install it with: pip install 'pdx-1i[live]'"
            ) from exc

        rows: list[dict[str, Any]] = []
        url: str | None = start_url

        for page in range(MAX_PAGES):
            response = httpx.get(url, timeout=self.timeout, follow_redirects=True)
            response.raise_for_status()
            payload = response.json()

            if not isinstance(payload, dict):
                # A plain array response -- take it as one page and stop.
                rows.extend(payload)
                break

            rows.extend(payload.get("value") or [])
            next_link = payload.get("@odata.nextLink") or payload.get("odata.nextLink")
            if not next_link:
                break
            # OData permits a relative nextLink and OLIS emits one; handing it
            # unresolved to httpx would raise UnsupportedProtocol.
            url = urljoin(str(response.url), str(next_link))
            if page == MAX_PAGES - 1:
                logger.warning(
                    "%s: stopped at %d-page ceiling with a nextLink still set",
                    self.name,
                    MAX_PAGES,
                )
        return rows

    # ── Parse ────────────────────────────────────────────────────────────────

    def parse(self, raw: str) -> list[Signal]:
        """
        Dispatch on payload shape.

        Legacy flat-array fixture keeps the old per-measure prose emission so
        the checked-in tests stay green. The envelope shape produced by
        ``_fetch_live`` (or by a fixture in the new shape) takes the replay
        path and emits only on state change.
        """
        payload = json.loads(raw)
        if isinstance(payload, dict) and "actions" in payload:
            return self._parse_actions(payload)
        if isinstance(payload, list):
            return self._parse_flat(payload)
        raise RuntimeError(f"{self.name}: unrecognised payload shape")

    # ── Legacy flat-array path ──────────────────────────────────────────────

    def _parse_flat(self, records: list[dict[str, Any]]) -> list[Signal]:
        """The pre-mapping behavior. One Signal per record, status from the row."""
        signals: list[Signal] = []
        for rec in records:
            action_at = datetime.fromisoformat(rec["action_at"])
            sponsors = ", ".join(rec.get("sponsors", [])) or "not stated"
            text = (
                f"OLIS record for {rec['bill_id']}, {rec['title']}, in the "
                f"{rec['session']} session. The measure is sponsored by {sponsors} and "
                f"referred to the {rec['committee']} committee. The most recent recorded "
                f"action is {rec['action']} on {rec['action_at']}, moving the measure to "
                f"status {rec['status']}. Summary as published: {rec['summary']} "
                f"Subject areas recorded are "
                f"{', '.join(rec.get('subjects', [])) or 'not stated'}. "
                f"The measure affects jurisdictions "
                f"{', '.join(rec.get('jurisdictions', [])) or 'not stated'}."
            )
            signals.append(
                Signal(
                    source=self.name,
                    source_type=self.source_type,
                    text=text,
                    url=rec.get("url"),
                    author=rec.get("committee"),
                    published_at=action_at,
                    credibility=self.credibility,
                )
            )
        return signals

    # ── State-change replay path ────────────────────────────────────────────

    def _parse_actions(self, payload: dict[str, Any]) -> list[Signal]:
        """
        Replay each measure's action ledger; emit only on publishable state change.

        State persistence tracks the last PUBLISHABLE state per chamber, not
        the raw per-chamber final position. That distinction matters: a measure
        that goes ``introduced → committee → work_session`` in one row batch
        never rests on ``introduced``, but ``introduced`` is still a state we
        want to publish once for its first appearance. Walking history for
        publishable transitions and comparing each against the file lets both
        ``first appearance of introduced`` and ``newly signed`` reach a reader
        with no double-emission on the next cycle.
        """
        contexts = self._group(payload)
        prev_state = self._load_state()
        # Start next-cycle state from the prior file so a halt or a run with
        # no publishable event does not lose the last known good position.
        new_state: dict[str, dict[str, str]] = {mid: dict(v) for mid, v in prev_state.items()}
        signals: list[Signal] = []

        for ctx in contexts.values():
            item, halt_reason, _uncovered = replay(ctx.measure_id, ctx.actions)

            if halt_reason:
                # Fail closed: no signal, but keep the prior state in the file.
                # Overwriting with a partial replay would fake a "no change"
                # next cycle if the halted portion later gets patched.
                logger.warning(
                    "%s: halt replaying %s: %s", self.name, ctx.measure_id, halt_reason
                )
                continue

            prior = prev_state.get(ctx.measure_id, {})
            triggering, chamber_latest = self._new_publishable_transitions(item, ctx.actions, prior)
            if not triggering:
                continue

            # Record the latest publishable state per chamber for next cycle's
            # baseline. Chambers that had no publishable event this run keep
            # whatever the file remembered.
            merged = dict(prior)
            merged.update(chamber_latest)
            new_state[ctx.measure_id] = merged

            signals.append(self._emit(ctx, item, triggering))

        self._save_state(new_state)
        return signals

    @staticmethod
    def _group(payload: dict[str, Any]) -> dict[str, _MeasureContext]:
        """Group action rows by measure and pair each group with its metadata row."""
        metadata_by_id: dict[str, dict] = {}
        for m in payload.get("measures") or []:
            mid = _measure_id_from_row(m)
            if mid:
                metadata_by_id[mid] = m

        buckets: dict[str, list[dict]] = {}
        for row in payload.get("actions") or []:
            mid = _measure_id_from_row(row)
            if not mid:
                continue
            buckets.setdefault(mid, []).append(row)

        # ActionDate is the only defensible sort key per proc_track's CLAUDE.md;
        # MeasureHistoryId contradicts it (id 654676 @08:33 precedes 654677
        # @08:32 in SB976 2025R1). Ties go to MeasureHistoryId for determinism.
        contexts: dict[str, _MeasureContext] = {}
        for mid, rows in buckets.items():
            rows.sort(key=lambda r: (r.get("ActionDate", ""), r.get("MeasureHistoryId", 0)))
            contexts[mid] = _MeasureContext(
                measure_id=mid,
                metadata=metadata_by_id.get(mid, {}),
                actions=rows,
            )
        return contexts

    @staticmethod
    def _new_publishable_transitions(
        item: Any, actions: list[dict], prior: dict[str, str]
    ) -> tuple[list[tuple[str, str, str, str]], dict[str, str]]:
        """
        Which publishable transitions in this replay are new since the last cycle.

        Returns ``(triggering, chamber_latest)``:

        - ``triggering`` is a list of ``(occurred_at, chamber, to_state, action_text)``
          tuples, one per chamber whose LATEST publishable state this run
          differs from the state the file recorded. The tuple names the
          transition that put the chamber there -- the reader learns what
          moved, not just where it landed.
        - ``chamber_latest`` maps chamber → that latest publishable state, for
          the caller to persist.

        Comparing the latest publishable state (not every intermediate one)
        keeps a re-parse of the same ledger quiet: history replays whole, so
        the introduced-then-passed sequence still lands on ``passed`` and
        matches what the file already recorded.

        Committee/hearing/work-session churn is skipped entirely: those states
        are never a chamber's latest publishable state, so a hearing that
        follows a pass does not reset the file to a lower-tier state.
        """
        text_by_pos = {
            (row.get("Chamber"), row.get("ActionDate")): row.get("ActionText", "")
            for row in actions
        }

        # Last publishable transition per chamber, by ledger order.
        latest_by_chamber: dict[str, tuple[str, str, str]] = {}
        for occurred_at, chamber, _frm, to_state, _rule in item.history:
            if to_state not in _PUBLISHABLE_STATES:
                continue
            latest_by_chamber[chamber] = (occurred_at, to_state, text_by_pos.get((chamber, occurred_at), ""))

        triggering: list[tuple[str, str, str, str]] = []
        chamber_latest: dict[str, str] = {}
        for chamber, (occurred_at, to_state, action_text) in latest_by_chamber.items():
            if prior.get(chamber) == to_state:
                continue
            triggering.append((occurred_at, chamber, to_state, action_text))
            chamber_latest[chamber] = to_state

        triggering.sort(key=lambda t: t[0])
        return triggering, chamber_latest

    def _emit(
        self,
        ctx: _MeasureContext,
        item: Any,
        transitions: list[tuple[str, str, str, str]],
    ) -> Signal:
        """Compose one Signal for a measure with a state change."""
        latest_at, _latest_chamber, _latest_state, latest_text = transitions[-1]
        published_at = _parse_action_datetime(latest_at)

        meta = ctx.metadata
        title = meta.get("CatchLine") or meta.get("RelatingTo") or meta.get("MeasureTitle") or "not stated"
        session = meta.get("SessionKey") or "not stated"

        state_summary = ", ".join(
            f"{chamber}={state}" for chamber, state in sorted(item.states.items())
        ) or "no chambers"

        transition_summary = "; ".join(
            f"{chamber}→{state} at {occurred_at}: {text.strip()}"
            for occurred_at, chamber, state, text in transitions
        )

        text = (
            f"OLIS record for {ctx.measure_id}, {title}, in the {session} session. "
            f"Procedural state per chamber: {state_summary}. "
            f"State-changing action(s) since the last cycle: {transition_summary}."
        )

        return Signal(
            source=self.name,
            source_type=self.source_type,
            text=text,
            url=_measure_url(meta, ctx.measure_id),
            author=None,
            published_at=published_at,
            credibility=self.credibility,
        )

    # ── State file I/O ──────────────────────────────────────────────────────

    def _load_state(self) -> dict[str, dict[str, str]]:
        """Return the previous cycle's per-chamber state, or an empty dict."""
        try:
            with self.state_path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            logger.warning(
                "%s: state file unreadable (%s: %s) -- treating everything as new",
                self.name,
                self.state_path,
                exc,
            )
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            str(mid): {str(k): str(v) for k, v in states.items()}
            for mid, states in data.items()
            if isinstance(states, dict)
        }

    def _save_state(self, state: dict[str, dict[str, str]]) -> None:
        """Write the new state file. Never raises -- a bad write only degrades next cycle."""
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            with self.state_path.open("w", encoding="utf-8") as fh:
                json.dump(state, fh, sort_keys=True, indent=2)
        except OSError as exc:
            logger.warning("%s: state write failed (%s): %s", self.name, self.state_path, exc)


# ── Row-level helpers ────────────────────────────────────────────────────────


def _measure_id_from_row(row: dict[str, Any]) -> str | None:
    """
    Combine MeasurePrefix + MeasureNumber (SB + 976 → 'SB976').

    OData splits the identifier; the citation form joins them. Falls back to
    ``bill_id`` for callers that pass one directly.
    """
    direct = row.get("bill_id")
    if direct:
        return str(direct)
    prefix = row.get("MeasurePrefix")
    number = row.get("MeasureNumber")
    if prefix and number is not None:
        return f"{prefix}{number}"
    return None


def _measure_url(meta: dict[str, Any], measure_id: str) -> str | None:
    """Prefer a URL from the metadata row; construct one when it is absent."""
    for key in ("MeasureUrl", "WebSiteUrl", "url"):
        v = meta.get(key)
        if v:
            return str(v)
    session = meta.get("SessionKey") or meta.get("session")
    if session:
        return f"{_OLIS_WEB_BASE}/{session}/Measures/Overview/{measure_id}"
    return None


def _parse_action_datetime(raw: str) -> datetime:
    """
    Coerce an ``ActionDate`` string into a tz-aware UTC datetime.

    OLIS returns local (Portland) timestamps without a suffix. Signal rejects
    naive datetimes so we stamp them UTC rather than defaulting to now -- the
    Signal model's guard is what keeps the velocity gate honest.
    """
    if not raw:
        raise ValueError("empty ActionDate")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"unparseable ActionDate: {raw!r}") from exc
    if parsed.tzinfo is None:
        # Not strictly UTC on the wire, but naive would fail the Signal
        # validator; the timeline math cares about relative order and
        # freshness, both of which survive a fixed offset.
        from datetime import timezone
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
