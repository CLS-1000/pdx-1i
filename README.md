# PDX-1i — Portland Metro Intelligence

Open-source intelligence (OSINT) engine for Portland-area politics and civic
infrastructure, covering the bi-state metro: Multnomah, Washington and Clackamas
counties in Oregon, and Clark County in Washington.

PDX-1i is the regional module of the SPEC-1 architecture. It harvests public records,
scores them through a four-gate deterministic filter, resolves the entities they name,
measures them against a rolling baseline, and writes structured intelligence records —
then assembles a neutrality-gated brief when publication triggers.

Around that core sit four surfaces: an HTTP API, a cron scheduler for the daily cycle,
a PDF renderer for the brief, and two single-page viewers — the daily brief and the
force-directed political web. What is *not* built is listed at the bottom.

---

## Executive Summary

### What it was

PDX-1i began as a research prototype: a set of harvesting scripts pointed at
Portland-metro public-records endpoints, producing flat files that had to be read
manually. There was no scoring logic, no entity resolution, no anomaly detection, and
no publication pipeline. The outputs were raw and unannotated — useful only to someone
who already knew what to look for. The UI did not exist. The record of what had been
fetched lived only on whoever's laptop ran the scripts.

### What it is

A deterministic, reproducible OSINT engine covering the bi-state Portland metro
(Multnomah, Washington, Clackamas, Clark). It ingests five public-record feeds —
ORESTAR, OLIS, SEI, WA PDC, and Portland Press — runs every signal through a
four-gate filter (credibility, volume, velocity, novelty), resolves named entities
against a role-based registry of jurisdictions and seats, and measures each surviving
signal against a 90-day rolling baseline. Anomalies are reported as sigma measurements,
not adjectives. Every published line traces to the run that produced it.

A neutrality layer applies before publication: attribution is a hard gate (a section
that cites nothing does not publish); tone and hedging are observation-only (flagged and
carried into the store, not suppressed — because the check cannot distinguish a
newspaper reporting a conviction from the engine alleging one, and suppression is worse
than annotation).

The outputs are a structured JSONL ground-truth store, a queryable SQLite layer, a
daily PDF brief, a JSON API, and two single-page viewers: a brief reader and a
force-directed political web. A daily cron cycle drives the whole thing. Six
infrastructure-watch monitors run alongside the record feeds and feed the same pipeline.

As of the current build, the transport, scoring, storage, and publication machinery
are fully exercised. Live connectivity is partial: OLIS, two press feeds, and one watch
target answer; the remaining feed URLs are documented 404s, and no record feed has yet
returned a row that confirmed its field-alias table against real data.

### What it will be

Three bodies of work remain, in priority order:

1. **Feed verification.** Correct the dead endpoint URLs and confirm each adapter's
   field-alias table against a real payload. This is data entry and one alias-table
   pass per feed — no new machinery required. Until it is done, the engine publishes
   only press records and watch events.

2. **Front-end completion.** Three SPEC-1 panels are absent: District Map (projected
   GIS), Signal Feed (per-record four-gate expansion), and Statistics. The API
   endpoints they depend on exist; the work is front-end. The visual language also
   needs to converge on the SPEC-1 monochrome design system (black canvas, white
   opacity hierarchy, hue reserved for live-status only).

3. **Network diagrams in the PDF brief.** The renderer currently emits text only —
   headings, paragraphs, tables. Structural diagrams of the political web are the
   natural next addition once the data layer is verified.

None of these require changes to the scoring logic, the gate thresholds, the neutrality
layer, or the publication trigger. The engine's guarantees — traceability, role-based
attribution, sigma-scaled anomaly reporting, fault-tolerant cycle — are stable.

---

## What it does, and what it refuses to do

The engine reports **structure and timing**. It makes conflict-of-interest structure
visible and legible; it does not allege anything, and a tie in the graph is not a
finding.

Two constraints are enforced in code — a section that breaks either does not publish:

- **Every claim traces to a record.** The attribution gate rejects any section that
  cites nothing, cites a record the engine does not hold, or uses vague sourcing.
- **Officials are role-based seats**, never named individuals — "Metro Councilor · D2",
  not a person. A seat can be described structurally without characterising whoever
  holds it.

Two more are **observed and recorded, not enforced.** They annotate; they do not
withhold:

- **Descriptive, not prosecutorial.** The tone check
  (`src/pdx1/neutrality/tone.py`) matches prosecutorial vocabulary, motive attribution
  and loaded framing.
