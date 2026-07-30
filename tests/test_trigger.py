"""
Publication trigger.

Three independent conditions. Each must fire on its own, and the floor cadence must
keep firing so that a quiet period never becomes a signal in itself.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from pdx1.models import AnomalyTier
from pdx1.trigger import TriggerState


def _published(epoch, **kwargs) -> TriggerState:
    """A trigger that has already published, so the first-run condition is cleared."""
    state = TriggerState(**kwargs)
    state.mark_published(epoch - timedelta(days=1))
    return state


# ── Condition 1: accumulated weight ──────────────────────────────────────────


def test_weight_below_threshold_does_not_fire(epoch):
    state = _published(epoch, weight_threshold=3.0)
    state.add_weight(2.9)
    assert not state.evaluate(epoch)


def test_weight_at_threshold_fires(epoch):
    state = _published(epoch, weight_threshold=3.0)
    state.add_weight(3.0)
    decision = state.evaluate(epoch)
    assert decision.should_publish
    assert any("weight" in r for r in decision.reasons)


def test_weight_accumulates(epoch):
    state = _published(epoch, weight_threshold=3.0)
    for _ in range(4):
        state.add_weight(0.8)
    assert state.accumulated_weight == pytest.approx(3.2)
    assert state.evaluate(epoch)


def test_negative_weight_is_rejected(epoch):
    with pytest.raises(ValueError, match="non-negative"):
        TriggerState().add_weight(-1.0)


# ── Condition 2: anomaly auto-trigger ────────────────────────────────────────


def test_tier_1_anomaly_fires_on_its_own(epoch):
    state = _published(epoch, weight_threshold=100.0)
    state.note_anomaly(AnomalyTier.TIER_1, "rec_abc")

    decision = state.evaluate(epoch)
    assert decision.should_publish
    assert any("TIER_1" in r for r in decision.reasons)
    assert decision.accumulated_weight == 0.0


def test_weaker_tiers_do_not_fire_at_default_setting(epoch):
    state = _published(epoch, weight_threshold=100.0)
    state.note_anomaly(AnomalyTier.TIER_2, "rec_x")
    state.note_anomaly(AnomalyTier.TIER_3, "rec_y")
    state.note_anomaly(AnomalyTier.NONE, "rec_z")
    assert not state.evaluate(epoch)


def test_lowering_the_publish_tier_admits_weaker_readings(epoch):
    state = _published(epoch, weight_threshold=100.0, publish_tier=AnomalyTier.TIER_2)
    state.note_anomaly(AnomalyTier.TIER_2, "rec_x")
    assert state.evaluate(epoch)


def test_stronger_reading_satisfies_a_weaker_setting(epoch):
    state = _published(epoch, weight_threshold=100.0, publish_tier=AnomalyTier.TIER_2)
    state.note_anomaly(AnomalyTier.TIER_1, "rec_x")
    assert state.evaluate(epoch)


# ── Condition 3: floor cadence ───────────────────────────────────────────────


def test_first_run_always_publishes(epoch):
    decision = TriggerState().evaluate(epoch)
    assert decision.should_publish
    assert "no prior publication" in decision.reasons


def test_floor_cadence_fires_with_nothing_accumulated(epoch):
    """Silence must not carry information, so the desk publishes anyway."""
    state = TriggerState(floor_days=7)
    state.mark_published(epoch - timedelta(days=8))

    decision = state.evaluate(epoch)
    assert decision.should_publish
    assert any("floor cadence" in r for r in decision.reasons)


def test_inside_the_floor_window_stays_quiet(epoch):
    state = TriggerState(floor_days=7)
    state.mark_published(epoch - timedelta(days=3))
    assert not state.evaluate(epoch)


def test_floor_boundary_is_inclusive(epoch):
    state = TriggerState(floor_days=7)
    state.mark_published(epoch - timedelta(days=7))
    assert state.evaluate(epoch)


# ── Reset ────────────────────────────────────────────────────────────────────


def test_publishing_resets_weight_and_anomalies(epoch):
    state = TriggerState(weight_threshold=1.0)
    state.add_weight(5.0)
    state.note_anomaly(AnomalyTier.TIER_1, "rec_a")
    assert state.evaluate(epoch)

    state.mark_published(epoch)
    decision = state.evaluate(epoch)
    assert not decision.should_publish
    assert decision.accumulated_weight == 0.0


def test_decision_is_falsy_when_it_does_not_fire(epoch):
    state = _published(epoch, weight_threshold=100.0)
    assert not state.evaluate(epoch)
    assert bool(state.evaluate(epoch)) is False


def test_multiple_conditions_report_multiple_reasons(epoch):
    state = TriggerState(weight_threshold=1.0, floor_days=1)
    state.mark_published(epoch - timedelta(days=30))
    state.add_weight(5.0)
    state.note_anomaly(AnomalyTier.TIER_1, "rec_a")

    assert len(state.evaluate(epoch).reasons) == 3
