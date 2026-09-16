"""
The OLIS adapter's procedural-transition path, end to end over a mocked OData service.

What is being protected here is the emission contract: one Signal per procedural
transition not previously emitted. Not one per measure, and not one per measure whose
final state changed -- both of those collapse real movement into a single line and
both of them miss a retroactively inserted row entirely.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from pdx1.sources import OlisAdapter, olis_actions
from pdx1.store import DualWriteStore

SESSION = "2026R1"

SESSIONS_PAYLOAD = [
    {"SessionKey": "2026R1", "BeginDate": "2026-02-02T00:00:00", "EndDate": None, "DefaultSession": False},
    {"SessionKey": "2025I1", "BeginDate": "2025-06-27T00:00:00", "EndDate": None, "DefaultSession": True},
]

# MeasureNumber is a string here on purpose: that is how the live Measures collection
# serves it, while MeasureHistoryActions serves an int. The join has to survive it.
MEASURES_PAYLOAD = [
    {
        "MeasurePrefix": "SB",
        "MeasureNumber": "976",
        "SessionKey": SESSION,
        "CatchLine": "Relating to transportation funding.",
        "CurrentLocation": "Senate - Tabled",
        "AtTheRequestOf": "Governor Kotek",
        "ModifiedDate": "2026-02-20T09:00:00",
    },
    {
        "MeasurePrefix": "SB",
        "MeasureNumber": "579",
        "SessionKey": SESSION,
        "CatchLine": "Relating to land use.",
        "CurrentLocation": "Senate - In committee",
        "ModifiedDate": "2026-02-20T09:00:00",
    },
]


def _action(history_id, prefix, number, chamber, date, text):
    return {
        "MeasureHistoryId": history_id,
        "SessionKey": SESSION,
        "MeasurePrefix": prefix,
        "MeasureNumber": number,
        "Chamber": chamber,
        "ActionDate": date,
        "ActionText": text,
        "VoteText": None,
    }


#: SB 976 moves; SB 579 carries the one row in the real corpus that no rule claims
#: ("Rescission of the subsequent referral denied by Order of the President" -- the
#: actual 2023R1 halt), so the halt path is exercised against a real payload rather
#: than an invented one.
BASE_ACTIONS = [
    _action(1, "SB", 976, "S", "2026-02-10T08:00:00",
            "Introduction and first reading. Referred to President's desk."),
    _action(2, "SB", 976, "S", "2026-02-12T08:00:00", "Public Hearing held."),
    _action(3, "SB", 976, "S", "2026-02-13T08:00:00", "Work Session held."),
    _action(5, "SB", 976, "S", "2026-02-20T08:00:00",
            "Third reading. Carried by Nash. Passed."),
    _action(9, "SB", 579, "S", "2026-02-11T08:00:00",
            "Introduction and first reading. Referred to President's desk."),
    _action(10, "SB", 579, "S", "2026-02-14T08:00:00",
            "Rescission of the subsequent referral denied by Order of the President"),
]

#: A row inserted into the middle of an already-replayed history. OLIS does this: 2,188
#: of 2025R1's rows were created at least a day after their own ActionDate, one of them
#: 399.9 days after. It opens the second chamber, which is a new emittable transition
#: even though nothing changed at the tail of the Senate's history.
BACKDATED = _action(
    4, "SB", 976, "H", "2026-02-15T08:00:00", "First reading. Referred to Speaker's desk."
)


def _responder(actions):
    """Dispatch a mocked httpx.get by which OData collection the URL names."""

    def _get(url, **_kwargs):
        if "LegislativeSessions" in url:
            body = {"value": SESSIONS_PAYLOAD}
        elif "MeasureHistoryActions" in url:
            body = {"value": actions}
        elif "Measures" in url:
            body = {"value": MEASURES_PAYLOAD}
        else:  # pragma: no cover - a URL the adapter should never request
            raise AssertionError(f"unexpected request: {url}")
        response = MagicMock()
        response.json.return_value = body
        response.text = json.dumps(body)
        response.url = url
        response.raise_for_status = MagicMock()
        return response

    return _get


@pytest.fixture
def store(tmp_path) -> DualWriteStore:
    return DualWriteStore(tmp_path / "signals.jsonl", tmp_path / "pdx1.db")


def _run(store, actions, **kwargs):
    adapter = OlisAdapter(live=True, store=store, **kwargs)
    with patch("httpx.get", side_effect=_responder(actions)):
        result = adapter.safe_fetch()
    assert result.ok, result.errors
    transitions = [s for s in result.signals if s.meta]
    return adapter, transitions


def _states(signals):
    return sorted((s.meta["measure"], s.meta["chamber"], s.meta["to_state"]) for s in signals)


# ── (a) only EMITTABLE transitions produce Signals ───────────────────────────


def test_emits_only_emittable_transitions(store):
    _adapter, signals = _run(store, BASE_ACTIONS)

    assert _states(signals) == [("SB 976", "S", "introduced"), ("SB 976", "S", "passed")]
    # The public hearing and work session rows were replayed and deliberately not sent.
    assert not {s.meta["to_state"] for s in signals} & {
        "committee",
        "public_hearing",
        "work_session",
    }


def test_transition_signal_carries_structured_meta(store):
    _adapter, signals = _run(store, BASE_ACTIONS)
    passed = next(s for s in signals if s.meta["to_state"] == "passed")

    assert passed.meta == {
        "session": SESSION,
        "measure": "SB 976",
        "chamber": "S",
        "from_state": "third_reading",
        "to_state": "passed",
        "action_id": 5,
        "action_text": "Third reading. Carried by Nash. Passed.",
        "rules_version": olis_actions.RULES_VERSION,
    }
    # The prose carries the verbatim action text and the title from the Measures join,
    # so a reader never has to be told to go and look the measure up.
    assert "Third reading. Carried by Nash. Passed." in passed.text
    assert "Relating to transportation funding." in passed.text
    assert passed.published_at.tzinfo is not None


def test_action_date_is_read_as_pacific_not_utc(store):
    """ActionDate is naive local time; reading it as UTC shifts every transition."""
    _adapter, signals = _run(store, BASE_ACTIONS)
    passed = next(s for s in signals if s.meta["to_state"] == "passed")

    # 2026-02-20 08:00 Pacific (PST, UTC-8) is 16:00 UTC.
    assert passed.published_at.isoformat() == "2026-02-20T16:00:00+00:00"


def test_measures_fetch_is_not_removed(store):
    """The Measures pass still runs and still produces its own Signals."""
    adapter = OlisAdapter(live=True, store=store)
    with patch("httpx.get", side_effect=_responder(BASE_ACTIONS)):
        result = adapter.safe_fetch()

    assert [s for s in result.signals if not s.meta], "measure signals disappeared"


# ── (b) a second run over identical input emits nothing ──────────────────────


def test_second_run_over_identical_input_emits_nothing(store):
    _adapter, first = _run(store, BASE_ACTIONS)
    assert first

    _adapter, second = _run(store, BASE_ACTIONS)

    assert second == []
    assert store.olis_emitted_count(SESSION) == len(first)


# ── (c) a backdated row produces a new Signal ────────────────────────────────


def test_row_backdated_into_the_middle_produces_a_new_signal(store):
    _adapter, first = _run(store, BASE_ACTIONS)
    assert ("SB 976", "H", "introduced") not in _states(first)

    # Same tail, same final Senate state -- the new row lands in the middle.
    _adapter, second = _run(store, [*BASE_ACTIONS, BACKDATED])

    assert _states(second) == [("SB 976", "H", "introduced")]
    assert second[0].meta["action_id"] == 4


def test_backdated_row_is_not_re_emitted_on_the_next_cycle(store):
    _run(store, BASE_ACTIONS)
    _run(store, [*BASE_ACTIONS, BACKDATED])

    _adapter, third = _run(store, [*BASE_ACTIONS, BACKDATED])

    assert third == []


# ── (d) a halting measure is skipped without aborting the cycle ──────────────


def test_halting_measure_is_skipped_and_the_cycle_completes(store):
    adapter, signals = _run(store, BASE_ACTIONS)

    assert [mid for mid, _why in adapter.last_halts] == ["SB579"]
    assert "NO RULE MATCHED" in adapter.last_halts[0][1]
    # SB 579 halted; SB 976 still emitted, and nothing was recorded for SB 579.
    assert {s.meta["measure"] for s in signals} == {"SB 976"}
    assert not [row for row in store.olis_emitted(SESSION) if row[2] == 579]


def test_a_halt_does_not_make_the_adapter_report_failure(store):
    adapter = OlisAdapter(live=True, store=store)
    with patch("httpx.get", side_effect=_responder(BASE_ACTIONS)):
        result = adapter.safe_fetch()

    assert result.ok
    assert adapter.last_halts


# ── Bootstrap ────────────────────────────────────────────────────────────────


def test_bootstrap_records_everything_and_emits_nothing(store):
    _adapter, signals = _run(store, BASE_ACTIONS, bootstrap=True)

    assert signals == []
    assert store.olis_emitted_count(SESSION) == 2


def test_cycle_after_bootstrap_emits_only_what_is_new(store):
    _run(store, BASE_ACTIONS, bootstrap=True)

    _adapter, signals = _run(store, [*BASE_ACTIONS, BACKDATED])

    assert _states(signals) == [("SB 976", "H", "introduced")]


# ── Without a store ──────────────────────────────────────────────────────────


def test_no_store_means_no_transitions_rather_than_a_crash():
    """
    Without somewhere to record what it emitted, the adapter must not emit.

    Emitting anyway would re-send the whole session every cycle, which is worse than
    sending nothing and much harder to notice.
    """
    adapter = OlisAdapter(live=True)
    with patch("httpx.get", side_effect=_responder(BASE_ACTIONS)):
        result = adapter.safe_fetch()

    assert result.ok
    assert not [s for s in result.signals if s.meta]


# ── Persistence shape ────────────────────────────────────────────────────────


def test_emitted_rows_record_the_rules_version(tmp_path, store):
    _run(store, BASE_ACTIONS)

    import sqlite3

    conn = sqlite3.connect(store.db_path)
    versions = {row[0] for row in conn.execute("SELECT rules_version FROM olis_emitted")}
    conn.close()

    assert versions == {olis_actions.RULES_VERSION}


def test_session_key_is_part_of_the_identity(store):
    """SB 976 exists in more than one session; the two must not collide."""
    store.record_olis_emitted([("2019R1", "SB", 976, 5, "S", "passed")], "deadbeef")
    _adapter, signals = _run(store, BASE_ACTIONS)

    assert ("SB 976", "S", "passed") in _states(signals)


# ── Degradation ──────────────────────────────────────────────────────────────


def test_a_failing_action_fetch_does_not_cost_the_measure_signals(store):
    """
    The transition pass is additive and its failure must degrade, not halt.

    This is the outage case: the base class serves the last-good Measures payload from
    cache, and the action fetch that follows has nothing to talk to. Losing the cached
    measures as well would turn a partial outage into a dead feed.
    """

    def _get(url, **_kwargs):
        if "MeasureHistoryActions" in url:
            raise RuntimeError("connection reset")
        return _responder(BASE_ACTIONS)(url)

    adapter = OlisAdapter(live=True, store=store)
    with patch("httpx.get", side_effect=_get):
        result = adapter.safe_fetch()

    assert result.ok, result.errors
    assert [s for s in result.signals if not s.meta], "measure signals were lost"
    assert not [s for s in result.signals if s.meta]
    assert store.olis_emitted_count(SESSION) == 0