- **No implication without a claim.** The hedging check
  (`src/pdx1/neutrality/hedging.py`) matches prose that characterises by insinuation —
  "raises questions", "appears to", "clearly". Such a sentence asserts nothing, so
  neither of the other checks can see it.

Both attach an `observation` to the published section, carried into the store:

```json
{"gate": "tone_gate", "rule": "observation_only", "severity": "info",
 "matched_terms": ["fraud", "fraudulent", "guilty"],
 "note": "Prosecutorial or subjective vocabulary detected in source text."}
```

**This is a deliberate trade, and it is worth understanding before relying on the
engine's neutrality.** These two were gates until a live run showed the flaw: they scan
the assembled section body, and a record's `pattern` carries harvested source text into
it — so a newspaper reporting that someone pleaded guilty to fraud tripped exactly what
the engine alleging fraud would. The check cannot tell those apart, and withholding the
section suppressed the report in order to prevent the accusation.

What is given up: nothing in code now stops prosecutorial or insinuating language
reaching a reader. Editorial judgement sits with whoever reads the observations. The
engine still *detects* everything it detected before, and says so on the record.

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
pdx1                              # or: python -m pdx1, or python -m pdx1.pipeline

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

`PDX1_SOURCE_MODE` selects which, and it has **no default**:

| Value | What it does |
|---|---|
| `fixture` | replays the checked-in payloads; reproducible, and reaches no network |
| `live` | reads the real endpoints and adds the watch monitors; needs the `live` extra |
| anything else, or unset | **refuses to start** |

There is no third behaviour and no fallback. Both modes run the same code and both end
in a publishable brief, so an operator who believes they are running live and is not
would see a plausible brief assembled from a frozen May 2026 snapshot with nothing in
the output to contradict it. That is the failure the missing default exists to prevent:
an unreadable setting stops the process instead of picking a side. The CLI reports the
mode it resolved on its first summary line, and the API logs it at startup.

The removed `PDX1_LIVE` key is refused rather than ignored — a stale `PDX1_LIVE=true`
that was silently dropped would be exactly the situation above.

The two modes also anchor the velocity gate differently when `--as-of` is not given. A
fixture replay anchors to the newest harvested signal, because the payloads carry fixed
dates and wall-clock would age them out of every run. A live cycle anchors to wall
clock, because under replay-anchoring a feed that stopped updating months ago would
present its last batch as fresh — the one thing the velocity gate exists to prevent.

A live read resolves in three tiers, in order:

| Tier | Source | When |
|---|---|---|
| 1 | `fixture_path` | an explicit local payload; wins over everything |
| 2 | live HTTP | the registered `feed_url`; writes a last-good cache on success |
| 3 | last-good cache | the previous successful body, when the live fetch fails |

Tier 3 is why a cycle survives a feed outage with real data rather than none. It does
not weaken the velocity gate: a cached payload carries its original timestamps, so
stale records are dropped downstream exactly as they would be if the feed had served
them. The cache makes an outage non-fatal; it does not make old records publishable.
Set the location with `PDX1_CACHE_DIR`.

Every adapter now reads its real payload shape, and each feed needed something
different:

| Adapter | Live shape |
|---|---|
| **ORESTAR** | the Secretary of State bulk transaction export — a ZIP containing one CSV, unwrapped by `_decode`. Published per calendar year, so `feed_url` carries a `{year}` the adapter resolves at construction. |
| **OLIS** | the OData service — rows under `value`, paged via `@odata.nextLink`. |
| **WA PDC** | a Socrata dataset on `data.wa.gov`, paged with `$limit`/`$offset` and sorted newest-first. Washington's disclosure regime exposes a real API where Oregon's does not. |
| **SEI** | **no API exists.** OGEC publishes periodic downloads from a landing page, so live mode here means pointing `fixture_path` at an export. `parse` accepts JSON, JSONL or a wrapper object, and rejects HTML loudly rather than returning nothing. |
| **Portland Press** | RSS, which needed no mapping — `feedparser` reads a real feed the same way it reads the fixture. What it needed was *all five* tracked feeds; live mode previously polled only OregonLive. |

The four record feeds map field names through an alias table, so correcting a name is a
one-line change in one place, and a name matching nothing leaves its field empty and
logs rather than raising. Alias resolution reads the union of every row's keys, because
exports omit empty optional columns per row and reading only the first row would drop a
field for every record on the strength of whichever sorted first.

