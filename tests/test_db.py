"""Centralised DB layer: migrations, pragmas, and the paths routed through it."""

from __future__ import annotations

import sqlite3
from contextlib import closing

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
