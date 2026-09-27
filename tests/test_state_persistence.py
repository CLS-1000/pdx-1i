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


# ── OLIS emitted transitions ─────────────────────────────────────────────────
#
# The daily cloud task rebuilds SQLite from JSONL on a fresh machine. While the
# emitted-transition table lived only in SQLite, every run started believing no OLIS
# transition had ever been emitted.

_T = [
    ("2025R1", "SB", 875, 101, "S", "vetoed"),
    ("2025R1", "SB", 875, 102, "S", "veto_sustained"),
    ("2026R1", "HB", 4177, 7, "H", "passed"),
]


def test_emitted_transitions_are_written_to_ground_truth(store):
    assert store.record_olis_emitted(_T, "f08744a669f3") == 3
    lines = list(store.iter_olis_emitted())
    assert len(lines) == 3
    assert {line["rules_version"] for line in lines} == {"f08744a669f3"}


def test_recording_the_same_transition_twice_appends_nothing(store):
    store.record_olis_emitted(_T, "aaaaaaaaaaaa")
    assert store.record_olis_emitted(_T, "bbbbbbbbbbbb") == 0
    lines = list(store.iter_olis_emitted())
    assert len(lines) == 3
    assert {line["rules_version"] for line in lines} == {"aaaaaaaaaaaa"}


def test_a_fresh_database_rebuilt_from_jsonl_remembers_what_was_emitted(settings, tmp_path):
    first = DualWriteStore(settings.store_path, settings.db_path)
    first.record_olis_emitted(_T, "f08744a669f3")

    # A new machine: same JSONL files, no database.
    fresh = DualWriteStore(settings.store_path, tmp_path / "fresh.db")
    assert fresh.olis_emitted_count() == 0
    fresh.rebuild_from_jsonl()

    assert fresh.olis_emitted("2025R1") == first.olis_emitted("2025R1")
    assert fresh.olis_emitted_count() == 3


def test_a_store_from_before_the_jsonl_file_is_backfilled_once(settings):
    import sqlite3

    store = DualWriteStore(settings.store_path, settings.db_path)
    # Simulate an older store: rows in SQLite, no ground-truth file.
    with sqlite3.connect(settings.db_path) as conn:
        conn.execute(
            "INSERT INTO olis_emitted VALUES (?,?,?,?,?,?,?,?)",
            ("2025R1", "SB", 1, 1, "S", "introduced", "5d6c1b8bf766", "2026-09-01T00:00:00"),
        )
    store.olis_emitted_path.unlink(missing_ok=True)

    reopened = DualWriteStore(settings.store_path, settings.db_path)
    lines = list(reopened.iter_olis_emitted())
    assert [(line["measure_number"], line["state"]) for line in lines] == [(1, "introduced")]

    # Rebuilding now keeps the row instead of wiping it.
    reopened.rebuild_from_jsonl()
    assert reopened.olis_emitted_count() == 1


def test_emitted_ledger_lives_beside_ground_truth(settings, store):
    assert store.olis_emitted_path.name == "signals_olis_emitted.jsonl"
    assert store.olis_emitted_path.parent == settings.store_path.parent