#### What a live run actually reached

`PDX1_SOURCE_MODE=live` was run against the real endpoints on **2026-08-21**. Every
adapter, its HTTP result, what it returned and how long it took:

| Adapter | HTTP | Items | Elapsed | Note |
|---|---|---|---|---|
| **OLIS** | 200 × 7 pages | **31,405** | 21.0 s | 31,405 measures, all dated; first live rows this project has parsed |
| **WA PDC** | 200 × 50 pages | **50,000** | 97.9 s | stops at the 50-page ceiling; 6,354,167 rows exist |
| **Portland Press** | 200, 404, 200, SSL, 404 | **60** | 4.1 s | OregonLive + KOIN answered; three outlets did not |
| **ORESTAR** | 404 | 0 | 1.3 s | bulk export path still wrong |
| **SEI** | 200 HTML | 0 | 0.7 s | landing page, rejected by `parse` as designed |
| WATCH/TriMet | 200 | 10 | 1.2 s | |
| WATCH/OHSU · NW Natural | 404 | 0 | | |
| WATCH/PPB · Water Bureau | 403 | 0 | | portland.gov refuses the client outright |
| WATCH/PGE | connect failure | 0 | | |

The whole cycle: **81,475 harvested → 91 written**, three brief sections, three records
at elevated disposition, 3 m 45 s wall clock, 88 MB of last-good cache written. Six of
fifteen registered endpoints answer.

Two feeds were fixed in the course of that run, both found only by going live:

- **OLIS was returning nothing despite a 200.** Its `@odata.nextLink` drops the
  `$format=json` the first request carried, so page two answered 200 with 26 MB of Atom
  XML, `response.json()` raised, and the walk was discarded — including page one, which
  had parsed. Restoring the parameter on each next link turned 0 rows into 31,405.
- **WA PDC pointed at a retired dataset id.** The current one is `kv7h-kjye`
  ("Contributions to Candidates and Political Committees", 6.35 M rows, updated daily),
  found in the portal's own catalogue and confirmed by fetching and parsing it. Two
  further changes were needed before the corrected endpoint was usable: Socrata serves
  the URL column as an *object*, which failed `Signal` validation and took the whole
  feed down, and an unordered walk over 6.35 M rows reads 50,000 arbitrary rows against
  roughly 90 that fall inside any 48-hour window. It now sorts `receipt_date DESC NULLS
  LAST` — nulls last because 14,965 rows have no date and Postgres sorts nulls first
  under `DESC`.

Two things also follow from the run, both aimed at making the next correction cheap:

```bash
pdx1 --check-endpoints    # probe every registered URL, print its status, exit non-zero on failure
```

It harvests nothing and writes nothing — it exists because a dead endpoint is
otherwise quiet by design, recorded as an adapter error while the cycle carries on.

Every endpoint is then overridable from `.env`, so a publisher moving one costs a line
rather than a release:

| Setting | Overrides |
|---|---|
| `PDX1_ORESTAR_URL` | the bulk export (may contain `{year}`) |
| `PDX1_OLIS_URL` | the OData service |
| `PDX1_SEI_URL` | the OGEC landing page |
| `PDX1_WA_PDC_URL` | the Socrata dataset |
| `PDX1_PORTLAND_PRESS_URL` | the primary press feed |

**Field names are now confirmed for two of the four record feeds, and still unconfirmed
for the other two.**

| Feed | Field names |
|---|---|
| **OLIS** | verified against 31,405 live rows. Four canonical fields map exactly; `committee` resolves only to a committee *code*, and the collection carries no action and no URL field at all, so the record falls back to status and a constructed OLIS link. |
| **WA PDC** | verified against a 6,000-row union. Thirteen of fourteen map; there is no aggregate column, so a record states the contribution rather than a cycle total. |
| **ORESTAR** | unconfirmed — the export has never returned a row. |
| **SEI** | unconfirmed — no API exists to return one. |

Verification was done over the *union* of thousands of rows, not a sample. A five-row
sample of the WA PDC dataset reported two fields as unmatched that in fact map: Socrata
omits null columns per row, and neither column appeared in those five. That is the
`union_keys` hazard this codebase already documents, met in the field.

Correcting a wrong URL is data entry, and correcting a wrong column is a one-line change
in one alias table; neither touches the parse logic.

