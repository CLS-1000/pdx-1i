"""
Dual-write store.

The parity check between JSONL and SQLite is the core assertion: JSONL is ground truth
and the database must always be reconstructible from it.
"""

from __future__ import annotations

import sqlite3

import pytest

from pdx1.models import (
    ConfidenceTier,
    GateResult,
    IntelligenceRecord,
    Outcome,
    Priority,
    SourceType,
)
from pdx1.store import DualWriteStore


def make_record(n: int = 0, run_id: str = "pdx1_test_run") -> IntelligenceRecord:
    from datetime import datetime, timezone

    return IntelligenceRecord(
        record_id=f"rec_test_{n:04d}",
        run_id=run_id,
        source="ORESTAR",
        source_type=SourceType.ORESTAR,
        pattern=f"Test pattern {n}",
        outcome=Outcome.INVESTIGATE,
        priority=Priority.STANDARD,
        confidence=0.5 + (n % 5) / 100,
        tier=ConfidenceTier.HARD_RECORD,
        gates=GateResult(credibility=True, volume=True, velocity=True, novelty=True),
        entity_ids=["pge"],
        signal_id=f"sig_test_{n:04d}",
        dedup_hash=f"hash_test_{n:04d}",
        published_at=datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def store(tmp_path) -> DualWriteStore:
    return DualWriteStore(tmp_path / "signals.jsonl", tmp_path / "pdx1.db")


# ── Writing ──────────────────────────────────────────────────────────────────


def test_write_populates_both_stores(store):
    assert store.write([make_record(i) for i in range(5)]) == 5
    assert store.jsonl_count() == 5
    assert store.count() == 5


def test_jsonl_and_sqlite_stay_in_parity(store):
    for batch in range(3):
        store.write([make_record(batch * 10 + i) for i in range(4)])
    assert store.jsonl_count() == store.count() == 12


def test_write_is_idempotent(store):
    records = [make_record(i) for i in range(3)]
    assert store.write(records) == 3
    assert store.write(records) == 0
    assert store.count() == 3


def test_empty_write_is_a_no_op(store):
    assert store.write([]) == 0
    assert store.jsonl_count() == 0


def test_creates_parent_directories(tmp_path):
    nested = tmp_path / "deep" / "nested"
    store = DualWriteStore(nested / "s.jsonl", nested / "p.db")
    store.write([make_record(1)])
    assert (nested / "s.jsonl").exists()


# ── Reading ──────────────────────────────────────────────────────────────────


def test_round_trip_preserves_the_model(store):
    original = make_record(7)
    store.write([original])

    restored = next(store.iter_jsonl())
    assert restored.record_id == original.record_id
    assert restored.outcome is original.outcome
    assert restored.tier is original.tier
    assert restored.entity_ids == original.entity_ids
    assert restored.gates.passed


def test_query_filters_by_outcome(store):
    from dataclasses import replace  # noqa: F401 - pydantic models use model_copy

    escalated = make_record(1).model_copy(update={"outcome": Outcome.ESCALATE})
    store.write([make_record(2), escalated])

    hits = store.query(outcome="ESCALATE")
    assert len(hits) == 1
    assert hits[0].record_id == escalated.record_id


def test_query_filters_by_source(store):
    press = make_record(3).model_copy(update={"source": "PORTLAND_PRESS"})
    store.write([make_record(4), press])

    assert len(store.query(source="PORTLAND_PRESS")) == 1
    assert len(store.query(source="ORESTAR")) == 1


def test_query_respects_the_limit(store):
    store.write([make_record(i) for i in range(20)])
    assert len(store.query(limit=5)) == 5


def test_has_detects_presence(store):
    store.write([make_record(1)])
    assert store.has("rec_test_0001")
    assert not store.has("rec_nope")


def test_counts_are_zero_on_a_fresh_store(store):
    assert store.jsonl_count() == 0
    assert store.count() == 0
    assert list(store.iter_jsonl()) == []


# ── Recovery ─────────────────────────────────────────────────────────────────


def test_rebuild_restores_sqlite_from_ground_truth(store):
    store.write([make_record(i) for i in range(6)])

    with sqlite3.connect(store.db_path) as conn:
        conn.execute("DELETE FROM intelligence_records")
        conn.commit()
    assert store.count() == 0

    assert store.rebuild_from_jsonl() == 6
    assert store.count() == store.jsonl_count() == 6


def test_rebuild_on_an_empty_store(store):
    assert store.rebuild_from_jsonl() == 0


def test_known_hashes_seed_the_novelty_gate(store):
    store.write([make_record(i) for i in range(3)])
    assert len(store.known_signal_hashes()) == 3


# ── Schema ───────────────────────────────────────────────────────────────────


def test_indexed_columns_are_queryable(store):
    store.write([make_record(i) for i in range(3)])
    with sqlite3.connect(store.db_path) as conn:
        indexes = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
    assert {"idx_records_run", "idx_records_outcome", "idx_records_source"} <= indexes


def test_reopening_an_existing_store_keeps_data(tmp_path):
    paths = (tmp_path / "s.jsonl", tmp_path / "p.db")
    DualWriteStore(*paths).write([make_record(1)])
    assert DualWriteStore(*paths).count() == 1


# ── Entity activity ───────────────────────────────────────────────────────────


def test_entity_counts_tally_across_records(store):
    store.write([make_record(i) for i in range(4)])
    # make_record puts entity_ids=["pge"] on every record.
    assert store.entity_record_counts() == {"pge": 4}


def test_entity_counts_are_empty_on_a_fresh_store(store):
    assert store.entity_record_counts() == {}


def test_entity_counts_handle_records_with_no_entities(store):
    bare = make_record(9).model_copy(update={"entity_ids": []})
    store.write([make_record(1), bare])
    assert store.entity_record_counts() == {"pge": 1}


def test_entity_counts_tally_each_id_on_a_multi_entity_record(store):
    multi = make_record(2).model_copy(update={"entity_ids": ["pge", "metro", "nwn"]})
    store.write([make_record(1), multi])

    counts = store.entity_record_counts()
    assert counts["pge"] == 2
    assert counts["metro"] == 1
    assert counts["nwn"] == 1


def test_records_for_entity_returns_only_matching_records(store):
    other = make_record(5).model_copy(update={"entity_ids": ["metro"]})
    store.write([make_record(1), other])

    assert [r.record_id for r in store.records_for_entity("metro")] == [other.record_id]
    assert [r.record_id for r in store.records_for_entity("pge")] == ["rec_test_0001"]


def test_records_for_entity_matches_ids_exactly(store):
    """A prefix must not match — 'pg' is not 'pge'."""
    store.write([make_record(1)])
    assert store.records_for_entity("pg") == []
    assert store.records_for_entity("pge")


def test_records_for_entity_is_empty_for_an_unknown_id(store):
    store.write([make_record(1)])
    assert store.records_for_entity("no_such_entity") == []


def test_records_for_entity_honours_the_limit(store):
    store.write([make_record(i) for i in range(10)])
    assert len(store.records_for_entity("pge", limit=3)) == 3
