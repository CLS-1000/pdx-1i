# PDX-1i — Portland Metro Intelligence

PDX-1i is an open-source intelligence (OSINT) engine for public records about
politics and civic infrastructure in the bi-state Portland metro: Multnomah,
Washington, and Clackamas counties in Oregon, and Clark County in Washington.
It ingests campaign-finance filings, legislative records, statements of economic
interest, local press, and infrastructure-watch feeds.

The pipeline normalizes source data, applies deterministic credibility, volume,
velocity, and novelty gates, resolves entities against a role-based registry,
measures surviving records against a rolling baseline, and stores traceable
intelligence records. A brief is assembled when the publication trigger fires;
each run can complete even when an individual source fails.

The engine reports structure and timing, not motive or findings. Attribution is
enforced: published sections must cite records held by the engine. Tone and
hedging checks are recorded as observations, not publication gates. Officials in
the engine's graph are role-based seats rather than named people.

## Architecture

```text
public feeds → normalize and parse → four gates → entity resolution
             → baseline and analysis → JSONL + SQLite → triggered brief
```

Source adapters and six infrastructure-watch monitors live in `src/pdx1/sources/`
and `src/pdx1/watch/`. `pipeline.py` orchestrates each cycle; `gates.py`,
`resolver.py`, `anomaly.py`, and `trigger.py` implement scoring and state.
`store.py` writes append-only JSONL ground truth before updating SQLite.
`publication/` assembles briefs, and `api/` serves records and graph data.

## Requirements and installation

Python 3.12 or later is required. Install the capabilities you plan to use:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Optional extras:

| Extra | Provides |
|---|---|
| `live` | HTTP clients and parsing support for fetching live feeds |
| `api` | FastAPI, Uvicorn, and the APScheduler daily scheduler |
| `pdf` | PDF rendering for published briefs |
| `dev` | Test and development tools |

For example, install live fetching, the scheduler/API, and PDF support with:

```bash
python -m pip install -e ".[live,api,pdf]"
```

Configuration is read from process environment variables and, when present, `.env`.
Start from the checked-in defaults:

```bash
cp .env.example .env
```

Review `.env.example` for all settings. In particular, it sets
`PDX1_ENVIRONMENT=development` and `PDX1_LIVE=false`: cycles use checked-in
fixtures by default and make no network requests. For live fetching, install the
`live` extra and set `PDX1_LIVE=true`. Production must explicitly set both
`PDX1_ENVIRONMENT=production` and `PDX1_LIVE=true` (or explicitly choose
`PDX1_LIVE=false`); it will not silently default to fixture replay.

## Run a cycle

Run one complete cycle locally with the fixture data:

```bash
python -m pdx1
```

This is equivalent to `pdx1` and `python -m pdx1.pipeline`. The fixture cycle
anchors its velocity gate to the newest harvested signal, so replay remains
repeatable as the fixture dates age. Use `--as-of` to choose an explicit anchor,
or `--verbose` for stage timing and counts:

```bash
python -m pdx1 --as-of 2026-05-28T12:00:00+00:00
python -m pdx1 --verbose
```

For a live one-off cycle, first install `.[live]` and set `PDX1_LIVE=true` in
`.env` or the process environment, then run the same command. Live records use
their real timestamps. Adapters isolate fetch errors, report them in the cycle
result, and continue with available sources. The first run against a new OLIS
session needs a live bootstrap to record existing transitions without emitting
them as new signals:

```bash
PDX1_LIVE=true python -m pdx1 --bootstrap
```

### Schedule recurring cycles

`pdx1-scheduler` runs the cycle using APScheduler. Its default schedule is daily
at 06:00 in `America/Los_Angeles`; configure it with `PDX1_CRON_HOUR`,
`PDX1_CRON_MINUTE`, and `PDX1_TIMEZONE`. It is a long-running process, so run it
under a process manager or on a host that keeps it running:

```bash
# Set PDX1_ENVIRONMENT=production and PDX1_LIVE=true in .env first.
pdx1-scheduler
```