Portland Press is the exception to all of this: RSS is a standard format, so there is
nothing to verify beyond the URLs themselves.

#### Why tone and hedging stopped being gates

That same run dropped a section:

```
section 'Under Review' rejected -- tone gate: prosecutorial language
  ['fraud', 'fraudulent', 'guilty']
```

Those words came from **press headlines the engine had harvested**, not from anything
the engine wrote. A newspaper reporting that someone pleaded guilty to fraud is stating
a court outcome; the tone gate could not tell that apart from the engine alleging
fraud, because it scans the assembled section body and a record's `pattern` carries the
source text into it. Withholding the section suppressed the report in order to prevent
the accusation.

Both checks are now observation-only. The same run produces:

```
  [info] section 'Under Review' observation: tone_gate matched
         ['fraud', 'fraudulent', 'guilty']
```

and the section publishes with that note attached. Withheld sections still print as
`[warn]`; only attribution can withhold one now.

The trade is stated under *What it does, and what it refuses to do* — detection is
unchanged, enforcement is gone, and editorial judgement moves to whoever reads the
observations. `PDX1_TONE_GATE=false` now means "do not annotate" rather than "do not
withhold", since nothing is withheld either way.

Records that cannot be dated are dropped rather than dated to now. Defaulting to the
current time would make an undated record look fresh and slip it past the velocity
gate, which is the record the gate exists to drop. OLIS logs a warning when every
fetched row is undated, so a broken date mapping cannot read as a quiet session.

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
| `GET /leads` | the analyst queue — everything above `MONITOR` (`ESCALATE`, `CORROBORATED`, `INVESTIGATE`), confidence-sorted |
| `GET /brief` | the most recently published brief |
| `GET /brief/archive` | every published brief, newest first, paginated |
| `GET /brief/{brief_id}` | one brief by ID |
| `GET /graph` | the political web — every node and tie, with record activity |
| `GET /graph/districts` | the district roster, for the District Map |
| `GET /graph/{node_id}` | one node, its ties, its neighbours, and the records touching it |
| `POST /cycle/run` | drives a full cycle and returns its summary |

Set `PDX1_API_KEY` to require an `X-API-Key` header on every request; leave it blank and
auth is bypassed, which is appropriate for local use only. `PDX1_CORS_ORIGINS` controls
the allowed origins.

`GET /brief` reads the store rather than process memory, so a brief assembled by
`python -m pdx1.pipeline` or by `pdx1-scheduler` is served here, and survives a restart.
It 404s only when no cycle has ever published one.

## Storage

JSONL is append-only and authoritative. SQLite exists to answer questions quickly and
can be rebuilt from the JSONL at any time.

There is no transaction spanning a file append and a database commit, and the module
does not pretend otherwise. Writes go to JSONL first (flushed and fsynced), then to
SQLite. If the second step fails, ground truth still holds the record and
`rebuild_from_jsonl()` restores the database. The reverse order could leave SQLite
holding a record ground truth never saw, which is the failure worth avoiding.

Writes are idempotent — re-running a cycle over the same input adds nothing.

Two streams are persisted, each with its own ground-truth file and its own table:

| Stream | Ground truth | Table |
|---|---|---|
| `IntelligenceRecord` | `pdx1_signals.jsonl` | `intelligence_records` |
| `Brief` | `pdx1_signals_briefs.jsonl` | `briefs` |

They are kept apart rather than interleaved so each file stays a homogeneous stream that
reads back without discriminating on type. The briefs path is derived from the records
path; override it with `PDX1_BRIEFS_PATH`.

Briefs are persisted because they are the product. A brief assembled by the 06:00
scheduler has to outlive the process that built it, and a re-run cannot recreate one —
the novelty gate correctly drops signals already stored, so a second cycle over the same
input yields no records and therefore no brief.

## Repository layout

