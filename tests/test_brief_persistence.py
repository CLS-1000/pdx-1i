"""
Brief persistence.

Briefs are the product. Before this existed, an assembled brief lived only in the API
process's memory, which meant:

  - a brief assembled by the CLI or by `pdx1-scheduler` was invisible to the API and
    discarded when that process exited;
  - restarting the API lost the brief while the records survived;
  - re-running a cycle could not regenerate it, because the novelty gate correctly drops
    signals already in the store -- so a second cycle produces no records and no brief.

The net effect was that `GET /brief` returned 404 permanently on any store past its
first cycle. The regression tests below pin each link in that chain.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from pdx1.config import GateConfig, Settings, SourceMode
from pdx1.models import Brief, BriefSection
from pdx1.pipeline import default_adapters, run_cycle
from pdx1.store import DualWriteStore

PRODUCED = datetime(2026, 5, 28, 6, 0, tzinfo=timezone.utc)


def make_brief(n: int = 0, produced: datetime | None = None) -> Brief:
    return Brief(
        brief_id=f"brief_test_{n:03d}",
        run_id=f"pdx1_test_run_{n:03d}",
        date="2026-05-28",
        headline=f"Headline {n}",
        summary=f"Summary {n}. Every line traces to run pdx1_test_run_{n:03d}.",
        sections=[
            BriefSection(
                title="Elevated",
                body=f"- [CORROBORATED] Pattern {n}.",
                source_record_ids=[f"rec_{n:03d}"],
            )
        ],
        confidence=0.5 + n / 100,
        sources=["ORESTAR"],
        produced_at=(produced or PRODUCED) + timedelta(minutes=n),
    )


@pytest.fixture
def store(tmp_path) -> DualWriteStore:
    return DualWriteStore(tmp_path / "signals.jsonl", tmp_path / "pdx1.db")


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        store_path=tmp_path / "signals.jsonl",
        db_path=tmp_path / "pdx1.db",
        gates=GateConfig(),
        source_mode=SourceMode.FIXTURE,
    )


# ── Store round-trip ─────────────────────────────────────────────────────────


def test_write_and_read_back(store):
    original = make_brief(1)
    assert store.write_brief(original) is True

    restored = store.latest_brief()
    assert restored is not None
    assert restored.brief_id == original.brief_id
    assert restored.headline == original.headline
    assert restored.sections[0].source_record_ids == ["rec_001"]


def test_latest_brief_is_none_on_a_fresh_store(store):
    assert store.latest_brief() is None
    assert store.brief_count() == 0
    assert store.briefs() == []


def test_write_is_idempotent(store):
    brief = make_brief(2)
    assert store.write_brief(brief) is True
    assert store.write_brief(brief) is False
    assert store.brief_count() == 1
    assert store.briefs_jsonl_count() == 1


def test_jsonl_and_sqlite_stay_in_parity(store):
    for i in range(5):
        store.write_brief(make_brief(i))
    assert store.briefs_jsonl_count() == store.brief_count() == 5


def test_latest_is_the_most_recently_produced(store):
    for i in range(4):
        store.write_brief(make_brief(i))
    # make_brief staggers produced_at by n minutes, so 3 is newest.
    assert store.latest_brief().brief_id == "brief_test_003"


def test_latest_breaks_ties_by_write_order(store):
    """Two briefs produced in the same second resolve to the one written later."""
    same = PRODUCED
    first = make_brief(0, produced=same)
    second = make_brief(0, produced=same).model_copy(
        update={"brief_id": "brief_test_zzz"}
    )
    store.write_brief(first)
    store.write_brief(second)
    assert store.latest_brief().brief_id == "brief_test_zzz"


def test_fetch_one_by_id(store):
    store.write_brief(make_brief(7))
    assert store.brief("brief_test_007").headline == "Headline 7"
    assert store.brief("brief_nope") is None


def test_archive_is_newest_first_and_paginates(store):
    for i in range(6):
        store.write_brief(make_brief(i))

    archive = store.briefs(limit=3)
    assert [b.brief_id for b in archive] == [
        "brief_test_005",
        "brief_test_004",
        "brief_test_003",
    ]
    assert [b.brief_id for b in store.briefs(limit=2, offset=4)] == [
        "brief_test_001",
        "brief_test_000",
    ]


def test_briefs_do_not_pollute_the_record_stream(store):
    """Each stream stays homogeneous — briefs must not land in the records JSONL."""
    store.write_brief(make_brief(1))
    assert store.jsonl_count() == 0
    assert list(store.iter_jsonl()) == []
    assert store.briefs_jsonl_count() == 1


def test_briefs_path_is_derived_from_the_records_path(tmp_path):
    s = DualWriteStore(tmp_path / "signals.jsonl", tmp_path / "p.db")
    assert s.briefs_path == tmp_path / "signals_briefs.jsonl"


def test_briefs_path_can_be_given_explicitly(tmp_path):
    explicit = tmp_path / "somewhere" / "b.jsonl"
    s = DualWriteStore(tmp_path / "signals.jsonl", tmp_path / "p.db", explicit)
    s.write_brief(make_brief(1))
    assert explicit.exists()


# ── Recovery ─────────────────────────────────────────────────────────────────


def test_rebuild_restores_briefs_from_ground_truth(store):
    for i in range(3):
        store.write_brief(make_brief(i))

    with sqlite3.connect(store.db_path) as conn:
        conn.execute("DELETE FROM briefs")
        conn.commit()
    assert store.brief_count() == 0

    store.rebuild_from_jsonl()
    assert store.brief_count() == 3
    assert store.latest_brief().brief_id == "brief_test_002"


def test_reopening_the_store_keeps_briefs(tmp_path):
    """The bug in one line: a brief must outlive the process that built it."""
    paths = (tmp_path / "s.jsonl", tmp_path / "p.db")
    DualWriteStore(*paths).write_brief(make_brief(1))

    assert DualWriteStore(*paths).latest_brief().brief_id == "brief_test_001"


# ── Pipeline integration ─────────────────────────────────────────────────────


def _cycle(settings, store, fixture_dir):
    return run_cycle(
        settings=settings,
        adapters=default_adapters(settings, fixture_dir),
        store=store,
    )


def test_a_cycle_persists_the_brief_it_assembles(settings, store, fixture_dir):
    result = _cycle(settings, store, fixture_dir)

    assert result.brief is not None
    assert store.brief_count() == 1
    assert store.latest_brief().brief_id == result.brief.brief_id


def test_the_brief_survives_a_second_barren_cycle(settings, store, fixture_dir):
    """
    The heart of the bug.

    The second cycle produces nothing -- the novelty gate drops every signal already
    stored -- so it cannot regenerate a brief. The first one must still be there.
    """
    first = _cycle(settings, store, fixture_dir)
    second = _cycle(settings, store, fixture_dir)

    assert second.opportunities == 0
    assert second.brief is None
    assert store.latest_brief().brief_id == first.brief.brief_id


def test_a_cli_run_is_visible_to_a_later_reader(settings, tmp_path, fixture_dir):
    """A brief written by one process is readable by the next — the scheduler case."""
    writer = DualWriteStore(settings.store_path, settings.db_path)
    result = _cycle(settings, writer, fixture_dir)
    assert result.brief is not None

    reader = DualWriteStore(settings.store_path, settings.db_path)
    assert reader.latest_brief().brief_id == result.brief.brief_id


def test_brief_traceability_survives_the_round_trip(settings, store, fixture_dir):
    """Every section still cites records the store holds."""
    result = _cycle(settings, store, fixture_dir)
    stored = store.latest_brief()
    known = {r.record_id for r in store.iter_jsonl()}

    assert stored.sections
    for section in stored.sections:
        assert section.source_record_ids
        assert set(section.source_record_ids) <= known
    assert stored.run_id == result.run_id
