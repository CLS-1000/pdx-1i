"""
Replay contract with proc_track.

The mapping module in ``pdx1.sources.olis_actions`` is a straight copy of the
tested harness in ``~/proc_track``. That harness has SB976 as one of its
reference measures -- it went through the executive path in one chamber and
died to a sustained veto in the other, so a bill's replay landing anywhere
else is a regression on either the mapping or the schema. This test asserts
the expected terminal states for SB976; it is skipped when the sibling repo is
not on disk, so CI stays independent of a sandbox with proc_track alongside.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pdx1.sources.olis_actions import replay

TRAJECTORY = Path.home() / "proc_track" / "data" / "sb976_trajectory.json"


def _to_odata_rows(actions: list[dict]) -> list[dict]:
    """
    Map the trajectory shape (snake_case) to what ``replay`` reads (PascalCase).

    The proc_track corpus was recorded in the API shape; the trajectory fixture
    was normalised for its own harness. Both survive; this glue is one place.
    """
    return [
        {
            "ActionText": row["action_text"],
            "Chamber": row["chamber"],
            "ActionDate": row["occurred_at"],
            "MeasureHistoryId": row.get("history_id"),
        }
        for row in actions
    ]


@pytest.mark.skipif(not TRAJECTORY.exists(), reason="proc_track trajectory fixture not present")
def test_sb976_replays_to_reference_terminal_states():
    """
    SB976 (2025R1): Senate had its veto sustained; House signed the bill out.

    This is the canonical replay proc_track uses to check the schema. If the
    terminal states drift, either the mapping ate a transition or the flow
    graph accepted one it should not have.
    """
    with TRAJECTORY.open("r", encoding="utf-8") as fh:
        trajectory = json.load(fh)

    rows = _to_odata_rows(trajectory["actions"])
    item, halt, _uncovered = replay("SB976", rows)

    assert halt is None, halt
    assert item.states == {"S": "veto_sustained", "H": "signed_by_presiding"}
