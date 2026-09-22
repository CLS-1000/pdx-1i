"""
Dual-write storage: JSONL as ground truth, SQLite as the query layer.

The two stores are not equal partners. JSONL is append-only and authoritative; SQLite
exists to answer questions quickly and can be rebuilt from the JSONL at any time.

There is no cross-process transaction spanning a file append and a database commit, and
this module does not pretend otherwise. The write order is deliberate:

    1. append to JSONL, flush and fsync
    2. insert into SQLite and commit

If step 2 fails, the JSONL still holds the record and `rebuild_from_jsonl` restores the
database. The reverse order would let SQLite hold a record that ground truth never saw,
which is the failure mode worth avoiding.

Two streams are persisted, each with its own ground-truth file and table:

    IntelligenceRecord  ->  <store>.jsonl          + intelligence_records
    Brief               ->  <store>_briefs.jsonl   + briefs

They are kept apart rather than interleaved so each file stays a homogeneous stream that
can be read back without discriminating on type. Briefs are persisted because they are
the product: a brief assembled by the scheduler at 06:00 has to outlive the process that
built it, and a re-run cannot regenerate it -- the novelty gate correctly drops signals
already stored, so a second cycle over the same input produces no records and therefore
no brief.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import closing
from pathlib import Path

from .models import Brief, IntelligenceRecord, utcnow

logger = logging.getLogger(__name__)

SCHEMA = """
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


