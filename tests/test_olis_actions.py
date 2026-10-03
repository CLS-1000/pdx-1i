"""
The copied proc_track mapping, replayed against a real measure trajectory.

SB 976 (2025R1) is the case worth pinning: it passed both chambers, was signed by both
presiding officers, was vetoed, and had that veto sustained. `CurrentLocation` reports
it as "Senate - Tabled", which is the misreading this whole module exists to replace.
"""

from __future__ import annotations

import json

import pytest

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


# ── Line-item vetoes ─────────────────────────────────────────────────────────
#
# Real OLIS histories for the two measures in the 2019-2025 corpus where a line-item
# veto was later sustained. Both are law (Chapter 644, 2019; Chapter 605, 2023). The
# sustain row reads exactly like a full veto's, and before this rule set both replayed
# to veto_sustained -- a published record that an enacted law had been vetoed.


def _line_item_rows(fixture_dir, key):
    import json

    return json.loads((fixture_dir / "line_item_veto.json").read_text())[key]


@pytest.mark.parametrize(
    ("key", "chamber"), [("2023R1:SB5506", "S"), ("2019R1:HB5050", "H")]
)
def test_sustained_line_item_veto_leaves_the_measure_enacted(fixture_dir, key, chamber):
    item, halt, _ = olis_actions.replay(key, _line_item_rows(fixture_dir, key))
    assert halt is None
    assert item.states[chamber] == "line_item_veto_sustained"
    assert item.payload["veto_type"] == "line_item"


def test_line_item_transitions_record_the_reinterpreted_state(fixture_dir):
    _, found = olis_actions.transitions(_line_item_rows(fixture_dir, "2023R1:SB5506"))
    tail = [(t.from_state, t.to_state) for t in found[-2:]]
    assert tail == [
        ("signed_by_presiding", "enacted_line_item_veto"),
        ("enacted_line_item_veto", "line_item_veto_sustained"),
    ]
    assert "veto_sustained" not in {t.to_state for t in found}


def test_both_line_item_states_are_emittable():
    assert {"enacted_line_item_veto", "line_item_veto_sustained"} <= olis_actions.EMITTABLE


def test_a_full_veto_still_ends_veto_sustained(fixture_dir):
    """The contextual reading applies only after a line-item signing."""
    rows = [
        {"MeasureHistoryId": 1, "Chamber": "S", "ActionDate": "2025-06-01T09:00:00",
         "ActionText": "Introduction and first reading. Referred to President's desk."},
        {"MeasureHistoryId": 2, "Chamber": "S", "ActionDate": "2025-06-02T09:00:00",
         "ActionText": "Third reading.  Carried by Example.  Passed."},
        {"MeasureHistoryId": 3, "Chamber": "S", "ActionDate": "2025-06-03T09:00:00",
         "ActionText": "President signed."},
        {"MeasureHistoryId": 4, "Chamber": "S", "ActionDate": "2025-06-04T09:00:00",
         "ActionText": "Governor vetoed."},
        {"MeasureHistoryId": 5, "Chamber": "S", "ActionDate": "2025-06-05T09:00:00",
         "ActionText": "Veto sustained in accordance with Art. V, sec. 15b, Oregon Constitution."},
    ]
    item, halt, _ = olis_actions.replay("SB1", rows)
    assert halt is None
    assert item.states["S"] == "veto_sustained"


def test_nothing_leaves_enacted():
    item = olis_actions.Item("HB1", states={"S": "enacted"})
    with pytest.raises(olis_actions.Halt):
        olis_actions.step(item, "veto_sustained", "S", "2025-01-01", "r", {})


# ── Motions and conference rows are not measure votes ────────────────────────
#
# Each of these once set a measure's state, and a transition edge was widened to let
# the wrong state through. The rows are real OLIS action text.


@pytest.mark.parametrize(
    "text",
    [
        "Motion to take and place consideration of action on concurrence to June 27 "
        "calendar. Motion failed.",
        "Motion to suspend the rules passed. Ayes, 55; Excused, 5--Alonso Leon, Bynum.",
        "Conference Committee Report read in Senate.",
    ],
)
def test_motion_and_report_rows_change_no_state(text):
    emits, _ = olis_actions.parse_row(text)
    assert emits, "the row must still be recognised, not fall through"
    assert all(e.state is None for e in emits), [(e.rule_id, e.state) for e in emits]


def test_appointing_conferees_puts_the_chamber_in_committee():
    emits, _ = olis_actions.parse_row(
        "Senators Dembrow, Girod, Riley appointed Senate conferees."
    )
    assert [(e.rule_id, e.state) for e in emits] == [("conferees_appointed", "committee")]


@pytest.mark.parametrize(
    ("frm", "to"),
    [
        ("passed", "failed"),
        ("second_reading", "passed"),
        ("third_reading", "signed_by_presiding"),
        ("passed", "second_reading"),
        ("second_reading", "signed_by_presiding"),
    ],
)
def test_edges_that_only_papered_over_mapping_bugs_are_gone(frm, to):
    assert to not in olis_actions.CHAMBER_FLOW[frm]
