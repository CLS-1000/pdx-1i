"""
Signal-gated publication trigger (publication path step 04).

Publication fires on any of three independent conditions:

    1. Accumulated weight crosses a threshold  -- enough has happened to be worth saying
    2. A TIER_1 anomaly is observed            -- a 3-sigma reading publishes on its own
    3. The floor cadence elapses               -- the desk does not go silent indefinitely

The floor exists so the absence of a brief carries no information. If publication only
happened when something was wrong, a quiet week would itself become a signal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .models import AnomalyTier, utcnow


@dataclass(frozen=True)
class TriggerDecision:
    """Why the trigger did or did not fire."""

    should_publish: bool
    reasons: tuple[str, ...] = ()
    accumulated_weight: float = 0.0

    def __bool__(self) -> bool:
        return self.should_publish


@dataclass
class TriggerState:
    """
    Accumulates weight between publications and decides when to assemble a brief.

    State is reset on publication, so weight measures what has accrued since the last
    brief rather than all time.
    """

    weight_threshold: float = 3.0
    floor_days: int = 7
    publish_tier: AnomalyTier = AnomalyTier.TIER_1

    accumulated_weight: float = 0.0
    last_published_at: datetime | None = None
    _tier_hits: list[str] = field(default_factory=list)

    # Tiers that satisfy the auto-trigger, ordered strongest first.
    _TIER_ORDER = (AnomalyTier.TIER_1, AnomalyTier.TIER_2, AnomalyTier.TIER_3)

    def add_weight(self, amount: float) -> None:
        """Accumulate scored weight from a record."""
        if amount < 0:
            raise ValueError("weight contribution must be non-negative")
        self.accumulated_weight += amount

    def note_anomaly(self, tier: AnomalyTier, label: str = "") -> None:
        """Record an anomaly tier that may auto-trigger publication."""
        if self._satisfies_publish_tier(tier):
            self._tier_hits.append(label or tier.value)

    def _satisfies_publish_tier(self, tier: AnomalyTier) -> bool:
        """True when `tier` is at least as strong as the configured publish tier."""
        if tier is AnomalyTier.NONE:
            return False
        if self.publish_tier is AnomalyTier.NONE:
            return True
        return self._TIER_ORDER.index(tier) <= self._TIER_ORDER.index(self.publish_tier)

    def evaluate(self, now: datetime | None = None) -> TriggerDecision:
        """Check all three conditions. Any one of them fires publication."""
        now = now or utcnow()
        reasons: list[str] = []

        if self.accumulated_weight >= self.weight_threshold:
            reasons.append(
                f"weight {self.accumulated_weight:.2f} >= threshold "
                f"{self.weight_threshold:.2f}"
            )

        if self._tier_hits:
            joined = ", ".join(self._tier_hits)
            reasons.append(f"{self.publish_tier.value} anomaly: {joined}")

        if self.last_published_at is None:
            reasons.append("no prior publication")
        elif now - self.last_published_at >= timedelta(days=self.floor_days):
            elapsed = (now - self.last_published_at).days
            reasons.append(f"floor cadence: {elapsed}d since last brief")

        return TriggerDecision(
            should_publish=bool(reasons),
            reasons=tuple(reasons),
            accumulated_weight=self.accumulated_weight,
        )

    def mark_published(self, at: datetime | None = None) -> None:
        """Reset accumulated state after a brief is assembled."""
        self.last_published_at = at or utcnow()
        self.accumulated_weight = 0.0
        self._tier_hits.clear()
