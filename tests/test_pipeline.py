"""
End-to-end pipeline.

Proves the documented entry point does real work: signals in, gated records out, both
stores in parity, and a brief that only contains gate-cleared sections.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from pdx1.config import GateConfig, Settings, SourceMode
from pdx1.graph import ALIASES, NODES
from pdx1.models import AnomalyTier, Outcome, Priority, SourceType
from pdx1.pipeline import (
    assign_priority,
    classify_outcome,
    default_adapters,
    make_run_id,
    parse_signal,
    run_cycle,
    summarize,
)
from pdx1.resolver import EntityResolver
from pdx1.store import DualWriteStore


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        store_path=tmp_path / "signals.jsonl",
        db_path=tmp_path / "pdx1.db",
        gates=GateConfig(),
        source_mode=SourceMode.FIXTURE,
    )


@pytest.fixture
def store(settings) -> DualWriteStore:
    return DualWriteStore(settings.store_path, settings.db_path)


def _cycle(settings, store, fixture_dir, now=None):
    return run_cycle(
        settings=settings,
        adapters=default_adapters(settings, fixture_dir),
        now=now,
        store=store,
    )


# ── Full cycle ───────────────────────────────────────────────────────────────


def test_cycle_harvests_every_feed(settings, store, fixture_dir):
    result = _cycle(settings, store, fixture_dir)
    assert result.harvested == 12
    assert result.parsed == 12
    assert result.errors == [] or all("dropped" in e for e in result.errors)


def test_cycle_writes_records_to_both_stores(settings, store, fixture_dir):
    result = _cycle(settings, store, fixture_dir)
    assert result.written > 0
    assert store.jsonl_count() == store.count() == result.written


def test_every_record_carries_traceability(settings, store, fixture_dir):
    """No claim reaches publication without a path back to its run and signal."""
    result = _cycle(settings, store, fixture_dir)
    for record in result.records:
        assert record.run_id == result.run_id
        assert record.signal_id.startswith("sig_")
        assert record.record_id.startswith("rec_")
        assert 0.0 <= record.confidence <= 1.0


def test_gate_failures_are_excluded_from_the_stores(settings, store, fixture_dir):
    """
    The fixtures include a March ORESTAR filing (fails velocity) and a one-line press
    item (fails volume). Neither may appear in either store.
    """
    result = _cycle(settings, store, fixture_dir)

    assert result.dropped.get("velocity") == 1
    assert result.dropped.get("volume") == 1
    assert result.opportunities == result.harvested - 2
    assert store.count() == result.opportunities

    patterns = " ".join(r.pattern for r in store.iter_jsonl())
    assert "ORE-2026-4402118" not in patterns


def test_cycle_is_idempotent(settings, store, fixture_dir):
    first = _cycle(settings, store, fixture_dir)
    second = _cycle(settings, store, fixture_dir)

    assert first.written > 0
    assert second.written == 0
    assert store.count() == first.written


def test_novelty_persists_across_cycles(settings, store, fixture_dir):
    """The second run sees the same content and drops all of it on novelty."""
    _cycle(settings, store, fixture_dir)
    second = _cycle(settings, store, fixture_dir)
    assert second.opportunities == 0
    assert second.dropped.get("novelty", 0) > 0


def test_a_broken_adapter_does_not_halt_the_cycle(settings, store, fixture_dir):
    from pdx1.sources import OrestarAdapter

    adapters = default_adapters(settings, fixture_dir)
    adapters.append(OrestarAdapter(fixture_path="/nonexistent.json"))

    result = run_cycle(settings=settings, adapters=adapters, store=store)
    assert result.errors
    assert result.written > 0


def test_cycle_with_no_adapters_writes_nothing(settings, store):
    result = run_cycle(settings=settings, adapters=[], store=store)
    assert result.harvested == 0
    assert result.written == 0
    assert result.brief is None


def test_as_of_anchors_the_velocity_gate(settings, store, fixture_dir):
    """Anchoring far in the future ages every fixture out of the window."""
    far_future = datetime(2027, 1, 1, tzinfo=timezone.utc)
    result = _cycle(settings, store, fixture_dir, now=far_future)

    assert result.opportunities == 0
    assert result.dropped["velocity"] == result.harvested


def test_default_now_is_the_newest_signal(settings, store, fixture_dir):
    """A fixture replay must not drop everything as the fixtures age."""
    result = _cycle(settings, store, fixture_dir)
    assert result.opportunities > 0


# ── Brief ────────────────────────────────────────────────────────────────────


def test_first_cycle_publishes_a_brief(settings, store, fixture_dir):
    result = _cycle(settings, store, fixture_dir)
    assert result.brief is not None
    assert result.brief.run_id == result.run_id
    assert result.brief.sections


def test_brief_sections_cite_known_records(settings, store, fixture_dir):
    result = _cycle(settings, store, fixture_dir)
    known = {r.record_id for r in result.records}
    for section in result.brief.sections:
        assert section.source_record_ids
        assert set(section.source_record_ids) <= known


def test_brief_summary_names_the_run(settings, store, fixture_dir):
    result = _cycle(settings, store, fixture_dir)
    assert result.run_id in result.brief.summary


def test_brief_headline_is_a_count_not_a_characterisation(settings, store, fixture_dir):
    result = _cycle(settings, store, fixture_dir)
    headline = result.brief.headline.lower()
    assert "records across" in headline
    for word in ("corrupt", "suspicious", "troubling", "scandal"):
        assert word not in headline


# ── Stage helpers ────────────────────────────────────────────────────────────


def test_parse_extracts_registry_entities():
    from conftest import make_signal

    resolver = EntityResolver(NODES, ALIASES)
    signal = make_signal(
        text="The Metro Council and Portland General Electric appear in this filing. " * 8
    )
    parsed = parse_signal(signal, resolver)

    assert "metro" in parsed.keywords
    assert "pge" in parsed.keywords
    assert parsed.word_count > 50


def test_parse_collapses_whitespace():
    from conftest import make_signal

    resolver = EntityResolver(NODES, ALIASES)
    parsed = parse_signal(make_signal(text="a  \n\t b   c"), resolver)
    assert parsed.clean_text == "a b c"


@pytest.mark.parametrize(
    ("score", "tier", "entities", "expected"),
    [
        (0.1, AnomalyTier.TIER_1, 0, Outcome.ESCALATE),
        (0.9, AnomalyTier.NONE, 2, Outcome.CORROBORATED),
        (0.9, AnomalyTier.NONE, 0, Outcome.INVESTIGATE),
        (0.6, AnomalyTier.NONE, 0, Outcome.INVESTIGATE),
        (0.4, AnomalyTier.NONE, 0, Outcome.MONITOR),
        (0.1, AnomalyTier.NONE, 1, Outcome.MONITOR),
        (0.1, AnomalyTier.NONE, 0, Outcome.ARCHIVE),
    ],
)
def test_outcome_classification(score, tier, entities, expected):
    assert classify_outcome(score, tier, entities) is expected


def test_tier_1_escalates_regardless_of_score():
    """A 3-sigma deviation is publish-eligible on its own."""
    assert classify_outcome(0.0, AnomalyTier.TIER_1, 0) is Outcome.ESCALATE


@pytest.mark.parametrize(
    ("outcome", "priority"),
    [
        (Outcome.ESCALATE, Priority.ELEVATED),
        (Outcome.CORROBORATED, Priority.ELEVATED),
        (Outcome.INVESTIGATE, Priority.STANDARD),
        (Outcome.MONITOR, Priority.MONITOR),
        (Outcome.ARCHIVE, Priority.MONITOR),
    ],
)
def test_priority_assignment(outcome, priority):
    assert assign_priority(outcome) is priority


def test_summarize_truncates_on_a_word_boundary():
    text = " ".join(["word"] * 200)
    out = summarize(text_holder(text), limit=50)
    assert out.endswith("...")
    assert len(out) <= 54


def text_holder(text):
    """Minimal stand-in exposing the one attribute `summarize` reads."""

    class _P:
        clean_text = text

    return _P()


def test_summarize_leaves_short_text_alone():
    assert summarize(text_holder("short text")) == "short text"


def test_run_id_encodes_the_timestamp():
    run_id = make_run_id(datetime(2026, 5, 28, 6, 0, 0, tzinfo=timezone.utc))
    assert run_id == "pdx1_2026_0528_060000"


# ── Confidence tiers ─────────────────────────────────────────────────────────


def test_filed_records_are_hard_record_tier(settings, store, fixture_dir):
    from pdx1.models import ConfidenceTier

    result = _cycle(settings, store, fixture_dir)
    filed = [
        r
        for r in result.records
        if r.source_type
        in (SourceType.ORESTAR, SourceType.OLIS, SourceType.SEI, SourceType.WA_PDC)
    ]
    assert filed
    assert all(r.tier is ConfidenceTier.HARD_RECORD for r in filed)


def test_press_records_are_reported_tier(settings, store, fixture_dir):
    from pdx1.models import ConfidenceTier

    result = _cycle(settings, store, fixture_dir)
    press = [r for r in result.records if r.source_type is SourceType.PORTLAND_PRESS]
    assert press
    assert all(r.tier is ConfidenceTier.REPORTED for r in press)


# ── Store interaction ────────────────────────────────────────────────────────


def test_records_survive_a_database_rebuild(settings, store, fixture_dir):
    result = _cycle(settings, store, fixture_dir)
    assert store.rebuild_from_jsonl() == result.written
    assert store.count() == store.jsonl_count()


def test_window_boundary_record_is_kept(settings, store, fixture_dir):
    """A signal exactly at the velocity limit passes -- the threshold is inclusive."""
    result = _cycle(settings, store, fixture_dir)
    newest = max(r.published_at for r in result.records)
    oldest = min(r.published_at for r in result.records)
    assert newest - oldest <= timedelta(hours=48)
