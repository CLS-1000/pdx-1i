# PDX-1i — Portland Metro Intelligence

Open-source intelligence (OSINT) engine for Portland-area politics and civic
infrastructure, covering the bi-state metro: Multnomah, Washington and Clackamas
counties in Oregon, and Clark County in Washington.

PDX-1i is the regional module of the SPEC-1 architecture. It harvests public records,
scores them through a four-gate deterministic filter, resolves the entities they name,
measures them against a rolling baseline, and writes structured intelligence records —
then assembles a neutrality-gated brief when publication triggers.

## What it does, and what it refuses to do

The engine reports **structure and timing**. It makes conflict-of-interest structure
visible and legible; it does not allege anything, and a tie in the graph is not a
finding.

Three constraints are enforced in code rather than left to editorial discipline:

- **Descriptive, not prosecutorial.** The tone gate (`src/pdx1/neutrality/tone.py`)
  rejects prosecutorial vocabulary, motive attribution, and loaded framing before a
  section can be published.
- **Every claim traces to a record.** The attribution gate rejects any section that
  cites nothing, cites a record the engine does not hold, or uses vague sourcing.
- **Officials are role-based seats**, never named individuals — "Metro Councilor · D2",
  not a person. A seat can be described structurally without characterising whoever
  holds it.

Anomalies are reported as measurements, never adjectives: "3.0 sigma against a 90-day
baseline of 5.00 (sd 2.00, n=8)", not "unusually high".

## The pipeline

Seven stages, run in sequence. Each is independently fault-tolerant — a dead feed is
recorded and skipped, and the cycle still completes and still writes.

```
01 Harvest      adapters pull raw payloads
02 Parse        clean text, extract registry entities
03 Score        four-gate filter + composite score
04 Investigate  generate a hypothesis for surviving opportunities
05 Verify       measure against the rolling baseline
06 Analyze      assign outcome, priority, confidence tier
07 Store        dual-write JSONL + SQLite, then assemble a brief if triggered
```

### The four gates

Every signal clears all four or it does not survive. No partial credit, no weighted
override. Thresholds are inclusive and configurable via `.env`.

| Gate | Criterion | Default |
|---|---|---|
| Credibility | source weight | ≥ 0.5 |
| Volume | word count | ≥ 50 words |
| Velocity | recency | ≤ 48 hours |
| Novelty | content-hash dedup | not previously seen |

Novelty is seeded from the store at the start of each cycle, so content republished
across cycles is still recognised as a duplicate.

### Confidence tiers

| Tier | Meaning |
|---|---|
| `HARD_RECORD` | a filed public record states it — ORESTAR, OLIS, SEI, WA PDC |
| `REPORTED` | a published source reports it — press feeds |
| `INFERRED` | the engine derived it by correlating records |

## Installation

```bash
git clone https://github.com/cls-1000/pdx-1i.git
cd pdx-1i
pip install -e ".[dev]"
```

**Requires Python 3.12+.** The engine itself needs only `pydantic`, `feedparser` and
`python-dotenv`; storage uses the standard library's `sqlite3`. Extras (`live`, `api`,
`pdf`, `llm`) cover capabilities that are not built yet — see *Not built yet* below.

## Quick start

```bash
# Run one full cycle over the checked-in fixtures
python -m pdx1.pipeline

# Or, installed as a console script
pdx1

# See every stage's work — what each adapter returned, which gate dropped what
python -m pdx1.demos.walkthrough
```

Output lands in `pdx1_signals.jsonl` (ground truth) and `pdx1.db` (query layer).

```python
from pdx1.sources.portland_press import PortlandPressAdapter

press = PortlandPressAdapter(fixture_path="tests/fixtures/portland_press.xml")
result = press.safe_fetch()
print(len(result), "signals", "ok" if result.ok else result.errors)
```

Adapters currently replay checked-in fixtures rather than fetching over the network, so
a cycle is reproducible and CI needs no connectivity. Because the fixtures carry fixed
dates, `run_cycle` anchors the velocity gate to the **newest harvested signal** rather
than wall-clock time — otherwise a replay would drop everything on velocity as the
fixtures age. Override with `--as-of`:

```bash
python -m pdx1.pipeline --as-of 2026-05-28T12:00:00+00:00
```

Live adapters should pass the real clock.

## Storage

JSONL is append-only and authoritative. SQLite exists to answer questions quickly and
can be rebuilt from the JSONL at any time.

