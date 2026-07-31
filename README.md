# PDX-1i — Portland Metro Intelligence

Open-source intelligence (OSINT) engine for Portland-area politics and civic
infrastructure, covering the bi-state metro: Multnomah, Washington and Clackamas
counties in Oregon, and Clark County in Washington.

PDX-1i is the regional module of the SPEC-1 architecture. It harvests public records,
scores them through a four-gate deterministic filter, resolves the entities they name,
measures them against a rolling baseline, and writes structured intelligence records —
then assembles a neutrality-gated brief when publication triggers.

Around that core sit four surfaces: an HTTP API, a cron scheduler for the daily cycle,
a PDF renderer for the brief, and a single-page brief viewer. What is *not* built is
listed at the bottom — the force-directed web map is the notable gap.

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

**Requires Python 3.12+.** The core engine needs only `pydantic`, `feedparser` and
`python-dotenv`; storage uses the standard library's `sqlite3`. Optional extras add the
surfaces built on top of it:

| Extra | Adds |
|---|---|
| `live` | HTTP fetching for the adapters and watch monitors (`httpx`, `requests`, `bs4`, `lxml`) |
| `api` | the FastAPI surface and the APScheduler cron (`fastapi`, `uvicorn`, `apscheduler`) |
| `pdf` | PDF brief output (`reportlab`) |
| `llm` | optional written explanations. Not used by scoring, which stays deterministic |

`pip install -e ".[dev]"` pulls the extras needed to run the full test suite.

## Quick start

```bash
# Run one full cycle over the checked-in fixtures
python -m pdx1.pipeline          # or: pdx1

# Serve the API on :8000
pdx1-api                          # or: python -m pdx1.api.app

# Run the daily cycle on a cron schedule (default 06:00 PT)
pdx1-scheduler

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

### Fixture replay vs live fetch

Adapters default to replaying checked-in fixtures, so a cycle is reproducible and CI
needs no connectivity. Set `PDX1_LIVE=true` (and install the `live` extra) to fetch over
HTTP instead: `LiveSourceAdapter` overrides only `_read_raw`, so the parse logic is the
same either way.

> **Live mode is a transport, not a working integration.** The HTTP plumbing is real and
> tested, but two things stand between it and live data, and neither is done:
>
> 1. **The parsers expect the fixtures' schema.** Every `parse` requires the exact keys
>    the fixtures use — `tran_id`, `filed_at`, `contributor_city`. A real government
>    export uses different field names and raises `KeyError` on the first record. Each
>    adapter needs a field mapping written against its actual feed.
> 2. **The `feed_url` values are unverified.** They are plausible-looking guesses, and
>    the only test covering them asserts that the string is non-empty and starts with
>    `https://` — which cannot fail for a wrong URL.
>
> The live tests mock `httpx.get` and hand back fixture content, so they prove the
> request is made with the right URL and timeout. They do not prove that anything on the
> other end parses. Treat `PDX1_LIVE=true` as unimplemented until the mappings land.

Because the fixtures carry fixed dates, `run_cycle` anchors the velocity gate to the
**newest harvested signal** rather than wall-clock time — otherwise a replay would drop
everything on velocity as the fixtures age. Override with `--as-of`:

```bash
python -m pdx1.pipeline --as-of 2026-05-28T12:00:00+00:00
```

Live runs should pass the real clock.

## HTTP API

`pdx1-api` serves the store over HTTP.

| Endpoint | Returns |
|---|---|
| `GET /health` | liveness probe |
| `GET /signals` | harvested signals, paginated |
| `GET /intel` | intelligence records, filterable by outcome and source |
| `GET /leads` | records at elevated disposition |
| `GET /brief` | the most recent assembled brief (404 until a cycle has run) |
| `POST /cycle/run` | drives a full cycle and returns its summary |

