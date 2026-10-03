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
| `schema/civic_records.sql` | repo | not wired in | none | Unused proposal (sources, raw_payloads, normalized_records ...) |

Findings:

- **Single owner of SQL.** All SQLite access is in `DualWriteStore`; there is no second writer, which makes centralising cheap.
- **Schema management was ad hoc.** `CREATE TABLE IF NOT EXISTS` plus a hand-written `ALTER` in `_migrate`; no version record.
- **JSON-in-column.** `entity_ids` was a JSON array, so entity lookups (`entity_ids_for_records`, `entity_record_counts`, `records_for_entity`) scanned and decoded every row.
- **Drift risk.** `schema/civic_records.sql` describes a different, unused model from the live tables.
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

## 5. Local setup, migration, rollback

```bash
pip install -e ".[dev]"
export PDX1_DB_PATH=pdx1.db      # already documented in .env.example
python -m pdx1 ...               # opening the store applies pending migrations
pytest tests/ -q && ruff check src/ tests/ && bandit -r src/ -ll
```

Upgrade is automatic on first open. Rollback: migrations are forward-only; because SQLite is a query layer, delete the database file (or restore a copy) and run `rebuild_from_jsonl()`, using the previous release. To drop only the new table: `DROP TABLE record_entities; DELETE FROM schema_migrations WHERE version = 2;`.

## 6. Phased rollout

1. **Done (this change):** DB module, versioned migrations, `record_entities`.
2. Move `entity_record_counts` and `records_for_entity` onto `record_entities`; add a parity test against the JSON column.
3. Add `runs` table mirrored from the run ledger (JSONL stays authoritative); extend `rebuild_from_jsonl`.
4. Persist `entities`/`ties` from `graph.py`; reconcile or delete `schema/civic_records.sql`; consider storing hashes so `known_signal_hashes` need not scan JSONL.
5. Optional: Postgres behind `db.connect` if concurrent writers or hosted querying appear. Dual-write, compare, then cut over.

## 7. Risks

- JSONL/SQLite parity: unchanged, mitigated by write order and rebuild. `record_entities` is derived, so it is rebuilt with the rest.
- `json_each` needs SQLite's JSON1 (built in on current Python builds).
- `executescript` commits implicitly, so migrations must stay idempotent.
- Public records only: no new table may hold private addresses or personal identifiers.
- Unverified here: behaviour on a very large production database (backfill time for migration 2).