There is no transaction spanning a file append and a database commit, and the module
does not pretend otherwise. Writes go to JSONL first (flushed and fsynced), then to
SQLite. If the second step fails, ground truth still holds the record and
`rebuild_from_jsonl()` restores the database. The reverse order could leave SQLite
holding a record ground truth never saw, which is the failure worth avoiding.

Writes are idempotent — re-running a cycle over the same input adds nothing.

## Repository layout

```
pdx-1i/
├── src/pdx1/                  25 modules
│   ├── config.py              settings; every PDX1_* key in .env.example
│   ├── models.py              Pydantic schemas — Signal → IntelligenceRecord
│   ├── gates.py               the four-gate filter
│   ├── resolver.py            EntityResolver — exact, token-sort, substring
│   ├── anomaly.py             RollingBaseline — 90-day rolling sigma
│   ├── trigger.py             TriggerState — weight | TIER_1 | floor cadence
│   ├── store.py               dual-write JSONL + SQLite
│   ├── graph.py               jurisdictions, seats, entities, ties
│   ├── pipeline.py            stage orchestration + CLI
│   ├── sources/               ORESTAR · OLIS · SEI · WA PDC · Portland Press
│   ├── neutrality/            tone gate · attribution gate
│   ├── publication/           IssueBuilder + BriefPublisher
│   ├── watch/                 WatchTarget — infrastructure watch records
│   └── demos/                 runnable walkthrough
├── tests/                     11 test files, 226 tests
│   └── fixtures/              source payloads replayed by the adapters
├── .github/workflows/         CI — ruff, bandit, pytest, coverage (Python 3.12)
└── pyproject.toml
```

## Data sources

| Source | Type | Adapter | Credibility |
|---|---|---|---|
| **ORESTAR** | OR campaign finance | `OrestarAdapter` | 0.90 |
| **OLIS** | OR legislation, hearings, markup timing | `OlisAdapter` | 0.90 |
| **SEI** | OR statements of economic interest (OGEC) | `SeiAdapter` | 0.85 |
| **WA PDC** | WA cross-border contributions | `WaPdcAdapter` | 0.85 |
| **Portland Press** | Local news RSS, 5 feeds | `PortlandPressAdapter` | 0.60 |

Filed records outrank press on credibility because a filing is the primary artifact;
press establishes that something was reported, not that it happened.

## The political web

`graph.py` holds the node and tie registry: 8 jurisdictions, 10 official seats, 13
monitored entities.

| Node | Meaning |
|---|---|
| Jurisdiction | a governing body — Metro, the counties, City of Portland, TriMet Board, the Port |
| Official | a seat, connected to the body it sits on |
| Entity | a utility, agency or private organisation the system tracks |

| Tie | Meaning |
|---|---|
| `seat` | an official occupies a seat on a jurisdiction |
| `tie` | a general affiliation |
| `regulates` | a jurisdiction sets rules or rates over an entity |
| `operates` | a jurisdiction runs or directly controls the entity |
| `disclosure` | a declared interest linking an official to an entity |

A disclosure is a completed legal obligation, not a finding. `validate()` runs in CI to
catch dangling ties — a record linked to a node that does not exist must never reach
publication.

## Testing

```bash
pytest tests/ -v
pytest --cov=src --cov-report=term-missing tests/
ruff check src/ tests/
```

226 tests. The suite leans on boundary conditions — a signal at exactly 0.5
credibility, exactly 50 words, exactly 48 hours old — because an off-by-one in a gate
silently changes what the engine publishes.

## Configuration

Copy `.env.example` to `.env`. Every key in its first half is read by
`pdx1.config.Settings`; keys below the divider belong to surfaces that do not exist yet
and are commented out.

## Not built yet

Deliberately out of scope for the current engine, each a clean follow-on:

- **Live HTTP fetching** — adapters replay fixtures. Live fetch is a thin layer over
  the same `parse` methods; only `_read_raw` changes.
- **FastAPI surface** — `GET /signals /intel /leads /brief`, `POST /cycle/run`.
- **Scheduler** — the daily 06:00 PT cycle.
- **PDF newsletters** and network diagrams.
- **`watch/` infrastructure monitors** — OHSU, PPB, TriMet, PGE, NW Natural, Water
  Bureau. `WatchTarget` records a name and endpoint, and these bodies exist in the graph
  as monitored entities, but nothing polls them yet.
- **Web UI** — the SPEC-1 console and political surfaces. Should be built against a
  real API, not fixtures.

## License

MIT — see LICENSE.

## Contact

CLS-1000 — Portland Metro Intelligence Project
https://github.com/cls-1000/pdx-1i

---

*PDX-1i is the regional module of the SPEC-1 OSINT architecture.*
