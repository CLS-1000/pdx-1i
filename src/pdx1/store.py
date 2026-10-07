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

from . import db
from .ledger import read_ledger
from .models import Brief, IntelligenceRecord, utcnow

logger = logging.getLogger(__name__)


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
        #: One line per cycle, written whether or not a brief published. Plain JSON
        #: rather than a model: it is an operational log, and a run that crashed
        #: before building any model still has to leave a line.
        self.runs_path = self.jsonl_path.with_name(f"{self.jsonl_path.stem}_runs.jsonl")
        #: Ground truth for which OLIS transitions have been emitted. Before this file
        #: existed the table lived only in SQLite, so a host that rebuilds SQLite from
        #: JSONL each run (the daily cloud task) started every morning believing no
        #: transition had ever been emitted.
        self.olis_emitted_path = self.jsonl_path.with_name(
            f"{self.jsonl_path.stem}_olis_emitted.jsonl"
        )
        self._ensure_paths()
        self._init_db()
        self._backfill_olis_emitted_jsonl()

    def _ensure_paths(self) -> None:
        for path in (self.jsonl_path, self.db_path, self.briefs_path):
            path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        return db.connect(self.db_path)

    def _init_db(self) -> None:
        db.migrate(self.db_path)

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

    def write_run(self, line: dict) -> None:
        """
        Append one run-ledger line, flushed and fsynced.

        This is the daily evidence for the unattended count: a quiet day, a partial
        day and a crashed day each leave one line that tells them apart, where
        otherwise all three leave the same trace -- no brief.
        """
        with self.runs_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, sort_keys=True, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def iter_runs(self) -> Iterator[dict]:
        """Every run-ledger line, oldest first. Blank or torn lines are skipped."""
        return read_ledger(self.runs_path)

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
            conn.executemany(
                "INSERT OR IGNORE INTO record_entities (record_id, entity_id) VALUES (?,?)",
                [(r.record_id, e) for r in records for e in dict.fromkeys(r.entity_ids)],
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

        Resolved through the `record_entities` link table (migration 2) rather than by
        scanning and decoding every record's JSON `entity_ids`.
        """
        wanted = set(record_ids)
        if not wanted:
            return []

        # Resolved through the indexed record_entities link table. Parameters are
        # bound in chunks to stay under SQLite's host-parameter cap; the SQL text is
        # built only from a count of "?" placeholders, never from the ids.
        found: set[str] = set()
        ids = sorted(wanted)
        with closing(self._connect()) as conn:
            for i in range(0, len(ids), 500):
                chunk = ids[i : i + 500]
                marks = ",".join("?" * len(chunk))
                rows = conn.execute(
                    f"SELECT DISTINCT entity_id FROM record_entities WHERE record_id IN ({marks})",  # nosec B608
                    chunk,
                ).fetchall()
                found.update(row["entity_id"] for row in rows)
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

    def count_leads(self) -> int:
        """Return the total number of records exposed by the analyst queue."""
        with closing(self._connect()) as conn:
            return int(
                conn.execute(
                    """
                    SELECT count(*) FROM intelligence_records
                     WHERE outcome IN ('ESCALATE', 'CORROBORATED', 'INVESTIGATE')
                    """
                ).fetchone()[0]
            )

    def query_leads(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> list[IntelligenceRecord]:
        """Read one confidence-ranked page of the analyst queue."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT payload FROM intelligence_records
                 WHERE outcome IN ('ESCALATE', 'CORROBORATED', 'INVESTIGATE')
                 ORDER BY confidence DESC, julianday(published_at) DESC, record_id DESC
                 LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        return [IntelligenceRecord.model_validate_json(row["payload"]) for row in rows]

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

        Same order as every other stream: new rows go to the JSONL file first
        (flushed and fsynced), then to SQLite. A transition already recorded keeps its
        original `emitted_at` and `rules_version` in both places, which is what makes
        the rules_version column worth reading later.
        """
        stamp = emitted_at or utcnow().isoformat()
        candidates = {}
        for s, p, n, a, c, st in transitions:
            candidates.setdefault((s, p, int(n), int(a), st), (s, p, int(n), int(a), c, st))
        if not candidates:
            return 0
        with closing(self._connect()) as conn:
            sessions = {key[0] for key in candidates}
            existing = {
                (r["session_key"], r["measure_prefix"], int(r["measure_number"]),
                 int(r["action_id"]), r["state"])
                for session in sessions
                for r in conn.execute(
                    "SELECT session_key, measure_prefix, measure_number, action_id, state "
                    "FROM olis_emitted WHERE session_key = ?",
                    (session,),
                )
            }
        fresh = [row for key, row in sorted(candidates.items()) if key not in existing]
        if not fresh:
            return 0
        lines = [
            {
                "session_key": s, "measure_prefix": p, "measure_number": n,
                "action_id": a, "chamber": c, "state": st,
                "rules_version": rules_version, "emitted_at": stamp,
            }
            for s, p, n, a, c, st in fresh
        ]
        self._append_olis_emitted_jsonl(lines)
        self._insert_olis_emitted(lines)
        return len(fresh)

    def _append_olis_emitted_jsonl(self, lines: list[dict]) -> None:
        with self.olis_emitted_path.open("a", encoding="utf-8") as fh:
            for line in lines:
                fh.write(json.dumps(line, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def _insert_olis_emitted(self, lines: list[dict]) -> None:
        with closing(self._connect()) as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO olis_emitted
                    (session_key, measure_prefix, measure_number, action_id,
                     chamber, state, rules_version, emitted_at)
                VALUES (:session_key, :measure_prefix, :measure_number, :action_id,
                        :chamber, :state, :rules_version, :emitted_at)
                """,
                lines,
            )
            conn.commit()

    def iter_olis_emitted(self) -> Iterator[dict]:
        """Every emitted-transition line in ground truth. Torn lines are skipped."""
        if not self.olis_emitted_path.exists():
            return
        with self.olis_emitted_path.open(encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    yield json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("olis_emitted ledger: skipped unreadable line")

    def _backfill_olis_emitted_jsonl(self) -> None:
        """
        One-time migration for stores created before the JSONL file existed.

        If SQLite holds emitted transitions and the file does not, write them out, so
        the next `rebuild_from_jsonl` keeps them instead of wiping them.
        """
        if self.olis_emitted_path.exists():
            return
        with closing(self._connect()) as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT session_key, measure_prefix, measure_number, action_id, "
                "chamber, state, rules_version, emitted_at FROM olis_emitted "
                "ORDER BY emitted_at, session_key, measure_prefix, measure_number, action_id"
            )]
        if rows:
            self._append_olis_emitted_jsonl(rows)
            logger.info(
                "olis_emitted: backfilled %d row(s) to %s", len(rows), self.olis_emitted_path
            )

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
        rather than SQL. The `record_entities` link table (migration 2) could serve
        this as an indexed `GROUP BY`; it is not used here yet because the record set
        is one metro's public filings and the scan has never been the slow part. If
        that stops being true, move this and `records_for_entity` onto the link table
        together, with a parity test against the JSON column.

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

        Rebuilds records, briefs and the OLIS emitted-transition table. The return
        value counts records only, for backwards compatibility.
        """
        with closing(self._connect()) as conn:
            conn.execute("DELETE FROM record_entities")
            conn.execute("DELETE FROM intelligence_records")
            conn.execute("DELETE FROM briefs")
            conn.execute("DELETE FROM olis_emitted")
            conn.commit()

        records = list(self.iter_jsonl())
        if records:
            self._insert_sqlite(records)

        briefs = list(self.iter_briefs())
        for brief in briefs:
            self._insert_brief(brief)

        emitted = list(self.iter_olis_emitted())
        if emitted:
            self._insert_olis_emitted(emitted)

        logger.info(
            "rebuilt %s: %d record(s) from %s, %d brief(s) from %s",
            self.db_path,
            len(records),
            self.jsonl_path,
            len(briefs),
            self.briefs_path,
        )
        return len(records)
