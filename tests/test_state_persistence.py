"""
State that has to outlive the process: rolling baselines, the publication trigger,
and the run ledger.

The scheduler starts a fresh process -- or, on a hosted runner, a fresh machine --
every morning. Anything held only in memory resets to empty, so a "90-day baseline"
built in-process measures against the same run, and a trigger built in-process
believes nothing has ever been published.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from pdx1 import pipeline
from pdx1.anomaly import BaselineRegistry
from pdx1.config import GateConfig, Settings
from pdx1.pipeline import default_adapters, run_cycle, seed_state
from pdx1.store import DualWriteStore
from pdx1.trigger import TriggerState


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        store_path=tmp_path / "signals.jsonl",
        db_path=tmp_path / "pdx1.db",
        gates=GateConfig(),
    )


@pytest.fixture
def store(settings) -> DualWriteStore:
    return DualWriteStore(settings.store_path, settings.db_path)


def _cycle(settings, store, fixture_dir, **kwargs):
    return run_cycle(
        settings=settings,
        adapters=default_adapters(settings, fixture_dir),
        store=store,
        **kwargs,
    )


def _newest(store):
    return max(r.published_at for r in store.iter_jsonl())


# ── Baselines ────────────────────────────────────────────────────────────────


def test_baselines_are_seeded_from_stored_records(settings, store, fixture_dir):
    first = _cycle(settings, store, fixture_dir)
    assert first.written > 0

    baselines = BaselineRegistry(90)
    seeded = seed_state(store, baselines, None, 90, _newest(store))

    assert seeded == first.written
    by_source: dict[str, int] = {}
    for record in store.iter_jsonl():
        by_source[record.source] = by_source.get(record.source, 0) + 1
    for source, count in by_source.items():
        assert baselines.baseline(source).sample_size == count


def test_second_cycle_reports_how_much_history_it_measured_against(
    settings, store, fixture_dir
):
    first = _cycle(settings, store, fixture_dir)
    second = _cycle(settings, store, fixture_dir)
    assert first.baseline_seeded == 0
    assert second.baseline_seeded == first.written


def test_records_outside_the_window_do_not_seed(settings, store, fixture_dir):
    _cycle(settings, store, fixture_dir)
    far_future = _newest(store) + timedelta(days=91)
    assert seed_state(store, BaselineRegistry(90), None, 90, far_future) == 0


def test_records_dated_after_now_do_not_seed(settings, store, fixture_dir):
    _cycle(settings, store, fixture_dir)
    before_all = min(r.published_at for r in store.iter_jsonl()) - timedelta(seconds=1)
    assert seed_state(store, BaselineRegistry(90), None, 90, before_all) == 0


# ── Trigger ──────────────────────────────────────────────────────────────────


def _trigger() -> TriggerState:
    return TriggerState(weight_threshold=3.0, floor_days=7)


def test_trigger_remembers_the_last_publication(settings, store, fixture_dir):
    first = _cycle(settings, store, fixture_dir)
    assert first.brief is not None

    trigger = _trigger()
    seed_state(store, BaselineRegistry(90), trigger, 90, _newest(store))

    assert trigger.last_published_at == first.brief.produced_at
    produced = first.brief.produced_at
    assert "no prior publication" not in trigger.evaluate(produced + timedelta(days=1)).reasons


def test_floor_fires_on_day_seven_not_day_one(settings, store, fixture_dir):
    first = _cycle(settings, store, fixture_dir)
    produced = first.brief.produced_at

    trigger = _trigger()
    seed_state(store, BaselineRegistry(90), trigger, 90, _newest(store))

    assert not trigger.evaluate(produced + timedelta(days=1)).should_publish
    decision = trigger.evaluate(produced + timedelta(days=7))
    assert decision.should_publish
    assert any("floor cadence" in r for r in decision.reasons)


def test_a_fresh_store_still_publishes_its_first_brief(settings, store, fixture_dir):
    result = _cycle(settings, store, fixture_dir)
    assert result.brief is not None
    assert "no prior publication" in result.trigger_reasons


def test_caller_supplied_trigger_is_not_overwritten(settings, store, fixture_dir):
    _cycle(settings, store, fixture_dir)
    mine = _trigger()
    _cycle(settings, store, fixture_dir, trigger=mine)
    assert mine.last_published_at is None


# ── Run ledger ───────────────────────────────────────────────────────────────


def test_every_cycle_writes_one_ledger_line(settings, store, fixture_dir):
    first = _cycle(settings, store, fixture_dir)
    _cycle(settings, store, fixture_dir)

    lines = list(store.iter_runs())
    assert len(lines) == 2
    assert lines[0]["run_id"] == first.run_id
    assert lines[0]["status"] == "ok"
    assert lines[0]["brief_id"] == first.brief.brief_id
    assert lines[0]["brief_sections"] == len(first.brief.sections)
    assert lines[0]["no_brief_reason"] is None
    assert lines[0]["live"] is False


def test_quiet_day_is_distinguishable_from_a_published_one(settings, store, fixture_dir):
    _cycle(settings, store, fixture_dir)
    _cycle(settings, store, fixture_dir)  # everything drops on novelty

    quiet = list(store.iter_runs())[-1]
    assert quiet["status"] == "ok"
    assert quiet["brief_id"] is None
    assert quiet["brief_sections"] == 0
    assert quiet["no_brief_reason"] == "no records cleared the gates"
    assert quiet["dropped"].get("novelty", 0) > 0


def test_all_feeds_dead_is_a_failed_run(settings, store):
    from pdx1.sources import OrestarAdapter

    run_cycle(
        settings=settings,
        adapters=[OrestarAdapter(fixture_path="/nonexistent.json")],
        store=store,
    )
    line = list(store.iter_runs())[-1]
    assert line["status"] == "fail"
    assert line["failed_adapters"] == ["ORESTAR"]
    assert "error" in line["adapters"][0]


def test_one_dead_feed_is_a_partial_run(settings, store, fixture_dir):
    from pdx1.sources import OrestarAdapter

    adapters = default_adapters(settings, fixture_dir)
    adapters.append(OrestarAdapter(fixture_path="/nonexistent.json"))
    run_cycle(settings=settings, adapters=adapters, store=store)
    assert list(store.iter_runs())[-1]["status"] == "partial"


def test_a_cycle_that_raises_still_leaves_a_line(settings, store, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(pipeline, "_run_cycle", boom)
    with pytest.raises(RuntimeError):
        run_cycle(settings=settings, adapters=[], store=store)

    line = list(store.iter_runs())[-1]
    assert line["status"] == "fail"
    assert line["brief_id"] is None
    assert "disk full" in line["no_brief_reason"]


def test_ledger_lives_beside_ground_truth(settings, store):
    assert store.runs_path.parent == settings.store_path.parent
    assert store.runs_path.name == "signals_runs.jsonl"
