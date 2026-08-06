# PDX-1i — working notes for agents

Read this before changing code. It records the constraints that are not obvious from
the source, and the ones that are enforced in CI rather than by review.

## What this engine is

An OSINT engine for Portland-area politics and civic infrastructure, covering the
bi-state metro. It harvests public records, scores them through a four-gate
deterministic filter, resolves the entities they name, measures them against a rolling
baseline, and writes structured intelligence records — then assembles a
neutrality-gated brief when publication triggers.

`README.md` is the user-facing guide and is kept accurate; prefer it for behaviour.
This file is about *how to work on the repo* without breaking its guarantees.

## The rules that are not negotiable

These are not style preferences. Each one exists because breaking it changes what the
engine publishes about real people and institutions.

1. **Descriptive, not prosecutorial.** The engine reports structure and timing. It
   does not allege, and a tie in the graph is not a finding. Three gates enforce this
   in code — tone, hedging, attribution — and they are the reason the project can make
   that claim honestly. Do not weaken a gate to make a section publish.
2. **Officials are role-based seats, never named individuals.** `graph.py` holds
   "Metro Councilor · D2", not a person. A seat can be described structurally without
   characterising whoever holds it. The one deliberate exception is the static dataset
   in `ui/citizen-cognisance.html`, which the README documents as confined; the
   role-based registry remains authoritative for anything the engine publishes.
3. **Anomalies are measurements, never adjectives.** "3.0 sigma against a 90-day
   baseline of 5.00 (sd 2.00, n=8)", not "unusually high".
4. **Every record traces to the run that produced it.** `run_id` is on every record.
   This is a publication requirement, not bookkeeping.
5. **JSONL is ground truth; SQLite is a query layer.** Writes go to the append-only
   file first (flushed and fsynced), then to the database. Never reverse that order —
   the failure worth avoiding is SQLite holding a record ground truth never saw.
   `rebuild_from_jsonl()` exists because the reverse is recoverable.
6. **A dead feed must never halt a cycle.** Adapters are independently fault-tolerant;
   `safe_fetch` converts any failure into an error on the result. The cycle still
   completes and still writes.

## Layout

```
src/pdx1/            43 modules
  config.py          settings; every PDX1_* key is documented in .env.example
  models.py          Pydantic schemas — Signal → IntelligenceRecord
  gates.py           the four-gate filter (credibility, volume, velocity, novelty)
  resolver.py        EntityResolver — exact, token-sort, substring
  anomaly.py         RollingBaseline — 90-day rolling sigma
  trigger.py         TriggerState — weight | TIER_1 | floor cadence
  store.py           dual-write JSONL + SQLite
  graph.py           jurisdictions, seats, entities, ties (data only)
  pipeline.py        stage orchestration + CLI
  __main__.py        `python -m pdx1` entry point
  scheduler.py       APScheduler cron — daily cycle, default 06:00 PT
  sources/           ORESTAR · OLIS · SEI · WA PDC · Portland Press
    base.py          adapter contract + three-tier live read
    normalize.py     money/date/header coercions shared by the adapters
  watch/             6 infrastructure monitors, declared in targets.py
  neutrality/        tone · hedging · attribution gates
  publication/       IssueBuilder · BriefPublisher · PDF renderer
  api/               FastAPI app, routes (incl. /graph), API-key auth
  demos/             runnable walkthrough
ui/                  index.html · webmap.html · citizen-cognisance.html · DESIGN.md
tests/               24 files, 449 tests
  fixtures/          source payloads replayed by the adapters
```

## Fixture replay vs live fetch

Adapters default to replaying checked-in fixtures, so a cycle is reproducible and CI
needs no connectivity. `PDX1_LIVE=true` (with the `live` extra) switches to HTTP.

A live read resolves in three tiers: **fixture_path**, then **live HTTP** (which writes
a last-good cache), then **that cache** when the fetch fails. The third tier is why an
outage costs freshness rather than the whole source. It does not weaken the velocity
gate — a cached payload carries its original timestamps, so stale records still drop.

**Field mappings are only partly verified.** The four record feeds map real payload
shapes through alias tables (`_COLUMN_ALIASES` in `orestar.py`, `_FIELD_ALIASES`
elsewhere), and each table carries a comment saying how far it has been confirmed. The
endpoints are unreachable from the sandboxes this was developed in, so the *mapping
logic* is tested and the *field names* are not. If you can reach the real endpoints,
verify the names and update the comment to say so.

Two feeds are special cases worth knowing before you touch them:

- **SEI has no API.** OGEC publishes downloads from a landing page, so `feed_url` is
  that page and a live fetch of it returns HTML. `parse` raises on non-JSON rather than
  returning `[]` — an empty list would read as "no official filed anything", which is a
  false statement about public records. Live SEI means `fixture_path` on a download.
- **Portland Press needs no mapping.** RSS is standard and `feedparser` handles a real
  feed already. It polls all five tracked feeds; one dead outlet is logged and skipped,
  and only a total failure falls through to the cache.

Alias resolution reads `union_keys(rows)`, not `rows[0].keys()`. Exports omit empty
optional columns per row, so reading the first row alone drops that field for *every*
record — a whole feed silently emptied by whichever row sorted first.

Because fixtures carry fixed dates, `run_cycle` anchors the velocity gate to the newest
harvested signal rather than wall-clock time. Override with `--as-of`. Live runs should
pass the real clock.

## Working on it

```bash
pip install -e ".[dev]"        # Python 3.12+ required
pytest tests/ -q
ruff check src/ tests/
bandit -r src/ -ll
```

All four are hard gates in CI (`.github/workflows/python-package.yml`, Python 3.12).
None may fail soft. The workflow runs on PRs into **any** base branch — a PR once
merged without ever being tested because the filter only matched `main`.

Conventions worth matching:

- **Tests use `tmp_path`; nothing touches the network.** Live behaviour is tested by
  patching `httpx.get` with constructed responses. Adapters do not write to disk unless
  a `cache_dir` is passed, so a bare adapter in a test has no side effects.
- **The suite leans on boundary conditions** — a signal at exactly 0.5 credibility,
  exactly 50 words, exactly 48 hours old — because an off-by-one in a gate silently
  changes what the engine publishes.
- **`parse` is pure.** I/O belongs in `_read_raw` / `_fetch_live`, so parsing is
  directly testable against a string.
- An unreadable date returns `None` and the record is dropped. Never default it to
  now: that makes an undated record look fresh and slips it past the velocity gate.

## Data sensitivity

**Public records only.** Campaign finance, legislative records, statements of economic
interest, and public agency communications — all of them already-published disclosures.
No private addresses, no personal identifiers, no confidential business information.

The engine's output is about institutions and seats. That is a design constraint, not
an incidental property, and rule 2 above is how it is kept.

## Before you commit

1. Check `git branch --show-current` — work on the assigned branch, never push to
   `main` directly.
2. Run the four CI commands locally. A green suite is the minimum, not the goal.
3. If you changed what the engine publishes — a gate, a threshold, a mapping — say so
   plainly in the commit message, including what you could not verify.

Do not rename `ConfidenceTier`, `Outcome`, `AnomalyTier` or `TieKind` values. They are
serialised into JSONL ground truth and into the API surface; a rename silently
invalidates stored records.
