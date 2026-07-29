"""
Rolling baseline.

The sigma arithmetic is checked against a hand-computable series -- if this drifts,
every published deviation figure is wrong.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from pdx1.anomaly import MIN_SAMPLES, BaselineRegistry, RollingBaseline, classify
from pdx1.models import AnomalyTier


def _series(baseline: RollingBaseline, values: list[float], epoch, spacing_days=1):
    for i, v in enumerate(values):
        baseline.add(v, epoch - timedelta(days=len(values) - i) * spacing_days)


# ── Tier classification ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("sigma", "tier"),
    [
        (0.0, AnomalyTier.NONE),
        (0.99, AnomalyTier.NONE),
        (1.0, AnomalyTier.TIER_3),
        (2.0, AnomalyTier.TIER_2),
        (3.0, AnomalyTier.TIER_1),
        (7.5, AnomalyTier.TIER_1),
        (-3.0, AnomalyTier.TIER_1),
    ],
)
def test_classify_bands(sigma, tier):
    assert classify(sigma) is tier


def test_classification_is_symmetric():
    """A drop is as much a deviation as a spike."""
    assert classify(-3.4) is classify(3.4)


# ── Sigma arithmetic ─────────────────────────────────────────────────────────


def test_sigma_against_a_known_series(epoch):
    """
    Values [2, 4, 4, 4, 5, 5, 7, 9]: mean 5, population sd 2.
    An observation of 11 is therefore exactly 3 sigma.
    """
    baseline = RollingBaseline(90)
    _series(baseline, [2, 4, 4, 4, 5, 5, 7, 9], epoch)

    assert baseline.mean == pytest.approx(5.0)
    assert baseline.stddev == pytest.approx(2.0)

    reading = baseline.measure(11.0)
    assert reading.sigma == pytest.approx(3.0)
    assert reading.tier is AnomalyTier.TIER_1
    assert reading.sample_size == 8


def test_reading_carries_the_baseline_not_a_verdict(epoch):
    baseline = RollingBaseline(90)
    _series(baseline, [2, 4, 4, 4, 5, 5, 7, 9], epoch)
    text = baseline.measure(11.0).describe()

    assert "3.0 sigma" in text
    assert "90-day baseline" in text
    # No adjective -- the reader gets the measurement.
    for word in ("unusual", "suspicious", "alarming", "spike"):
        assert word not in text.lower()


def test_below_minimum_samples_reports_none(epoch):
    baseline = RollingBaseline(90)
    _series(baseline, [1.0] * (MIN_SAMPLES - 1), epoch)
    reading = baseline.measure(1000.0)
    assert reading.tier is AnomalyTier.NONE
    assert reading.sigma == 0.0


def test_flat_series_yields_no_deviation(epoch):
    """With zero variance there is no basis to call anything a deviation."""
    baseline = RollingBaseline(90)
    _series(baseline, [7.0] * 20, epoch)
    reading = baseline.measure(9999.0)
    assert reading.baseline_stddev == 0.0
    assert reading.sigma == 0.0
    assert reading.tier is AnomalyTier.NONE


# ── Window behaviour ─────────────────────────────────────────────────────────


def test_window_trims_old_observations(epoch):
    baseline = RollingBaseline(window_days=30)
    baseline.add(100.0, epoch - timedelta(days=200))
    baseline.add(1.0, epoch - timedelta(days=2))
    baseline.add(2.0, epoch - timedelta(days=1))

    assert baseline.sample_size == 2
    assert 100.0 not in baseline.values


def test_out_of_order_observations_are_sorted(epoch):
    baseline = RollingBaseline(90)
    baseline.add(3.0, epoch - timedelta(days=1))
    baseline.add(1.0, epoch - timedelta(days=3))
    baseline.add(2.0, epoch - timedelta(days=2))
    assert baseline.values == [1.0, 2.0, 3.0]


def test_trim_is_relative_to_newest_not_wall_clock(epoch):
    """Replaying historical fixtures must not empty the window."""
    baseline = RollingBaseline(window_days=30)
    _series(baseline, [1.0, 2.0, 3.0, 4.0], epoch - timedelta(days=3650))
    assert baseline.sample_size == 4


def test_observe_measures_then_folds_in(epoch):
    baseline = RollingBaseline(90)
    _series(baseline, [5.0, 5.0, 5.0, 6.0], epoch)
    before = baseline.sample_size

    baseline.observe(20.0, epoch)
    assert baseline.sample_size == before + 1


def test_naive_timestamp_is_rejected():
    from datetime import datetime

    with pytest.raises(ValueError, match="timezone-aware"):
        RollingBaseline(90).add(1.0, datetime(2026, 5, 28))


def test_window_days_must_be_positive():
    with pytest.raises(ValueError, match="positive"):
        RollingBaseline(0)


# ── Registry ─────────────────────────────────────────────────────────────────


def test_registry_keeps_series_independent(epoch):
    registry = BaselineRegistry(90)
    for i in range(10):
        registry.observe("ORESTAR", 5.0 + (i % 2), epoch - timedelta(days=10 - i))
        registry.observe("OLIS", 100.0 + (i % 2), epoch - timedelta(days=10 - i))

    assert registry.keys() == ["OLIS", "ORESTAR"]
    assert registry.baseline("ORESTAR").mean < registry.baseline("OLIS").mean


def test_unknown_key_starts_empty():
    assert BaselineRegistry(90).baseline("NEW").sample_size == 0