```
pdx-1i/
├── src/pdx1/                  43 modules
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
│   ├── neutrality/            tone · hedging · attribution gates
│   ├── publication/           IssueBuilder · BriefPublisher · PDF renderer
│   ├── api/                   FastAPI app, routes (incl. /graph), API-key auth
│   └── demos/                 runnable walkthrough
├── ui/                        index.html (brief) · webmap.html (political web)
│                              citizen-cognisance.html (public landing) · DESIGN.md
├── tests/                     27 test files, 484 tests
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

### Serving the graph

`GET /graph` returns the whole registry — small and fixed, so it ships in one response
and a renderer can lay it out without a second round trip:

```json
{
  "nodes": [{"id": "pge", "label": "Portland General Electric", "group": "E",
             "weight": 0.9, "flag": null, "record_count": 1}],
  "ties":  [{"source": "mcp", "target": "pge", "kind": "disclosure", "flagged": true}],
  "node_count": 31, "tie_count": 40
}
```

`group` drives node shape, `kind` drives line style, and `disclosure` ties render dashed.
`record_count` is how many stored records mention that node, so the map reflects actual
activity rather than a static diagram — and it is a count, nothing more. It says how
often a body appears in the record set and nothing about why, which is the same
discipline the neutrality gates enforce on published prose.

`GET /graph/{node_id}` returns one node with its ties, its neighbours and the records
touching it — what a click on the map needs. `GET /graph/districts` returns the seat
roster for the District Map.

### Drawing it

`ui/webmap.html` renders the graph: a force-directed layout where node shape carries
`group` (diamond jurisdiction, square seat, circle entity), line style carries `kind`,
declared interests are dashed, and node size grows with `record_count`. Clicking a node
pins a panel showing its ties, its neighbours and the records that mention it.

```bash
pdx1-api                                  # terminal 1
python -m http.server 8300 --directory ui # terminal 2
# open http://localhost:8300/webmap.html?api=http://localhost:8000
```

Set `PDX1_CORS_ORIGINS` to the page's origin, and pass `?key=` if `PDX1_API_KEY` is set.

The page has no built-in dataset. If the API is unreachable it says so and draws nothing,
rather than falling back to baked-in nodes that would drift from the store while still
looking authoritative. `tests/test_webmap_ui.py` pins that, along with the neutrality
constraints — no names of individuals, no affiliation labels, no characterising language,
and no hue beyond the vacancy signal.

## Testing

```bash
pytest tests/ -v
pytest --cov=src --cov-report=term-missing tests/
ruff check src/ tests/
bandit -r src/ -ll
```

484 tests. The suite leans on boundary conditions — a signal at exactly 0.5
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

### The public landing page

`ui/citizen-cognisance.html` is the CITIZEN COGNISANCE landing page — the public face of
pdx-1i, wrapping the political web in an MCM Editorial shell: warm neutrals, one accent
(deep teal), typographic hierarchy over decoration. It is deliberately *not* the phosphor
palette of `ui/webmap.html`, which is an internal ops surface. The design system —
palette, type scale, component specs — is written up in [`ui/DESIGN.md`](ui/DESIGN.md).

```bash
python -m http.server 8300 --directory ui
# open http://localhost:8300/citizen-cognisance.html
```

Node colour is signal freshness (LIVE < 6h, RECENT < 24h, STALE beyond), node shape is
category, node size is relationship degree, and edge colour is relationship type. Below
768px the force graph is replaced by the same nodes as a list ranked by freshness, since
a force layout is unreadable on a phone.

Two things separate it from `ui/webmap.html`, and both are deliberate:

- **It carries a static fallback dataset** (48 nodes, 131 ties) so the map still draws
  with the API down, where `webmap.html` draws nothing by design. The trade-off is real:
  a baked-in dataset can drift from what the engine holds. It is confined to structure —
  freshness, gate scores, summaries and coverage links come from
  `GET /api/v1/nodes/{id}/signal` and are never synthesised locally. A node the engine
  has nothing on renders hollow and says so.
- **That dataset names individual officeholders**, where the engine's own registry
  (`src/pdx1/graph.py`) is role-based by design — seats, never people. The two are not
  interchangeable, and the role-based registry remains the authority for anything the
  engine publishes.

## Not built yet

Each of these is a clean follow-on. What is listed here is genuinely absent — if a
capability is described anywhere above, it exists and has tests.

- **Working endpoints for the remaining feeds.** The transport, mapping and fault
  tolerance are done and exercised against the real internet — a live run completes and
  publishes, and as of 2026-08-21 two record feeds (OLIS, WA PDC) return real rows with
  verified field names. Six of fifteen registered endpoints answer. ORESTAR is the one
  that needs more than data entry: the Secretary of State publishes no static bulk
  export at any path checked, and its public transaction search is a session-based web
  application rather than a URL. SEI has no API by design. Every failure is recorded
  per-endpoint in *Fixture replay vs live fetch* and beside the URL in the source.
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
