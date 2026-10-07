# Centralized database: audit, target design, and migration plan

## 1. Audit of current persistence

| Store | Where | Written by | Read by | Role |
|---|---|---|---|---|
| `<store>.jsonl` | `PDX1_STORE_PATH` | `DualWriteStore.write` | `iter_jsonl`, `known_signal_hashes`, `rebuild_from_jsonl` | Ground truth: `IntelligenceRecord` |
| `<store>_briefs.jsonl` | derived from store path | `write_brief` | `iter_briefs` | Ground truth: `Brief` |
| `<store>_runs.jsonl` | derived | `write_run` | `iter_runs`, `ledger.py`, pipeline alert | Run ledger (one line per cycle) |
| `<store>_olis_emitted.jsonl` | derived | `record_olis_emitted` | `iter_olis_emitted`, backfill | Ground truth: OLIS transitions |
| SQLite `pdx1.db` (`PDX1_DB_PATH`) | `store.py` | all of the above, after JSONL | API, brief, graph, pipeline | Query layer: `intelligence_records`, `briefs`, `olis_emitted` |
| Adapter last-good caches | `cache_dir` (`sources/base.py`) | `_fetch_live` | `_read_raw` tier 3 | Outage fallback; projected to mapped columns (no donor addresses) |
| Source fixtures | `tests/fixtures`, `fixture_path` | checked in | adapters | Replay input |
| In-memory | `RollingBaseline`, `TriggerState`, novelty gate, resolver | pipeline | pipeline | Re-seeded from the store each cycle |

Findings:

- **Single owner of SQL.** All SQLite access is in `DualWriteStore`; there is no second writer, which makes centralising cheap.
- **Schema management was ad hoc.** `CREATE TABLE IF NOT EXISTS` plus a hand-written `ALTER` in `_migrate`; no version record.
- **JSON-in-column.** `entity_ids` was a JSON array, so entity lookups (`entity_ids_for_records`, `entity_record_counts`, `records_for_entity`) scanned and decoded every row.
- **Drift risk.** ~~`schema/civic_records.sql` describes a different, unused model from the live tables.~~ **Resolved 2026-10-07: deleted.** Reconciling it would have meant designing a second schema nothing reads.
- **Integrity.** JSONL and SQLite have no cross-process transaction; mitigated by write order (JSONL first) and `rebuild_from_jsonl`. The run ledger and caches live outside the DB.
- **Pre-existing, unchanged:** novelty hashes are read from JSONL (`known_signal_hashes`), a full file scan per cycle.

## 2. Recommended approach

**Stay on SQLite for now, behind one module (`pdx1.db`), with versioned migrations; keep JSONL as ground truth.**
Rationale: the workload is one metro's public filings, a single daily writer, a Python 3.12 stack with no ORM, and the project rule that JSONL is authoritative and the DB is rebuildable. SQLite needs no service and works on the scheduler's ephemeral machines. Postgres becomes appropriate if there are multiple concurrent writers or hosted multi-user queries; because SQL is now concentrated in `store.py` and `db.py`, that move is a driver swap plus dialect fixes, not a redesign.
Tradeoff: no ORM or Alembic dependency (a ~100-line runner instead), at the cost of writing migrations by hand.

## 3. Starter schema

Implemented (migrations in `src/pdx1/db.py`):

| Migration | Tables | Notes |
|---|---|---|
| 1 `baseline` | `intelligence_records`, `briefs`, `olis_emitted` | The pre-existing schema, unchanged; idempotent so legacy databases are adopted. Indexes on run, outcome, source, produced_at. |
| 2 `record_entities` | `record_entities(record_id, entity_id)` | PK `(record_id, entity_id)`, index on `entity_id`, FK to `intelligence_records` with `ON DELETE CASCADE`. Backfilled from the JSON column via `json_each`. |
| runner | `schema_migrations(version, name, applied_at)` | Records applied versions. |

```
intelligence_records 1──* record_entities
briefs (run_id) ··· intelligence_records (run_id)
olis_emitted (independent)
```

Proposed for later phases (not created yet): `entities` / `entity_ties` (from `graph.py`, role-based seats only), `runs` (ledger), `sources` and `raw_payloads` (reconcile with `schema/civic_records.sql` rather than keeping both). Names and enum values already serialised (`ConfidenceTier`, `Outcome`, `AnomalyTier`, `TieKind`) must not change.

## 4. What changed now

- `pdx1/db.py`: `connect()` (parent dir creation, `foreign_keys=ON`, Row factory), `migrate()`, `applied_versions()`.
- `DualWriteStore` opens and initialises through `db`; its inline schema moved to migration 1.
- **Write path:** `_insert_sqlite` also writes `record_entities` (still after JSONL). `rebuild_from_jsonl` clears and repopulates it.
- **Read path:** `entity_ids_for_records` queries `record_entities` instead of scanning all rows.
- `tests/test_db.py`.

Compatibility: JSONL files, table names and the public `DualWriteStore` API are unchanged. Nothing legacy was removed.

### 4a. Follow-up fixes — 2026-10-07

An audit of the audit found two latent defects in the runner above, both verified by
test before and after.

**The migration runner's transaction did nothing.** It opened `BEGIN`, called the
migration, inserted the ledger row and rolled back on error -- but both migrations ran
their DDL through `executescript`, which COMMITs any pending transaction before it
runs. Measured: a migration that created a table and then raised left the table
behind and recorded no version.

```
canary table survived the rollback: True      <- before
canary table survived the rollback: False     <- after
```

Fixed by executing statement by statement (`_statements`, `_exec_script`) using
`sqlite3.complete_statement` as the splitter, so `--` comment blocks between
statements are handled. SQLite makes DDL transactional, so the rollback is now real.
Migrations stay idempotent regardless, because an unversioned legacy database must be
adoptable.