class DualWriteStore:
    """Writes IntelligenceRecords to JSONL and SQLite."""

    def __init__(
        self,
        jsonl_path: Path | str,
        db_path: Path | str,
        briefs_path: Path | str | None = None,
    ) -> None:
        self.jsonl_path = Path(jsonl_path)
        self.db_path = Path(db_path)
        # Derived from the records path so a caller that only knows about records --
        # including every existing call site -- still gets a working brief store.
        self.briefs_path = (
            Path(briefs_path)
            if briefs_path is not None
            else self.jsonl_path.with_name(f"{self.jsonl_path.stem}_briefs.jsonl")
        )
        self._ensure_paths()
        self._init_db()

    def _ensure_paths(self) -> None:
        for path in (self.jsonl_path, self.db_path, self.briefs_path):
            path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with closing(self._connect()) as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)
            conn.commit()

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """
        Bring an existing database up to the current schema.

        `CREATE TABLE IF NOT EXISTS` leaves an already-created table alone, so columns
        added later have to be applied by hand. Each is defaulted, so rows written
        before the column existed stay readable -- ground truth is the JSONL either
        way, and this table is a query layer that can be rebuilt from it.
        """
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(briefs)")}
        for column, ddl in (
            ("observations", "TEXT NOT NULL DEFAULT '[]'"),
            ("observation_count", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if column not in existing:
                conn.execute(f"ALTER TABLE briefs ADD COLUMN {column} {ddl}")

    # ── Writing ──────────────────────────────────────────────────────────────

    def write(self, records: Iterable[IntelligenceRecord]) -> int:
        """
        Persist records to both stores. Returns the number written.

        Records already present (same record_id) are skipped rather than duplicated, so
        re-running a cycle over the same input is idempotent.
        """
        pending = [r for r in records if not self.has(r.record_id)]
        if not pending:
            return 0

        self._append_jsonl(pending)
        self._insert_sqlite(pending)
        return len(pending)

    def _append_jsonl(self, records: list[IntelligenceRecord]) -> None:
        with self.jsonl_path.open("a", encoding="utf-8") as fh:
            for record in records:
                fh.write(record.model_dump_json() + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def _insert_sqlite(self, records: list[IntelligenceRecord]) -> None:
        rows = [
            (
                r.record_id,
                r.run_id,
                r.source,
                r.source_type.value,
                r.pattern,
                r.outcome.value,
                r.priority.value,
                r.confidence,
                r.tier.value,
                r.anomaly.sigma if r.anomaly else None,
                r.anomaly.tier.value if r.anomaly else None,
                json.dumps(r.entity_ids),
                r.signal_id,
                r.url,
                r.published_at.isoformat(),
                r.created_at.isoformat(),
                r.model_dump_json(),
            )
            for r in records
        ]
        with closing(self._connect()) as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO intelligence_records
                    (record_id, run_id, source, source_type, pattern, outcome, priority,
                     confidence, tier, sigma, anomaly_tier, entity_ids, signal_id, url,
                     published_at, created_at, payload)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                rows,
            )
            conn.commit()

    # ── Reading ──────────────────────────────────────────────────────────────

    def has(self, record_id: str) -> bool:
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "SELECT 1 FROM intelligence_records WHERE record_id = ? LIMIT 1",
                (record_id,),
            )
            return cur.fetchone() is not None

    def count(self) -> int:
        with closing(self._connect()) as conn:
            return int(
                conn.execute("SELECT count(*) FROM intelligence_records").fetchone()[0]
            )

    def entity_ids_for_records(self, record_ids: Iterable[str]) -> list[str]:
        """
        Entity ids mentioned by the given records, de-duplicated and sorted.

        A `Brief` section carries `source_record_ids`, not entity ids, so anything
        wanting to draw the bodies behind a brief -- the PDF network diagram -- has to
        resolve one to the other, and that needs the store. `render_brief_pdf` takes
        `entity_ids` as a parameter for exactly this reason.

        A record id that is not in the store contributes nothing rather than raising.
        The brief is ground truth for what was published; if the query layer has fallen
        behind it, the diagram should be the part that goes thin, not the document that
        fails to render. `rebuild_from_jsonl()` is the fix for that state.

        Sorted so the same brief renders the same diagram on every call --
        `build_network_drawing` lays out on sorted ids, and an unstable order here
        would put a stable layout behind an unstable input.

        Matched in Python rather than through a generated `IN (?,?,...)` clause, for
        the same two reasons `entity_record_counts` tallies this column in Python: the
        SQL stays a fixed string that no future edit can make injectable, and it does
        not run into SQLite's cap on host parameters when a brief cites more records
        than the driver will bind at once. The record set is one metro's public
        filings, not a firehose; if that stops being true this wants a join table.
        """
        wanted = set(record_ids)
        if not wanted:
            return []

        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT record_id, entity_ids FROM intelligence_records"
            ).fetchall()

        found: set[str] = set()
        for row in rows:
            if row["record_id"] in wanted:
                found.update(json.loads(row["entity_ids"]))
        return sorted(found)

    def count_query(
        self,
        outcome: str | None = None,
        source: str | None = None,
    ) -> int:
        """Return the total number of records matching the given filters."""
        with closing(self._connect()) as conn:
            return int(
                conn.execute(
                    """
                    SELECT count(*) FROM intelligence_records
                     WHERE (? IS NULL OR outcome = ?)
                       AND (? IS NULL OR source  = ?)
                    """,
                    (outcome, outcome, source, source),
                ).fetchone()[0]
            )

    def jsonl_count(self) -> int:
        if not self.jsonl_path.exists():
            return 0
        with self.jsonl_path.open(encoding="utf-8") as fh:
            return sum(1 for line in fh if line.strip())

    def iter_jsonl(self) -> Iterator[IntelligenceRecord]:
        """Stream ground truth back as models."""
        if not self.jsonl_path.exists():
            return
        with self.jsonl_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield IntelligenceRecord.model_validate_json(line)

    def query(
        self,
        outcome: str | None = None,
        source: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[IntelligenceRecord]:
        """
        Read records back out of the query layer.

        The SQL is a fixed string with no interpolation -- an omitted filter is passed
        as NULL and short-circuits its own clause. Building the WHERE clause by string
        concatenation would work here too, but a static query cannot be made injectable
        by a future edit.
        """
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT payload FROM intelligence_records
                 WHERE (? IS NULL OR outcome = ?)
                   AND (? IS NULL OR source  = ?)
                 ORDER BY published_at DESC
                 LIMIT ? OFFSET ?
                """,
                (outcome, outcome, source, source, limit, offset),
            ).fetchall()

        return [IntelligenceRecord.model_validate_json(row["payload"]) for row in rows]

    # ── OLIS emitted transitions ─────────────────────────────────────────────

    def olis_emitted(self, session_key: str) -> set[tuple[str, str, int, int, str, str]]:
        """
        Every transition already emitted for one session.

        Returned in the adapter's tuple shape --
        `(session_key, prefix, number, action_id, chamber, state)` -- so the adapter
        can take a plain set difference against what this cycle's replay produced.
        """
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT session_key, measure_prefix, measure_number,
                       action_id, chamber, state
                  FROM olis_emitted
                 WHERE session_key = ?
                """,
                (session_key,),
            ).fetchall()
        return {
            (
                r["session_key"],
                r["measure_prefix"],
                int(r["measure_number"]),
                int(r["action_id"]),
                r["chamber"],
                r["state"],
            )
            for r in rows
        }

    def record_olis_emitted(
        self,
        transitions: Iterable[tuple[str, str, int, int, str, str]],
        rules_version: str,
        emitted_at: str | None = None,
    ) -> int:
        """
        Mark transitions as emitted. Returns the number of new rows.

        `INSERT OR IGNORE` rather than a replace: a transition that is already recorded
        keeps its original `emitted_at` and `rules_version`, which is what makes the
        rules_version column worth reading later.

        This table is a query-layer bookkeeping aid, not ground truth -- the Signals it
        gates are written to JSONL by the normal path. It is therefore the one table
        here that is not reconstructible from `rebuild_from_jsonl`; losing it costs a
        re-bootstrap, not a record.
        """
        stamp = emitted_at or utcnow().isoformat()
        rows = [(s, p, n, a, c, st, rules_version, stamp) for s, p, n, a, c, st in transitions]
        if not rows:
            return 0
        with closing(self._connect()) as conn:
            before = conn.execute("SELECT count(*) FROM olis_emitted").fetchone()[0]
            conn.executemany(
                """
                INSERT OR IGNORE INTO olis_emitted
                    (session_key, measure_prefix, measure_number, action_id,
                     chamber, state, rules_version, emitted_at)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                rows,
            )
            conn.commit()
            after = conn.execute("SELECT count(*) FROM olis_emitted").fetchone()[0]
        return int(after - before)

    def olis_emitted_count(self, session_key: str | None = None) -> int:
        """How many transitions are on record, for a session or overall."""
        with closing(self._connect()) as conn:
            return int(
                conn.execute(
                    "SELECT count(*) FROM olis_emitted WHERE (? IS NULL OR session_key = ?)",
                    (session_key, session_key),
                ).fetchone()[0]
            )

    # ── Briefs ───────────────────────────────────────────────────────────────

    def write_brief(self, brief: Brief) -> bool:
        """
        Persist an assembled brief. Returns True if written, False if already present.

        Same ordering as records: ground truth first, then the query layer.
        """
        if self.has_brief(brief.brief_id):
            return False

        with self.briefs_path.open("a", encoding="utf-8") as fh:
            fh.write(brief.model_dump_json() + "\n")
            fh.flush()
            os.fsync(fh.fileno())

        self._insert_brief(brief)
        return True

    def _insert_brief(self, brief: Brief) -> None:
        # Flattened from the sections so the audit trail is queryable directly. Each
        # entry carries the section it came from -- an observation without its section
        # says a term appeared somewhere in the brief, which is not much use.
        observations = [
            {"section": section.title, **observation.model_dump()}
            for section in brief.sections
            for observation in section.observations
        ]
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO briefs
                    (brief_id, run_id, date, headline, confidence, section_count,
                     produced_at, payload, observations, observation_count)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    brief.brief_id,
                    brief.run_id,
                    brief.date,
                    brief.headline,
                    brief.confidence,
                    len(brief.sections),
                    brief.produced_at.isoformat(),
                    brief.model_dump_json(),
                    json.dumps(observations),
                    len(observations),
                ),
            )
            conn.commit()

    def has_brief(self, brief_id: str) -> bool:
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "SELECT 1 FROM briefs WHERE brief_id = ? LIMIT 1", (brief_id,)
            )
            return cur.fetchone() is not None

    def brief_count(self) -> int:
        with closing(self._connect()) as conn:
            return int(conn.execute("SELECT count(*) FROM briefs").fetchone()[0])

    def latest_brief(self) -> Brief | None:
        """
        Most recently produced brief, or None if none has been assembled.

        Ordered by `produced_at`, with rowid as the tiebreak so two briefs produced in
        the same second still resolve deterministically to the one written later.
        """
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT payload FROM briefs ORDER BY produced_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
        return Brief.model_validate_json(row["payload"]) if row else None

    def brief(self, brief_id: str) -> Brief | None:
        """Fetch one brief by ID."""
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT payload FROM briefs WHERE brief_id = ?", (brief_id,)
            ).fetchone()
        return Brief.model_validate_json(row["payload"]) if row else None

    def briefs(self, limit: int = 50, offset: int = 0) -> list[Brief]:
        """Briefs newest first — the archive."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT payload FROM briefs ORDER BY produced_at DESC, rowid DESC "
                "LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [Brief.model_validate_json(r["payload"]) for r in rows]

    def iter_briefs(self) -> Iterator[Brief]:
        """Stream brief ground truth back as models."""
        if not self.briefs_path.exists():
            return
        with self.briefs_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield Brief.model_validate_json(line)

    def briefs_jsonl_count(self) -> int:
        if not self.briefs_path.exists():
            return 0
        with self.briefs_path.open(encoding="utf-8") as fh:
            return sum(1 for line in fh if line.strip())

    # ── Entity activity ──────────────────────────────────────────────────────

    def entity_record_counts(self) -> dict[str, int]:
        """
        How many stored records mention each entity, keyed by node ID.

        `entity_ids` is a JSON array in a text column, so this tallies in Python
        rather than SQL. The record set is one metro's public filings, not a firehose;
        if that stops being true, this wants a join table rather than a cleverer query.

        A count is a count. It says how often a body appears in the record set and
        nothing about why, which is the only claim the graph is entitled to make.
        """
        counts: dict[str, int] = {}
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT entity_ids FROM intelligence_records").fetchall()
        for row in rows:
            for node_id in json.loads(row["entity_ids"]):
                counts[node_id] = counts.get(node_id, 0) + 1
        return counts

    def records_for_entity(self, node_id: str, limit: int = 50) -> list[IntelligenceRecord]:
        """
        Records mentioning one entity, newest first.

        Matched against the stored JSON array rather than a substring of it, so `pge`
        cannot match a record that only mentions some other id containing those letters.
        """
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT payload, entity_ids FROM intelligence_records "
                "ORDER BY published_at DESC"
            ).fetchall()

        out: list[IntelligenceRecord] = []
        for row in rows:
            if node_id in json.loads(row["entity_ids"]):
                out.append(IntelligenceRecord.model_validate_json(row["payload"]))
            if len(out) >= limit:
                break
        return out

    # ── Novelty seeding ──────────────────────────────────────────────────────

    def known_signal_hashes(self) -> set[str]:
        """
        Content hashes of everything already recorded, for seeding the novelty gate.

        Read from ground truth so novelty survives a database rebuild.
        """
        return {r.dedup_hash for r in self.iter_jsonl() if r.dedup_hash}

    # ── Recovery ─────────────────────────────────────────────────────────────

    def rebuild_from_jsonl(self) -> int:
        """
        Drop and repopulate SQLite from ground truth. Returns the record count.

        Rebuilds both streams -- records and briefs. The return value counts records
        only, for backwards compatibility; use `brief_count()` for the other.
        """
        with closing(self._connect()) as conn:
            conn.execute("DELETE FROM intelligence_records")
            conn.execute("DELETE FROM briefs")
            conn.commit()

        records = list(self.iter_jsonl())
        if records:
            self._insert_sqlite(records)

        briefs = list(self.iter_briefs())
        for brief in briefs:
            self._insert_brief(brief)

        logger.info(
            "rebuilt %s: %d record(s) from %s, %d brief(s) from %s",
            self.db_path,
            len(records),
            self.jsonl_path,
            len(briefs),
            self.briefs_path,
        )
        return len(records)
