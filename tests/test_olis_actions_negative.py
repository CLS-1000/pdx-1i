"""
False-positive traps in the action text.

A rule that fires on the wrong sentence does not produce a visible error -- it produces
a confident, wrong, publishable statement about a real measure. These are the cases
where the plain English reading and the regex reading come apart.
"""

from __future__ import annotations

import pytest

from pdx1.sources import olis_actions

#: 2025S1. A *motion* failing is not the *measure* failing, and the difference is the
#: difference between "the legislature declined to suspend its own rules" and "the bill
#: died" -- one of which is a fact about procedure and the other a fact about the bill.
MOTION_FAILED = (
    "Motion to suspend the rules to amend the measure on the floor with -3 "
    "amendments failed."
)


def test_failed_motion_is_not_a_failed_measure():
    emits, _gaps = olis_actions.parse_row(MOTION_FAILED)

    assert emits, "the row must still be claimed by a rule, or replay halts on it"
    assert all(e.state is None for e in emits), [
        (e.rule_id, e.state) for e in emits if e.state
    ]
    assert "failed" not in {e.state for e in emits}


def test_failed_motion_moves_no_measure_state():
    """End to end: a history made only of this row leaves the measure where it was."""
    rows = [
        {
            "MeasureHistoryId": 1,
            "MeasurePrefix": "SB",
            "MeasureNumber": 1,
            "Chamber": "S",
            "ActionDate": "2025-09-02T10:00:00",
            "ActionText": "Third reading. Carried by Pham. Passed.",
        },
        {
            "MeasureHistoryId": 2,
            "MeasurePrefix": "SB",
            "MeasureNumber": 1,
            "Chamber": "S",
            "ActionDate": "2025-09-02T11:00:00",
            "ActionText": MOTION_FAILED,
        },
    ]

    item, found = olis_actions.transitions(rows)

    assert item.states["S"] == "passed"
    assert "failed" not in {t.to_state for t in found}
    assert all(t.action_id != 2 for t in found)


def test_the_bare_failed_rule_still_works():
    """The negative case must not be bought by breaking the positive one."""
    emits, _gaps = olis_actions.parse_row("Third reading. Failed.")

    assert "failed" in {e.state for e in emits}


@pytest.mark.parametrize(
    "text",
    [
        "Motion to postpone consideration until March 3 carried.",
        "Motion to refer to Ways and Means carried.",
        "Vote explanation(s) filed by Sollman.",
    ],
)
def test_procedural_motions_do_not_move_the_measure(text):
    emits, _gaps = olis_actions.parse_row(text)

    assert emits
    assert not ({e.state for e in emits} & {"failed", "passed", "adopted"})