The scheduler refuses to start if `PDX1_ENVIRONMENT` is not explicitly declared.
It can optionally serve the API in the same process with
`PDX1_SCHEDULER_EMBEDDED_API=true`; otherwise start `pdx1-api` separately. This
repository does not provide a deployed recurring job: its GitHub workflows run
tests or publish the static UI, not the daily cycle.

### Cycle outputs

These are the default paths; the brief, OLIS transition, and cache files are
populated only when applicable:

| Path | Contents |
|---|---|
| `pdx1_signals.jsonl` | Append-only intelligence records; ground truth |
| `pdx1.db` | SQLite query layer, rebuildable from JSONL |
| `pdx1_signals_briefs.jsonl` | Published briefs, when the trigger fires |
| `pdx1_signals_runs.jsonl` | One run-ledger entry per cycle, including quiet or failed runs |
| `pdx1_signals_olis_emitted.jsonl` | Live OLIS transitions already emitted, used to avoid repeats |
| `cache/pdx1/` | Last-good payloads from live fetching |

Set `PDX1_STORE_PATH`, `PDX1_DB_PATH`, `PDX1_BRIEFS_PATH`, and `PDX1_CACHE_DIR`
to change the corresponding paths. The brief file is separate because a cycle
does not publish a brief every time; publication follows the configured trigger.

Useful operational checks:

```bash
pdx1 --check-endpoints   # Probe registered live feed URLs; requires the live extra
pdx1 --check-ledger      # Check the latest run; fixture replays do not pass this check
pdx1 --init-store        # Initialize the configured JSONL and SQLite store
```

## API and web UI

Install the `api` extra and start the local API:

```bash
pdx1-api
```

It listens on `127.0.0.1:8000` by default. Check `GET /health`; useful routes
include `GET /signals`, `/intel`, `/brief`, and `/graph`, plus `POST /cycle/run`
to start a cycle through the API. Set `PDX1_API_KEY` to require an `X-API-Key`
header. The API key is not set by default, which is suitable only for local use.
Set `PDX1_API_HOST` and `PDX1_CORS_ORIGINS` deliberately when configuring access
from elsewhere.

The static pages are in `ui/`. `index.html` reads the brief and graph endpoints;
`webmap.html` renders the graph from `GET /graph`. Serve the pages locally and
point the web map at the API:

```bash
python -m http.server 8300 --directory ui
# Open http://localhost:8300/webmap.html?api=http://localhost:8000
```

The public landing page is `citizen-cognisance.html`. GitHub Pages publishes
static UI files only; it does not host the API or run cycles. See
[`ui/DESIGN.md`](ui/DESIGN.md) for UI conventions.

PDF brief routes are available through the API when the `pdf` extra is
installed. They render the latest stored brief; they do not create a new brief.

## Troubleshooting

- **Production config error:** set `PDX1_ENVIRONMENT=production` and explicitly
  set `PDX1_LIVE=true` for live operation (or `false` to intentionally replay
  fixtures).
- **Scheduler exits at startup:** install `.[api]` and declare
  `PDX1_ENVIRONMENT`; the scheduler requires it even for local use.
- **No records or no brief:** a successful cycle can yield no records after the
  gates, and briefs are trigger-based. Review the cycle summary and
  `pdx1_signals_runs.jsonl`; fixture data also becomes old, but replay anchors
  velocity to the newest fixture signal.
- **Live source errors:** run `pdx1 --check-endpoints`, confirm the live extra is
  installed, and review adapter errors in the cycle summary or logs. A source
  failure does not itself stop the cycle.
- **API unreachable from a UI page:** confirm the API is running, set the page's
  `?api=` URL, and allow the page origin in `PDX1_CORS_ORIGINS`. If an API key is
  configured, the web map supports it through `?key=`.
- **Missing PDF support:** install `python -m pip install -e ".[pdf]"`.

## Development

```bash
python -m pip install -e ".[dev]"
pytest tests/ -q
ruff check src/ tests/
bandit -r src/ -ll
```

The tests replay checked-in fixtures and do not require network access. CI runs
the linter, Bandit, and test suite on Python 3.12. See
[`SHIPPING.md`](SHIPPING.md) for measured feed status and daily-run progress, and
[`PARKED.md`](PARKED.md) for deferred work.

## License

MIT — see [LICENSE](LICENSE).
