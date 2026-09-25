"""
Which instant the velocity gate measures against.

Fixture replay anchors to the newest harvested signal, because the checked-in payloads
carry fixed dates and wall-clock time would age every one of them out. Live runs must
use the real clock instead, and the reason is not tidiness: anchoring a live run to its
own newest signal hands the window to whichever record has the furthest-future
timestamp.

That is a measured failure, not a hypothetical. One WA PDC record dated nine days ahead
moved the anchor and the gate dropped 51,028 of 51,029 harvested signals. The cycle
reported success and published a one-section brief. Nothing in the log said why.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from pdx1.config import Settings
from pdx1.models import Signal, SourceType
from pdx1.pipeline import _anchor


def _signal(published_at: datetime) -> Signal:
    return Signal(
        source="TEST",
        source_type=SourceType.ORESTAR,
        text="x" * 80,
        published_at=published_at,
    )


def test_fixture_mode_anchors_to_the_newest_signal():
    """Replay must stay reproducible: wall clock would age every fixture out."""
    newest = datetime(2026, 5, 27, 22, 15, tzinfo=timezone.utc)
    signals = [_signal(newest - timedelta(days=3)), _signal(newest)]

    assert _anchor(Settings(), signals) == newest


def test_live_mode_uses_the_real_clock_not_the_newest_signal():
    live = replace(Settings(), live_fetch=True)
    stale = [_signal(datetime(2020, 1, 1, tzinfo=timezone.utc))]

    anchor = _anchor(live, stale)

    assert (datetime.now(timezone.utc) - anchor).total_seconds() < 60


def test_a_future_dated_record_cannot_move_a_live_anchor():
    """
    The regression, stated as the thing that actually happened.

    A single record dated nine days ahead used to become the clock, putting every
    genuinely recent signal outside the 48-hour window.
    """
    live = replace(Settings(), live_fetch=True)
    now = datetime.now(timezone.utc)
    poisoned = [_signal(now - timedelta(hours=2)), _signal(now + timedelta(days=9))]

    anchor = _anchor(live, poisoned)

    assert anchor < now + timedelta(minutes=1), "a future record must not become the clock"


def test_a_future_dated_record_still_moves_a_fixture_anchor():
    """
    Fixture mode is unchanged on purpose.

    Replay reproducibility depends on this, and a checked-in payload is something a
    person wrote rather than something a public agency emitted.
    """
    now = datetime.now(timezone.utc)
    future = now + timedelta(days=9)

    assert _anchor(Settings(), [_signal(now), _signal(future)]) == future


def test_live_mode_with_no_signals_still_returns_a_usable_clock():
    """Every adapter failing must not leave the gate without an instant to measure."""
    live = replace(Settings(), live_fetch=True)

    assert (datetime.now(timezone.utc) - _anchor(live, [])).total_seconds() < 60
