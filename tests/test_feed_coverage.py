"""
A brief must disclose what the run did not reach.

The failure this guards against is the quiet one. When a feed dies, the records that
survive look exactly like a healthy cycle's -- correct, traceable, fully gated. Nothing
in them says a fifth of the sources never answered. A reader cannot infer an absence
from records that do not exist, so the run has to state it, and state it where a reader
who stops after one sentence still sees it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pdx1.config import Settings
from pdx1.models import FeedCoverage
from pdx1.pipeline import default_adapters, run_cycle


@pytest.fixture
def settings(tmp_path):
    return Settings(
        store_path=tmp_path / "signals.jsonl",
        db_path=tmp_path / "pdx1.db",
    )


def _kill(adapter):
    """Point an adapter at a source that will not answer."""
    adapter.fixture_path = Path("/nonexistent/feed.json")
    return adapter


# ── The measurement ──────────────────────────────────────────────────────────


def test_complete_coverage_is_reported_as_complete():
    coverage = FeedCoverage(attempted=5, returned=5)
    assert coverage.complete
    assert "5 of 5" in coverage.describe()


def test_a_failed_feed_is_named():
    coverage = FeedCoverage(attempted=5, returned=4, failed=["ORESTAR"])
    assert not coverage.complete
    assert "4 of 5" in coverage.describe()
    assert "ORESTAR" in coverage.describe()


def test_an_empty_feed_is_named_separately_from_a_failed_one():
    """
    They need different responses, so they are reported differently.

    A feed that errored is broken. A feed that answered with nothing may have had
    nothing to file -- or may have changed shape under the parser. The run reports the
    measurement and declines to say which.
    """
    coverage = FeedCoverage(attempted=5, returned=4, empty=["SEI"])
    text = coverage.describe()
    assert "Returned no items: SEI." in text
    assert "Did not return" not in text


def test_a_cached_feed_is_disclosed_as_stale():
    coverage = FeedCoverage(attempted=5, returned=5, stale=["WA_PDC"])
    assert "last-good cache" in coverage.describe()
    assert "WA_PDC" in coverage.describe()


def test_a_stale_feed_is_not_described_as_a_clean_cycle():
    """Full counts plus a cache fallback is not 'nothing was missing'."""
    coverage = FeedCoverage(attempted=5, returned=5, stale=["WA_PDC"])
    assert "No feed was missing" not in coverage.describe()


def test_no_attempts_says_so_rather_than_claiming_completeness():
    coverage = FeedCoverage()
    assert not coverage.complete
    assert "No feed was attempted" in coverage.describe()


def test_an_empty_feed_does_not_count_as_returned():
    """
    Guards the arithmetic that would let a hollow cycle call itself complete.

    If empty adapters counted as covered, a run with one live feed and four empty ones
    would describe itself as 5 of 5 and read as a healthy morning.
    """
    coverage = FeedCoverage(
        attempted=5, returned=1, empty=["OLIS", "SEI", "WA_PDC", "PORTLAND_PRESS"]
    )
    assert not coverage.complete
    assert "1 of 5" in coverage.describe()


# ── End to end ───────────────────────────────────────────────────────────────


def test_full_cycle_reports_complete_coverage(settings):
    result = run_cycle(settings=settings, adapters=default_adapters(settings))

    assert result.coverage.attempted == 5
    assert result.coverage.returned == 5
    assert result.coverage.complete
    assert result.brief is not None
    assert "5 of 5 feeds" in result.brief.summary


def test_a_dead_feed_does_not_abort_the_cycle(settings):
    """Rule 6: a dead feed must never halt a cycle."""
    adapters = default_adapters(settings)
    _kill(adapters[0])

    result = run_cycle(settings=settings, adapters=adapters)

    assert result.brief is not None, "the cycle must still publish"
    assert result.written > 0, "the surviving feeds must still be written"


def test_a_brief_from_four_of_five_feeds_says_so_on_its_face(settings):
    """The acceptance criterion, stated as a test."""
    adapters = default_adapters(settings)
    _kill(adapters[0])

    result = run_cycle(settings=settings, adapters=adapters)

    assert result.brief is not None
    summary = result.brief.summary
    assert "4 of 5 feeds" in summary
    assert "ORESTAR" in summary
    # Leading, not buried: a reader who stops after the first sentence still sees it.
    assert summary.startswith("Generated from 4 of 5 feeds.")


def test_the_missing_feed_is_named_in_stored_coverage(settings):
    adapters = default_adapters(settings)
    _kill(adapters[0])

    result = run_cycle(settings=settings, adapters=adapters)

    assert result.brief.coverage is not None
    assert result.brief.coverage.failed == ["ORESTAR"]
    assert result.brief.coverage.returned == 4


def test_adapter_failure_is_recorded_against_the_run(settings):
    """Rule 4: every record traces to the run that produced it -- failures included."""
    adapters = default_adapters(settings)
    _kill(adapters[0])

    result = run_cycle(settings=settings, adapters=adapters)

    assert len(result.adapters) == 5
    failed = [o for o in result.adapters if not o.ok]
    assert len(failed) == 1
    assert failed[0].source == "ORESTAR"
    assert failed[0].run_id == result.run_id
    assert failed[0].errors


def test_every_adapter_gets_an_outcome_bound_to_the_run(settings):
    result = run_cycle(settings=settings, adapters=default_adapters(settings))
    assert len(result.adapters) == 5
    assert all(o.run_id == result.run_id for o in result.adapters)


def test_coverage_survives_the_round_trip_to_the_store(settings):
    """Coverage is ground truth about the brief, not a rendering detail."""
    from pdx1.store import DualWriteStore

    adapters = default_adapters(settings)
    _kill(adapters[0])
    run_cycle(settings=settings, adapters=adapters)

    reopened = DualWriteStore(settings.store_path, settings.db_path)
    stored = reopened.latest_brief()

    assert stored is not None
    assert stored.coverage is not None
    assert stored.coverage.failed == ["ORESTAR"]
    assert "4 of 5 feeds" in stored.summary


def test_a_brief_without_coverage_still_loads():
    """
    Additive field, defaulted.

    Briefs written before coverage existed are still ground truth and must still read
    back. They report None -- honest about them, since the run that produced them did
    not measure it.
    """
    from pdx1.models import Brief

    brief = Brief(
        brief_id="brief_x",
        run_id="pdx1_2026_0101_000000",
        date="2026-01-01",
        headline="h",
        summary="s",
        confidence=0.5,
    )
    assert brief.coverage is None
