"""
The copied proc_track mapping, replayed against a real measure trajectory.

SB 976 (2025R1) is the case worth pinning: it passed both chambers, was signed by both
presiding officers, was vetoed, and had that veto sustained. `CurrentLocation` reports
it as "Senate - Tabled", which is the misreading this whole module exists to replace.
"""

from __future__ import annotations

import json

from pdx1.sources import olis_actions


def _rows(fixture_dir) -> list[dict]:
    """
    Map the checked-in trajectory onto the OData row shape the rules expect.

    The fixture is proc_track's normalised export (`history_id`, `occurred_at`,
    `action_text`); the mapping module reads the service's own spellings. Doing the
    translation here keeps the test honest about which shape is which.
    """
    payload = json.loads((fixture_dir / "sb976_trajectory.json").read_text(encoding="utf-8"))
    rows = [
        {
            "MeasureHistoryId": action["history_id"],
            "MeasurePrefix": "SB",
            "MeasureNumber": 976,
            "Chamber": action["chamber"],
            "ActionDate": action["occurred_at"],
            "ActionText": action["action_text"],
        }
        for action in payload["actions"]
    ]
    rows.sort(key=lambda r: (r["ActionDate"], r["MeasureHistoryId"]))
    return rows


def test_sb976_replays_to_veto_sustained(fixture_dir):
    """The measure's real end state, per chamber."""
    item, halt, _uncovered = olis_actions.replay("SB976", _rows(fixture_dir))

    assert halt is None, f"replay halted: {halt}"
    assert item.states == {"S": "veto_sustained", "H": "signed_by_presiding"}


def test_sb976_transitions_agree_with_replay(fixture_dir):
    """`transitions()` is `replay()` with the row identity kept -- same end state."""
    rows = _rows(fixture_dir)
    replayed, _halt, _ = olis_actions.replay("SB976", rows)
    walked, found = olis_actions.transitions(rows)

    assert walked.states == replayed.states
    # Every transition names a row that exists in the source history, and they come
    # back in history order.
    ids = {r["MeasureHistoryId"] for r in rows}
    assert found
    assert all(t.action_id in ids for t in found)
    order = {r["MeasureHistoryId"]: i for i, r in enumerate(rows)}
    positions = [order[t.action_id] for t in found]
    assert positions == sorted(positions)


def test_sb976_emits_the_veto_chain(fixture_dir):
    """The emittable set covers the facts a reader would call news."""
    _item, found = olis_actions.transitions(_rows(fixture_dir))
    emitted = [(t.chamber, t.from_state, t.to_state) for t in found if t.to_state in olis_actions.EMITTABLE]

    assert ("S", "signed_by_presiding", "vetoed") in emitted
    assert ("S", "vetoed", "veto_sustained") in emitted
    assert ("H", "passed", "signed_by_presiding") in emitted
    # Committee churn never reaches a Signal.
    assert not {"committee", "public_hearing", "work_session"} & {t[2] for t in emitted}


def test_committee_states_are_never_emittable():
    assert not olis_actions.EMITTABLE & {"committee", "public_hearing", "work_session"}


def test_carried_over_is_not_a_state():
    """
    `carried_over` parses but maps to no state, so it cannot be emitted.

    2026R1 has 94 matching rows and most are routine floor-calendar churn ("Carried
    over to 2-13 by unanimous consent") rather than the end-of-session outcome. The
    rule cannot separate the two, so neither is emitted.
    """
    emits, _gaps = olis_actions.parse_row("Carried over to 2027-01 by virtue of adjournment.")

    assert emits, "the text should still be claimed by a rule"
    assert all(e.state is None for e in emits)
    assert "carried_over" not in olis_actions.EMITTABLE
