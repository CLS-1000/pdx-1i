"""Centralised DB layer: migrations, pragmas, and the paths routed through it."""

from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest

from pdx1 import db
from pdx1.store import DualWriteStore

from test_store import make_record


def _tables(path) -> set[str]:
    with closing(sqlite3.connect(path)) as conn:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_migrate_creates_core_tables_and_records_versions(tmp_path):
    path = tmp_path / "sub" / "x.db"
    assert db.migrate(path) == [v for v, _, _ in db.MIGRATIONS]
    assert {"intelligence_records", "briefs", "olis_emitted", "record_entities", "schema_migrations"} <= _tables(path)
    with closing(db.connect(path)) as conn:
        assert db.applied_versions(conn) == [v for v, _, _ in db.MIGRATIONS]


def test_migrate_is_idempotent(tmp_path):
    db.migrate(tmp_path / "x.db")
    assert db.migrate(tmp_path / "x.db") == []


def test_connect_enables_foreign_keys(tmp_path):
    with closing(db.connect(tmp_path / "x.db")) as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_unversioned_legacy_database_is_adopted_and_backfilled(tmp_path):
    path = tmp_path / "legacy.db"
    store = DualWriteStore(tmp_path / "s.jsonl", path)
    store.write([make_record(1)])
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("DROP TABLE record_entities")
        conn.execute("DROP TABLE schema_migrations")
        conn.commit()
    DualWriteStore(tmp_path / "s.jsonl", path)
    with closing(db.connect(path)) as conn:
        rows = conn.execute("SELECT record_id, entity_id FROM record_entities").fetchall()
    assert [tuple(r) for r in rows] == [("rec_test_0001", "pge")]


def test_write_populates_record_entities_and_read_uses_it(tmp_path):
    store = DualWriteStore(tmp_path / "s.jsonl", tmp_path / "x.db")
    store.write([make_record(1), make_record(2)])
    with closing(store._connect()) as conn:
        assert conn.execute("SELECT count(*) FROM record_entities").fetchone()[0] == 2
    assert store.entity_ids_for_records(["rec_test_0001", "missing"]) == ["pge"]
    assert store.entity_ids_for_records([]) == []


def test_rebuild_repopulates_record_entities(tmp_path):
    store = DualWriteStore(tmp_path / "s.jsonl", tmp_path / "x.db")
    store.write([make_record(1)])
    assert store.rebuild_from_jsonl() == 1
    assert store.entity_ids_for_records(["rec_test_0001"]) == ["pge"]


# ── Migrations are atomic ────────────────────────────────────────────────────


def test_a_failed_migration_leaves_no_trace(tmp_path):
    """
    A migration that raises must roll back its own DDL.

    `executescript` COMMITs any pending transaction before running, so using it
    would leave the DDL behind and record no version -- the runner's BEGIN/rollback
    would read as protective while protecting nothing. Statement-wise execution plus
    SQLite's transactional DDL is what makes the rollback real.
    """
    target = tmp_path / "x.db"

    def failing(conn):
        db._exec_script(conn, "CREATE TABLE IF NOT EXISTS canary (x TEXT);")
        raise RuntimeError("boom")

    original = db.MIGRATIONS[:]
    db.MIGRATIONS = original[:1] + [(99, "failing", failing)]
    try:
        with pytest.raises(RuntimeError):
            db.migrate(target)
    finally:
        db.MIGRATIONS = original

    with closing(db.connect(target)) as conn:
        leaked = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='canary'"
        ).fetchone()
        assert leaked is None
        assert 99 not in db.applied_versions(conn)


def test_the_schemas_split_into_executable_statements():
    """The tokenizer must not merge or drop a statement from a commented script."""
    baseline = list(db._statements(db.BASELINE_SCHEMA))
    links = list(db._statements(db.RECORD_ENTITIES_SCHEMA))

    assert len(baseline) == 9
    assert len(links) == 3
    for statement in baseline + links:
        assert statement.endswith(";")

    with closing(sqlite3.connect(":memory:")) as conn:
        for statement in baseline + links:
            conn.execute(statement)


# ── The backfill survives a row it cannot parse ──────────────────────────────


def _insert_bare_record(conn, record_id: str, entity_ids: str) -> None:
    columns = [row[1] for row in conn.execute("PRAGMA table_info(intelligence_records)")]
    values = dict.fromkeys(columns, "")
    values["record_id"] = record_id
    values["entity_ids"] = entity_ids
    marks = ",".join("?" * len(columns))
    conn.execute(
        f"INSERT INTO intelligence_records ({','.join(columns)}) VALUES ({marks})",  # nosec B608
        [values[c] for c in columns],
    )


@pytest.mark.parametrize("entity_ids", ["", "pge,metro", "{oops"])
def test_an_unparseable_entity_ids_row_does_not_fail_the_migration(tmp_path, entity_ids):
    """
    One bad row must not stop the cycle starting -- forever.

    `json_each` raises on a value it cannot parse. Without the `json_valid` guard the
    migration fails, and since its ledger row is never written it fails again on every
    open: a crash loop on the scheduler rather than one skipped record.
    """
    target = tmp_path / "x.db"
    original = db.MIGRATIONS[:]
    db.MIGRATIONS = [m for m in original if m[0] == 1]
    try:
        db.migrate(target)
    finally:
        db.MIGRATIONS = original

    with closing(db.connect(target)) as conn:
        _insert_bare_record(conn, "r_bad", entity_ids)
        _insert_bare_record(conn, "r_good", '["pge","metro"]')
        conn.commit()

    assert 2 in db.migrate(target)

    with closing(db.connect(target)) as conn:
        linked = {
            (row[0], row[1])
            for row in conn.execute("SELECT record_id, entity_id FROM record_entities")
        }
    # The good row is linked; the unparseable one contributes nothing and raises nothing.
    assert linked == {("r_good", "pge"), ("r_good", "metro")}
