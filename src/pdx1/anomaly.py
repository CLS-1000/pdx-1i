"""
Rolling-baseline anomaly detection (publication path step 03).

A 90-day rolling window per series. Each new observation is measured in standard
deviations from the window's mean. TIER_1 (>= 3 sigma) is the publish-eligible band.

The detector reports the baseline alongside the deviation and never labels an
observation with an adjective. Published copy cites "3.1 sigma above a 90-day baseline
of 4.2", not "unusually high" -- the reader gets the measurement and draws their own
conclusion.
"""

from __future__ import annotations

import statistics
from bisect import insort
from collections.abc import Iterable
from datetime import datetime, timedelta

from .models import AnomalyReading, AnomalyTier, utcnow

# Sigma floor for each band. Checked highest-first.
_TIER_THRESHOLDS: tuple[tuple[float, AnomalyTier], ...] = (
    (3.0, AnomalyTier.TIER_1),
    (2.0, AnomalyTier.TIER_2),
    (1.0, AnomalyTier.TIER_3),
)

# Below this many points the standard deviation is not meaningful, so the detector
# reports NONE rather than manufacturing a sigma from two observations.
MIN_SAMPLES = 3


def classify(sigma: float) -> AnomalyTier:
    """Map an absolute sigma deviation to its tier."""
    magnitude = abs(sigma)
    for threshold, tier in _TIER_THRESHOLDS:
        if magnitude >= threshold:
            return tier
    return AnomalyTier.NONE


class RollingBaseline:
    """
    A rolling window of timestamped observations for one series.

    Observations may arrive out of order; the window is kept sorted by timestamp and
    trimmed relative to the newest observation seen, not to wall-clock now. That keeps
    replays over historical fixtures deterministic.
    """

    def __init__(self, window_days: int = 90) -> None:
        if window_days <= 0:
            raise ValueError("window_days must be positive")
        self.window_days = window_days
        self._points: list[tuple[datetime, float]] = []

    def add(self, value: float, at: datetime | None = None) -> None:
        """Add one observation and trim anything that fell out of the window."""
        at = at or utcnow()
        if at.tzinfo is None:
            raise ValueError("observation timestamp must be timezone-aware")
        insort(self._points, (at, value))
        self._trim()

    def extend(self, points: Iterable[tuple[datetime, float]]) -> None:
        for at, value in points:
            self.add(value, at)

    def _trim(self) -> None:
        if not self._points:
            return
        newest = self._points[-1][0]
        cutoff = newest - timedelta(days=self.window_days)
        self._points = [p for p in self._points if p[0] >= cutoff]

    @property
    def values(self) -> list[float]:
        return [v for _, v in self._points]

    @property
    def sample_size(self) -> int:
        return len(self._points)

    @property
    def mean(self) -> float:
        return statistics.fmean(self.values) if self._points else 0.0

    @property
    def stddev(self) -> float:
        """Population standard deviation of the window."""
        if len(self._points) < 2:
            return 0.0
        return statistics.pstdev(self.values)

    def measure(self, value: float) -> AnomalyReading:
        """
        Measure an observation against the current window without adding it.

        A flat window (stddev 0) yields sigma 0 -- with no variance there is no basis to
        call anything a deviation, however far the observation sits from the mean.
        """
        n = self.sample_size
        mean = self.mean
        sd = self.stddev

        if n < MIN_SAMPLES or sd == 0.0:
            sigma = 0.0
            tier = AnomalyTier.NONE
        else:
            sigma = (value - mean) / sd
            tier = classify(sigma)

        return AnomalyReading(
            observed=value,
            baseline_mean=mean,
            baseline_stddev=sd,
            sigma=sigma,
            tier=tier,
            window_days=self.window_days,
            sample_size=n,
        )

    def observe(self, value: float, at: datetime | None = None) -> AnomalyReading:
        """Measure an observation against the window, then fold it in."""
        reading = self.measure(value)
        self.add(value, at)
        return reading


class BaselineRegistry:
    """Keeps one RollingBaseline per series key (typically the source feed)."""

    def __init__(self, window_days: int = 90) -> None:
        self.window_days = window_days
        self._series: dict[str, RollingBaseline] = {}

    def baseline(self, key: str) -> RollingBaseline:
        if key not in self._series:
            self._series[key] = RollingBaseline(self.window_days)
        return self._series[key]

    def observe(
        self, key: str, value: float, at: datetime | None = None
    ) -> AnomalyReading:
        return self.baseline(key).observe(value, at)

    def keys(self) -> list[str]:
        return sorted(self._series)
