"""
Four-gate filter.

Boundary behaviour is the point of most of these: the thresholds are inclusive, and a
signal sitting exactly on one must pass. An off-by-one here silently changes what the
engine publishes.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from conftest import make_parsed
from pdx1.config import GateConfig
from pdx1.gates import FourGateFilter, composite_score


def test_clean_signal_passes_all_four(epoch):
    gates = FourGateFilter().evaluate(make_parsed(now=epoch), epoch)
    assert gates.passed
    assert gates.failed_gates == []


# ── Credibility ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("credibility", "expected"),
    [(0.49, False), (0.5, True), (0.51, True), (0.0, False), (1.0, True)],
)
def test_credibility_threshold_is_inclusive(epoch, credibility, expected):
    gates = FourGateFilter().evaluate(
        make_parsed(credibility=credibility, now=epoch), epoch
    )
    assert gates.credibility is expected


# ── Volume ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("words", "expected"), [(49, False), (50, True), (51, True), (0, False)]
)
def test_volume_threshold_is_inclusive(epoch, words, expected):
    gates = FourGateFilter().evaluate(make_parsed(words=words, now=epoch), epoch)
    assert gates.volume is expected


# ── Velocity ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("age_hours", "expected"), [(0, True), (47.9, True), (48, True), (48.1, False)]
)
def test_velocity_threshold_is_inclusive(epoch, age_hours, expected):
    gates = FourGateFilter().evaluate(make_parsed(age_hours=age_hours, now=epoch), epoch)
    assert gates.velocity is expected


def test_future_dated_signal_fails_velocity(epoch):
    """A signal published after `now` is a clock or feed fault, not fresh news."""
    gates = FourGateFilter().evaluate(make_parsed(age_hours=-1, now=epoch), epoch)
    assert gates.velocity is False


# ── Novelty ──────────────────────────────────────────────────────────────────


def test_novelty_fails_on_second_sighting(epoch):
    parsed = make_parsed(now=epoch)
    gate_filter = FourGateFilter()

    assert gate_filter.evaluate(parsed, epoch).novelty is True
    gate_filter.register(parsed)
    assert gate_filter.evaluate(parsed, epoch).novelty is False


def test_novelty_ignores_url_and_source(epoch):
    """Same text republished elsewhere is still a duplicate -- the hash is text-only."""
    first = make_parsed(text="identical body " * 40, now=epoch)
    second = make_parsed(text="identical body " * 40, source="OLIS", now=epoch)

    gate_filter = FourGateFilter()
    gate_filter.register(first)
    assert gate_filter.evaluate(second, epoch).novelty is False


def test_novelty_seeded_from_prior_run(epoch):
    """Seeding from the store is how novelty survives across cycles."""
    parsed = make_parsed(now=epoch)
    seeded = FourGateFilter(seen_hashes=[parsed.signal.dedup_hash])
    assert seeded.evaluate(parsed, epoch).novelty is False


# ── No partial credit ────────────────────────────────────────────────────────


def test_one_failed_gate_fails_the_whole_filter(epoch):
    """Three strong gates do not compensate for one failure."""
    gates = FourGateFilter().evaluate(
        make_parsed(credibility=1.0, words=500, age_hours=200, now=epoch), epoch
    )
    assert gates.credibility and gates.volume and gates.novelty
    assert gates.velocity is False
    assert gates.passed is False
    assert gates.failed_gates == ["velocity"]


def test_all_gates_evaluated_even_after_a_failure(epoch):
    """The analyst surface needs every gate's result, so nothing short-circuits."""
    gates = FourGateFilter().evaluate(
        make_parsed(credibility=0.1, words=10, age_hours=999, now=epoch), epoch
    )
    assert gates.failed_gates == ["credibility", "volume", "velocity"]
    assert set(gates.detail) == {"credibility", "volume", "velocity", "novelty"}


def test_custom_thresholds_are_honoured(epoch):
    config = GateConfig(min_credibility=0.9, min_words=10, max_age_hours=1)
    gates = FourGateFilter(config).evaluate(
        make_parsed(credibility=0.8, words=20, age_hours=0.5, now=epoch), epoch
    )
    assert gates.credibility is False
    assert gates.volume is True
    assert gates.velocity is True


# ── Composite score ──────────────────────────────────────────────────────────


def test_failed_gates_score_zero(epoch):
    parsed = make_parsed(age_hours=999, now=epoch)
    gates = FourGateFilter().evaluate(parsed, epoch)
    assert composite_score(parsed, gates, now=epoch) == 0.0


def test_score_is_bounded(epoch):
    parsed = make_parsed(credibility=1.0, words=10_000, age_hours=0, now=epoch)
    gates = FourGateFilter().evaluate(parsed, epoch)
    assert composite_score(parsed, gates, now=epoch) == 1.0


def test_fresher_signal_scores_higher(epoch):
    gate_filter = FourGateFilter()
    fresh = make_parsed(text="alpha " * 60, age_hours=1, now=epoch)
    stale = make_parsed(text="beta " * 60, age_hours=40, now=epoch)

    fresh_score = composite_score(fresh, gate_filter.evaluate(fresh, epoch), now=epoch)
    stale_score = composite_score(stale, gate_filter.evaluate(stale, epoch), now=epoch)
    assert fresh_score > stale_score


def test_higher_credibility_scores_higher(epoch):
    gate_filter = FourGateFilter()
    strong = make_parsed(text="gamma " * 60, credibility=0.95, now=epoch)
    weak = make_parsed(text="delta " * 60, credibility=0.55, now=epoch)

    strong_score = composite_score(strong, gate_filter.evaluate(strong, epoch), now=epoch)
    weak_score = composite_score(weak, gate_filter.evaluate(weak, epoch), now=epoch)
    assert strong_score > weak_score


def test_velocity_uses_the_configured_window(epoch):
    """Age is measured against max_age_hours, not a hardcoded 48."""
    config = GateConfig(max_age_hours=24)
    parsed = make_parsed(age_hours=30, now=epoch)
    assert FourGateFilter(config).evaluate(parsed, epoch).velocity is False
    assert FourGateFilter().evaluate(parsed, epoch).velocity is True
    assert timedelta(hours=24) < timedelta(hours=30) <= timedelta(hours=48)
