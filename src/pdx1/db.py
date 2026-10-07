"""
Centralised SQLite access: one connection factory and a versioned migration runner.

Every store opens its database through `connect` and brings it up to date through
`migrate`, so pragmas and schema live in one place instead of in each caller. JSONL
remains ground truth (see `store.py`); this module only governs the query layer, which
is why every migration here is safe to discard and rebuild with `rebuild_from_jsonl()`.

Migrations are numbered, applied in order, and recorded in `schema_migrations`, and
each one runs inside a transaction with its ledger row: SQLite makes DDL
transactional, so a migration that raises half way leaves no trace of itself. That
requires executing statement by statement rather than through `executescript`, which
COMMITs any open transaction before it runs and so would silently defeat the
rollback -- see `_statements`.

Every migration is *also* written to be idempotent, because a database created before
versioning existed has nothing recorded and must be adoptable without a rewrite.
Migrations are forward-only; rollback is "delete the database and rebuild from JSONL"
(see docs/centralized-db-audit.md).
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

BASELINE_SCHEMA = """
CREATE TABLE IF NOT EXISTS intelligence_records (
    record_id     TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL,
    source        TEXT NOT NULL,
    source_type   TEXT NOT NULL,
    pattern       TEXT NOT NULL,
    outcome       TEXT NOT NULL,
    priority      TEXT NOT NULL,
    confidence    REAL NOT NULL,
    tier          TEXT NOT NULL,
    sigma         REAL,
    anomaly_tier  TEXT,
    entity_ids    TEXT NOT NULL,
    signal_id     TEXT NOT NULL,
    url           TEXT,
    published_at  TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    payload       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_records_run     ON intelligence_records(run_id);
CREATE INDEX IF NOT EXISTS idx_records_outcome ON intelligence_records(outcome);
CREATE INDEX IF NOT EXISTS idx_records_source  ON intelligence_records(source);

CREATE TABLE IF NOT EXISTS briefs (
    brief_id     TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL,
    date         TEXT NOT NULL,
    headline     TEXT NOT NULL,
    confidence   REAL NOT NULL,
    section_count INTEGER NOT NULL,
    produced_at  TEXT NOT NULL,
    payload      TEXT NOT NULL,
    -- Audit trail from the observation-only checks, flattened out of `payload` so it
    -- can be queried without JSON extraction. Defaulted, so a database written before
    -- observations existed opens without a rewrite.
    observations TEXT NOT NULL DEFAULT '[]',
    observation_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_briefs_produced ON briefs(produced_at);
CREATE INDEX IF NOT EXISTS idx_briefs_run      ON briefs(run_id);

-- Procedural transitions the OLIS adapter has already emitted a Signal for.
--
-- A transition set rather than a final-state map, because OLIS rows are inserted and
-- edited retroactively: in 2025R1, 7.1% of rows carry a ModifiedDate and 2,188 were
-- created at least a day after their own ActionDate, one of them 399.9 days after.
-- The adapter replays each measure from scratch every cycle, so a row backdated into
-- the middle of a history changes the computed transitions and shows up in the diff
-- even though nothing new appeared at the tail. A final-state comparison misses that,
-- and also collapses several emittable transitions landing in one cycle into one.
--
-- session_key is part of the primary key because measure numbers repeat across
-- sessions -- SB 976 exists in both 2019R1 and 2025R1.
--
-- rules_version is recorded but deliberately not part of the key: it is there so that
-- when a rule patch changes historical output you can see which rows were produced
-- under which ruleset instead of guessing.
CREATE TABLE IF NOT EXISTS olis_emitted (
  session_key    TEXT    NOT NULL,
  measure_prefix TEXT    NOT NULL,
  measure_number INTEGER NOT NULL,
  action_id      INTEGER NOT NULL,
  chamber        TEXT    NOT NULL,
  state          TEXT    NOT NULL,
  rules_version  TEXT    NOT NULL,
  emitted_at     TEXT    NOT NULL,
  PRIMARY KEY (session_key, measure_prefix, measure_number, action_id, state)
);
CREATE INDEX IF NOT EXISTS idx_olis_emitted_session ON olis_emitted(session_key);
"""

# Link table for the JSON `entity_ids` array on intelligence_records, so entity lookups
# can be indexed. The JSON column stays: it is part of the stored record shape.
RECORD_ENTITIES_SCHEMA = """
CREATE TABLE IF NOT EXISTS record_entities (
    record_id TEXT NOT NULL REFERENCES intelligence_records(record_id) ON DELETE CASCADE,
    entity_id TEXT NOT NULL,
    PRIMARY KEY (record_id, entity_id)
);
CREATE INDEX IF NOT EXISTS idx_record_entities_entity ON record_entities(entity_id);

-- `json_valid` is not belt-and-braces. `json_each` raises on a value it cannot
-- parse, and because this backfill runs over whatever the database already holds,
-- one unparseable row would fail the whole migration -- on every open, forever,
-- since the ledger row is never written. On the scheduler under
-- `Restart=on-failure` that is a crash loop rather than a visible error. The
-- writer only ever emits a JSON array, so the guard should never exclude a row;
-- it is here so that a legacy or hand-edited one cannot stop the cycle starting.
INSERT OR IGNORE INTO record_entities (record_id, entity_id)
SELECT r.record_id, j.value
  FROM intelligence_records r, json_each(r.entity_ids) j
 WHERE json_valid(r.entity_ids);
"""


def _statements(script: str) -> Iterator[str]:
    """
    Split a DDL script into single statements, using SQLite's own tokenizer.

    `executescript` would be shorter, but it COMMITs any pending transaction before
    running, which silently defeats the runner's BEGIN/rollback: the DDL lands and
    stays even when the migration raises immediately afterwards. Running the
    statements one at a time keeps the whole migration, ledger row included, inside
    one transaction.

    `sqlite3.complete_statement` is what sqlite3's own shell uses to decide whether it
    has a whole statement yet, so `--` comments and semicolons inside string literals
    are handled. A naive `split(";")` is not safe here: these schemas carry comment
    blocks between statements.
    """
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                yield statement
            buffer = ""
    trailing = buffer.strip()
    if trailing:
        yield trailing


def _exec_script(conn: sqlite3.Connection, script: str) -> None:
    """Run a DDL script inside the caller's transaction. See `_statements`."""
    for statement in _statements(script):
        conn.execute(statement)


def _m1_baseline(conn: sqlite3.Connection) -> None:
    _exec_script(conn, BASELINE_SCHEMA)
    # CREATE TABLE IF NOT EXISTS leaves an older briefs table alone; add what it lacks.
    existing = {row[1] for row in conn.execute("PRAGMA table_info(briefs)")}
    for column, ddl in (
        ("observations", "TEXT NOT NULL DEFAULT '[]'"),
        ("observation_count", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if column not in existing:
            conn.execute(f"ALTER TABLE briefs ADD COLUMN {column} {ddl}")


def _m2_record_entities(conn: sqlite3.Connection) -> None:
    _exec_script(conn, RECORD_ENTITIES_SCHEMA)


#: (version, name, apply). Append only; never edit or renumber a shipped migration.
MIGRATIONS: list[tuple[int, str, Callable[[sqlite3.Connection], None]]] = [
    (1, "baseline", _m1_baseline),
    (2, "record_entities", _m2_record_entities),
]


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open the database with the project's standard settings."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def applied_versions(conn: sqlite3.Connection) -> list[int]:
    """Versions recorded as applied, ascending. Empty for an unversioned database."""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    if not exists:
        return []
    return [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]


def migrate(db_path: Path | str) -> list[int]:
    """Apply any pending migrations. Returns the versions applied by this call."""
    done: list[int] = []
    with closing(connect(db_path)) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        conn.commit()
        have = set(applied_versions(conn))
        for version, name, apply in MIGRATIONS:
            if version in have:
                continue
            try:
                conn.execute("BEGIN")
                apply(conn)
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?,?,?)",
                    (version, name, datetime.now(UTC).isoformat()),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            logger.info("applied migration %d (%s) to %s", version, name, db_path)
            done.append(version)
    return done
