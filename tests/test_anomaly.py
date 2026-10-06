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


# ── Dispersion floors ────────────────────────────────────────────────────────
#
# A window with variance can still be too flat to divide by. Without a floor, a
# difference at rounding scale becomes a large sigma and publishes as a finding.


def test_the_live_wa_pdc_case_that_published_a_false_tier_1(epoch):
    """
    Regression: the exact series that put a 3.7-sigma TIER_1 in a brief headline.

    Live WA PDC records are near-identical hard records filed the same day, so their
    composite scores clustered at mean 0.597 with sd 0.0016. An observation of 0.603
    -- six thousandths away -- divided out to 3.67 sigma, escalated, and published as
    "1 at elevated disposition". The line read "(sd 0.00, n=3) -- 3.7 sigma", which
    cannot show the arithmetic for its own claim.
    """
    baseline = RollingBaseline(90)
    _series(baseline, [0.595, 0.597, 0.599] * 4, epoch)

    reading = baseline.measure(0.603)

    assert reading.baseline_stddev < 0.005
    assert reading.tier is AnomalyTier.NONE
    assert reading.sigma == 0.0
    # The suppressed reading still reports the series it suppressed.
    assert reading.baseline_mean == pytest.approx(0.597)
    assert reading.sample_size == 12


def test_a_suppressed_reading_never_prints_sd_zero_beside_a_sigma(epoch):
    """
    The published line must be able to display the basis of its own claim.

    `describe()` renders sd at two decimals, so any sd under 0.005 prints as 0.00.
    Whenever that happens the sigma must be 0.0, or the brief states a deviation it
    cannot substantiate on its face.
    """
    baseline = RollingBaseline(90)
    _series(baseline, [0.595, 0.597, 0.599] * 4, epoch)
    text = baseline.measure(0.603).describe()

    assert "sd 0.00" in text
    assert "0.0 sigma" in text


@pytest.mark.parametrize(
    ("values", "observed", "tier"),
    [
        # Counts: sd 2.0 on mean 5.0 is 40% dispersion -- measurable.
        ([2, 4, 4, 4, 5, 5, 7, 9], 11.0, AnomalyTier.TIER_1),
        # Scores clustered at 1% dispersion clear both floors.
        ([0.50, 0.51, 0.52, 0.53, 0.54, 0.55, 0.56, 0.57], 0.75, AnomalyTier.TIER_1),
        # Same shape at a hundredth the spread: absolute floor rejects it.
        ([0.500, 0.501, 0.502, 0.503, 0.504, 0.505, 0.506, 0.507], 0.75, AnomalyTier.NONE),
    ],
)
def test_floors_separate_measurable_series_from_constant_ones(
    epoch, values, observed, tier
):
    baseline = RollingBaseline(90)
    _series(baseline, list(values), epoch)
    assert baseline.measure(observed).tier is tier


def test_a_wide_series_at_a_high_level_is_still_measurable(epoch):
    """The relative floor must not reject a volume series just because it is large."""
    baseline = RollingBaseline(90)
    _series(baseline, [30, 35, 40, 45, 45, 50, 55, 60], epoch)

    reading = baseline.measure(120.0)
    assert reading.tier is AnomalyTier.TIER_1
    assert reading.baseline_stddev > 0.005


def test_a_narrow_series_at_a_high_level_is_not(epoch):
    """
    Mean 1000 with sd 0.01 prints a legible sd and is still effectively constant.

    The absolute floor alone would admit this -- 0.01 exceeds 0.005 -- and a 0.05
    difference would read as 5 sigma. The relative floor is what rejects it.
    """
    baseline = RollingBaseline(90)
    _series(baseline, [999.99, 1000.0, 1000.01] * 4, epoch)

    reading = baseline.measure(1000.05)
    assert reading.baseline_stddev > 0.005
    assert reading.tier is AnomalyTier.NONE


def test_a_series_centred_on_zero_is_judged_on_the_absolute_floor(epoch):
    """
    Day-over-day deltas sit around zero, where the relative floor collapses.

    Such a series is legitimately measurable, so only the absolute floor applies.
    """
    baseline = RollingBaseline(90)
    _series(baseline, [-3, -2, -1, 0, 0, 1, 2, 3], epoch)

    assert baseline.mean == pytest.approx(0.0)
    assert baseline.measure(9.0).tier is AnomalyTier.TIER_1
