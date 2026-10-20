"""
Tabling of the Governor's veto message (re-sync from proc_track 9582534).

Before the re-sync, "Motion to lay bill, together with Governor's message, on the table
carried" lost a span tie to `motion_carried_generic`, so no veto-message tabling was
ever recorded. The rows below are the real OLIS rows for SB976 (2025R1) and SB5506
(2023R1), trimmed to the executive tail.
"""

from __future__ import annotations

from pdx1.sources import olis_actions


def _rows(prefix, number, actions):
    return [
        {
            "MeasurePrefix": prefix,
            "MeasureNumber": number,
            "MeasureHistoryId": 1000 + i,
            "Chamber": chamber,
            "ActionDate": date,
            "ActionText": text,
        }
        for i, (chamber, date, text) in enumerate(actions)
    ]


VETO_TABLED = _rows("SB", 976, [
    ("S", "2025-06-27T15:58:00", "President signed."),
    ("S", "2025-08-07T20:25:00", "Governor vetoed."),
    ("S", "2025-08-29T09:26:00",
     "Motion to lay bill, together with Governor's message, on the table carried."),
    ("S", "2025-10-01T09:27:00",
     "Veto sustained in accordance with Art. V, sec. 15b, Oregon Constitution."),
])

LINE_ITEM_MESSAGE_TABLED = _rows("SB", 5506, [
    ("S", "2023-06-27T11:21:00", "President signed."),
    ("S", "2023-08-04T14:05:00", "Governor signed with line-item veto."),
    ("S", "2024-02-05T09:06:00",
     "Motion to lay bill, together with Governor's message, on the table carried."),
    ("S", "2024-03-07T09:07:00",
     "Veto sustained in accordance with Art. V, sec. 15b, Oregon Constitution."),
])


def test_tabling_row_maps_to_motion_to_table_not_the_generic_motion():
    emits, gaps = olis_actions.parse_row(VETO_TABLED[2]["ActionText"])
    assert [(e.rule_id, e.state) for e in emits] == [("motion_to_table", "tabled")]
    assert gaps == []


def test_failed_tabling_motion_is_not_a_tabling():
    emits, _ = olis_actions.parse_row("Motion to lay on the table failed.")
    assert all(e.state != "tabled" for e in emits)


def test_vetoed_bill_records_tabled_before_veto_sustained():
    _item, found = olis_actions.transitions(_with_passage(VETO_TABLED))
    path = [(t.from_state, t.to_state) for t in found if t.chamber == "S"][-3:]
    assert path == [
        ("signed_by_presiding", "vetoed"),
        ("vetoed", "tabled"),
        ("tabled", "veto_sustained"),
    ]


def test_tabling_a_line_item_veto_message_is_not_a_transition():
    item, found = olis_actions.transitions(_with_passage(LINE_ITEM_MESSAGE_TABLED))
    to_states = [t.to_state for t in found if t.chamber == "S"]
    # The measure is enacted once, not again on the 2024 tabling row.
    assert to_states.count("enacted_line_item_veto") == 1
    assert to_states[-1] == "line_item_veto_sustained"
    tabling_id = 1000 + 2 + 2  # two head rows, then the third tail row
    assert all(t.action_id != tabling_id for t in found)
    # ...but the event is still in the measure's history.
    assert any(rule.endswith(":line_item_veto_message_tabled")
               for *_rest, rule in item.history)


def _with_passage(tail):
    """Prefix a minimal legal origin-chamber path so the tail replays from a known state."""
    head = _rows(tail[0]["MeasurePrefix"], tail[0]["MeasureNumber"], [
        ("S", "2025-01-01T00:00:00", "Introduction and first reading."),
        ("S", "2025-01-02T00:00:00", "Third reading. Passed."),
    ])
    rows = head + [dict(r) for r in tail]
    for i, r in enumerate(rows):
        r["MeasureHistoryId"] = 1000 + i
    return rows
