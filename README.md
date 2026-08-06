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

Adapters default to replaying checked-in fixtures, so a cycle is reproducible and CI
needs no connectivity. Set `PDX1_LIVE=true` (and install the `live` extra) to fetch over
HTTP instead.

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
| **WA PDC** | a Socrata dataset on `data.wa.gov`, paged with `$limit`/`$offset`. Washington's disclosure regime exposes a real API where Oregon's does not. |
| **SEI** | **no API exists.** OGEC publishes periodic downloads from a landing page, so live mode here means pointing `fixture_path` at an export. `parse` accepts JSON, JSONL or a wrapper object, and rejects HTML loudly rather than returning nothing. |
| **Portland Press** | RSS, which needed no mapping — `feedparser` reads a real feed the same way it reads the fixture. What it needed was *all five* tracked feeds; live mode previously polled only OregonLive. |

The four record feeds map field names through an alias table, so correcting a name is a
one-line change in one place, and a name matching nothing leaves its field empty and
logs rather than raising. Alias resolution reads the union of every row's keys, because
exports omit empty optional columns per row and reading only the first row would drop a
field for every record on the strength of whichever sorted first.

#### What a live run actually reached

`PDX1_LIVE=true` was run against the real endpoints on **2026-08-06**. The cycle
completed and published a brief, which is the fault-tolerance design working as
intended — but most endpoints answered 404. Recorded here and in the source so nobody
re-derives it:

| Endpoint | Result |
|---|---|
| OLIS | **200** — URL and OData envelope confirmed |
| SEI landing page | **200 HTML**, rejected by `parse` as designed |
| OregonLive · KOIN | **200** |
| TriMet watch | **200** |
| ORESTAR bulk export | 404 — path or filename convention is wrong |
| WA PDC dataset | 404 — right host and shape, wrong dataset id |
| Willamette Week · NW Politics | 404 |
| Pamplin Media | SSL handshake failure |
| OHSU · PPB · NW Natural · Water Bureau | 404 |
| PGE watch | DNS failure |

**Field names remain unconfirmed for all four record feeds.** No row from a live
response has been parsed yet — OLIS reached 200 but no measure was read on that run,
and the other three never returned data — so the spellings still come from two prior
PDX-1i implementations. The mapping *logic* is tested across 73 offline tests; the
*names* are not. Correcting a wrong URL is data entry, and correcting a wrong column is
a one-line change in one alias table; neither touches the parse logic.

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
├── tests/                     26 test files, 463 tests
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

463 tests. The suite leans on boundary conditions — a signal at exactly 0.5
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

- **Working endpoints for most feeds.** The transport, mapping and fault tolerance are
  done and exercised against the real internet — a live run completes and publishes.
  What is missing is correct URLs: as of 2026-08-06 only OLIS, two press feeds and one
  watch target answer, and no record feed has yet returned a row, so no field mapping
  has been confirmed against real data. Every failure is recorded per-endpoint in
  *Fixture replay vs live fetch* and beside the URL in the source. This is data entry
  plus one alias-table pass, not new machinery.
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