Set `PDX1_API_KEY` to require an `X-API-Key` header on every request; leave it blank and
auth is bypassed, which is appropriate for local use only. `PDX1_CORS_ORIGINS` controls
the allowed origins.

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
├── src/pdx1/                  39 modules
│   ├── config.py              settings; every PDX1_* key in .env.example
│   ├── models.py              Pydantic schemas — Signal → IntelligenceRecord
│   ├── gates.py               the four-gate filter
│   ├── resolver.py            EntityResolver — exact, token-sort, substring
│   ├── anomaly.py             RollingBaseline — 90-day rolling sigma
│   ├── trigger.py             TriggerState — weight | TIER_1 | floor cadence
│   ├── store.py               dual-write JSONL + SQLite
│   ├── graph.py               jurisdictions, seats, entities, ties (data only)
│   ├── pipeline.py            stage orchestration + CLI
│   ├── scheduler.py           APScheduler cron — daily cycle, default 06:00 PT
│   ├── sources/               ORESTAR · OLIS · SEI · WA PDC · Portland Press
│   ├── watch/                 6 infrastructure monitors
│   ├── neutrality/            tone gate · attribution gate
│   ├── publication/           IssueBuilder · BriefPublisher · PDF renderer
│   ├── api/                   FastAPI app, routes, API-key auth
│   └── demos/                 runnable walkthrough
├── ui/index.html              single-page brief viewer (see UI below)
├── tests/                     16 test files, 271 tests
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

**The registry is data only.** `graph.py` holds 31 nodes and 40 ties, and the resolver
uses them to attach `entity_ids` to records — but nothing renders the graph and no
endpoint serves it. The force-directed web map is not built; see *Not built yet*.

## Testing

```bash
pytest tests/ -v
pytest --cov=src --cov-report=term-missing tests/
ruff check src/ tests/
bandit -r src/ -ll
```

271 tests. The suite leans on boundary conditions — a signal at exactly 0.5
credibility, exactly 50 words, exactly 48 hours old — because an off-by-one in a gate
silently changes what the engine publishes.

All four commands are hard gates in CI. None of them are allowed to fail soft.

## Configuration

Copy `.env.example` to `.env`. Every key is read by `pdx1.config.Settings` except the
`ANTHROPIC_API_KEY` / `PDX1_LLM_MODEL` pair, which is reserved for optional written
explanations and is not consumed by scoring.

## UI

`ui/index.html` is a single self-contained page that reads the API and renders the
current brief. It is a brief viewer, not the SPEC-1 console — it does not implement the
five political-intelligence panels (Overview, District Map, Web Map, Signal Feed,
Statistics), and its palette does not follow the SPEC-1 monochrome design system.

## Not built yet

Each of these is a clean follow-on. What is listed here is genuinely absent — if a
capability is described anywhere above, it exists and has tests.

- **The web map.** The force-directed political-web diagram is the largest gap. The
  data is ready (`graph.py`: 31 nodes, 40 ties, five tie kinds) and the resolver already
  attaches `entity_ids` to every record, but nothing serves that graph and nothing draws
  it. Needs two pieces: a `GET /graph` endpoint emitting nodes and ties, and a canvas or
  SVG renderer in the UI. Node shape encodes type (jurisdiction, official seat, entity),
  line style encodes tie kind, and disclosure ties render dashed.
- **Working live fetch.** The HTTP transport exists; the field mappings do not. Each
  adapter's `parse` needs rewriting against its real feed's schema, and each `feed_url`
  needs verifying against the actual endpoint. See *Fixture replay vs live fetch*.
- **Network diagrams in the PDF.** `render_brief_pdf` emits text — headings, paragraphs
  and tables. No diagram is drawn.
- **The remaining SPEC-1 panels** — District Map over real projected GIS, Signal Feed
  with per-record four-gate expansion, Statistics. All depend on graph and record
  endpoints that mostly exist; the work is front-end.
- **SPEC-1 visual language for the UI** — monochrome `#000` canvas, hierarchy by white
  opacity ramp, severity by brightness rather than hue, `#00FF00`/`#FF0000` reserved for
  live status only.

## License

MIT — see LICENSE.

## Contact

CLS-1000 — Portland Metro Intelligence Project
https://github.com/cls-1000/pdx-1i

---

*PDX-1i is the regional module of the SPEC-1 OSINT architecture.*
