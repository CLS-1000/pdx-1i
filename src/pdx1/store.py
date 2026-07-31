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
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import closing
from pathlib import Path

from .models import IntelligenceRecord

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
"""


class DualWriteStore:
    """Writes IntelligenceRecords to JSONL and SQLite."""

    def __init__(self, jsonl_path: Path | str, db_path: Path | str) -> None:
        self.jsonl_path = Path(jsonl_path)
        self.db_path = Path(db_path)
        self._ensure_paths()
        self._init_db()

    def _ensure_paths(self) -> None:
        for path in (self.jsonl_path, self.db_path):
            path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with closing(self._connect()) as conn:
            conn.executescript(SCHEMA)
            conn.commit()

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

    def known_signal_hashes(self) -> set[str]:
        """
        Content hashes of everything already recorded, for seeding the novelty gate.

        Read from ground truth so novelty survives a database rebuild.
        """
        return {r.dedup_hash for r in self.iter_jsonl() if r.dedup_hash}

    # ── Recovery ─────────────────────────────────────────────────────────────

    def rebuild_from_jsonl(self) -> int:
        """Drop and repopulate SQLite from ground truth. Returns the row count."""
        with closing(self._connect()) as conn:
            conn.execute("DELETE FROM intelligence_records")
            conn.commit()

        records = list(self.iter_jsonl())
        if records:
            self._insert_sqlite(records)
        logger.info("rebuilt %s from %s: %d rows", self.db_path, self.jsonl_path, len(records))
        return len(records)