**One unparseable row failed migration 2 permanently.** `json_each` raises on a value
it cannot parse, and the backfill runs over whatever the database already holds. With
the transaction broken as above, the table was created but the ledger row was not --
so the migration failed again on *every subsequent open*. On the scheduler under
`Restart=on-failure` that is a crash loop, which is the failure shape D2 set out to
remove, on the machine D4 deploys to.

| `entity_ids` | before | after |
|---|---|---|
| `["a","b"]`, `[]` | OK | OK |
| `''`, `pge,metro`, `{oops` | **`OperationalError: malformed JSON`** | OK, row skipped |

Fixed with `WHERE json_valid(r.entity_ids)` in the backfill. The writer only ever
emits a JSON array, so the guard should never exclude a live row; it is there so a
legacy or hand-edited one cannot stop the cycle starting. Neither defect was
reachable from current data -- the column is `NOT NULL`, and the committed `pdx1.db`
holds 0 rows and 0 invalid values -- so both were latent, not active.

Also: deleted the orphaned `schema/civic_records.sql`, and corrected
`entity_record_counts`'s docstring, which recommended building the join table that
migration 2 had already built.

## 8. Measurements — 2026-10-07

Taken on this checkout, so they can be re-run rather than trusted.

**Migration 2 backfill**, synthetic databases, 400 entities, 0-4 per record:

| records | migration 2 | link rows | db size |
|---|---|---|---|
| 10,000 | 0.07s | 19,790 | 2.4 MB |
| 100,000 | 0.69s | 200,036 | 24.5 MB |
| 500,000 | **4.50s** | 1,000,486 | 126.6 MB |

**Novelty seed** (`known_signal_hashes`, a full JSONL parse once per cycle):

| records | jsonl size | parse |
|---|---|---|
| 10,000 | 8.6 MB | 0.08s |
| 100,000 | 85.6 MB | 0.81s |
| 500,000 | 428.0 MB | **4.28s** |

Both scale linearly. At the live rate measured 2026-10-06 (**1,249 records/day**),
500,000 records is **~365 days** of operation. That is the number that argues against
phases 3 and 4: the costs they remove are seconds per day after a year.

`ON DELETE CASCADE` on `record_entities` was also confirmed to fire -- deleting a
parent record removed its link rows -- which depends on `connect()` setting
`foreign_keys = ON`, and on `DualWriteStore` opening through it. Both hold.

## 5. Local setup, migration, rollback

```bash
pip install -e ".[dev]"
export PDX1_DB_PATH=pdx1.db      # already documented in .env.example
python -m pdx1 ...               # opening the store applies pending migrations
pytest tests/ -q && ruff check src/ tests/ && bandit -r src/ -ll
```

Upgrade is automatic on first open. Rollback: migrations are forward-only; because SQLite is a query layer, delete the database file (or restore a copy) and run `rebuild_from_jsonl()`, using the previous release. To drop only the new table: `DROP TABLE record_entities; DELETE FROM schema_migrations WHERE version = 2;`.

## 6. Phased rollout — revised 2026-10-07 against measurement

Phases 1 and 1a are done. **Everything below them is now recommended against**, on the
evidence in §8. Each was measured rather than estimated, and none of it moves the
thirty-day count, which is still 0.

1. **Done:** DB module, versioned migrations, `record_entities`.
1a. **Done 2026-10-07:** migrations made genuinely atomic, backfill guarded with
    `json_valid`, orphaned `schema/civic_records.sql` deleted. See §4.
2. **Optional, not required.** Move `entity_record_counts` and `records_for_entity`
   onto `record_entities` with a parity test against the JSON column. A performance
   fix for queries over ~1,249 records a day; do it when something feels slow, not
   before. The link table is already in place for it.
3. **Recommended against.** A `runs` table mirroring the ledger. The ledger is the
   evidence for the count (rule 7: every cycle leaves one line, and a missing line is
   what the alert looks for). A SQL mirror adds a second place that can disagree with
   the one artifact the definition of done rests on, and serves no query anyone makes.
4. **Recommended against, two parts.**
   - `entities`/`entity_ties` persisted from `graph.py`: `graph.py` is the
     authoritative role-based registry and is how rule 2 is kept. A copy in the
     database is a second home for the constraint, and at query time the copy wins.
   - Novelty hashes in SQL so `known_signal_hashes` stops scanning JSONL: measured in
     §8 at ~4s once a day after a *year* of operation. The docstring states why it
     reads ground truth -- "so novelty survives a database rebuild". Trading a
     correctness property of the gate that decides what publishes for four seconds a
     day is the wrong trade.
5. **Stay deferred.** Postgres behind `db.connect`, if concurrent writers or hosted
   querying ever appear. The concentration of SQL in `store.py` and `db.py` is what
   keeps that a driver swap; nothing more is needed now.

## 7. Risks

- JSONL/SQLite parity: unchanged, mitigated by write order and rebuild. `record_entities` is derived, so it is rebuilt with the rest.
- `json_each` needs SQLite's JSON1 (built in on current Python builds).
- ~~`executescript` commits implicitly, so migrations must stay idempotent.~~ **Closed
  2026-10-07.** Migrations now execute statement-wise inside the transaction, so the
  rollback is real; they are still written to be idempotent so an unversioned legacy
  database can be adopted.
- ~~Unverified: backfill time for migration 2 on a very large database.~~ **Measured
  2026-10-07, see §8.** 4.50s at 500,000 records -- over a year of operation at the
  observed rate. Not a risk.
- Public records only: no new table may hold private addresses or personal identifiers.
- **Still open:** the migration runner has no test for a *partially* written database
  (a crash between two statements of the same migration). Atomicity makes that case
  impossible in principle; it is untested in fact.
