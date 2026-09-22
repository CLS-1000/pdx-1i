# SHIPPING — PDX-1i Daily Brief

Scope frozen 2026-08-21. This file is the only definition of "done" for PDX-1i.
Everything deferred by that decision is in [PARKED.md](PARKED.md).

---

## Definition of done

> **The scheduler runs at 06:00 America/Los_Angeles on a VM, against live
> adapters, and produces a brief you would put your name on — for thirty
> consecutive days, without you touching it.**
>
> If it breaks on day nine, you fix it and the count restarts at zero. The count
> is the product. Nothing else on this list is finished until the count is thirty.

Falsifiable: the run-count table below reaches thirty consecutive rows with
`intervention = none`, or it does not.

### Non-goals for the entire script

No threat index. No Gatepost. No gated search layer. No publication redesign. No
new adapters. No new products. An idea that feels urgent mid-script goes in
PARKED.md and the work continues.

---

## Count

**Current consecutive count: 0 — not started.**

The count starts when D5 is complete: the scheduler is live on the VM, the daily
health check writes its line, and the failure alert path is armed. Until then
this table stays empty. A partially-deployed system producing briefs by hand is
not day one.

### What counts as an intervention

Anything the operator did between one 06:00 run and the next that the system
would not have done unattended:

- restarting `pdx1-api` or `pdx1-scheduler`, or rebooting the VM
- editing code, config, `.env`, or a systemd unit
- re-running the cycle by hand, for any reason
- editing the brief before it would be publishable
- clearing disk, rotating a log, or unsticking the store by hand

Reading the brief and filling in a row is **not** an intervention. Neither is a
partial-adapter run that the brief correctly discloses on its face — that is the
system working. Anything else is `intervention = <what you did>`, and the count
resets to zero on the next row.

The count is honest or it is worthless. A day you quietly restarted a service is
an intervention. Write it down.

### Run count

One row per day, filled by hand each morning after reading the brief.

- `date` — date of the run, ISO, America/Los_Angeles.
- `run_id` — from the run itself, format `pdx1_YYYY_MMDD_HHMMSS`.
- `status` — `ok` | `partial` (brief produced, some adapter failed and the brief
  says so) | `fail` (no brief, or a brief that needed edits).
- `brief_sections` — section count on the published brief; `0` on an empty day
  that correctly reported nothing cleared the gates.
- `intervention` — `none`, or what you did.

| date | run_id | status | brief_sections | intervention |
|------|--------|--------|----------------|--------------|
|      |        |        |                |              |

---

## Break log

Every reset gets a line here: the date, what broke, the fix, and the test that
now covers it. A break is information about this system — the response is a fix
and a test, not a redesign.

| date | run_id | cause | fix | test added | count reset to |
|------|--------|-------|-----|------------|----------------|
|      |        |       |     |            |                |

---

## Progress against the script

| step | what it delivers | state |
|------|------------------|-------|
| D0 | Freeze scope — SHIPPING.md, PARKED.md, notes in other repos | this file; PARKED notes in other repos **not yet written** |
| D1 | Go live — explicit live/fixture config, no fixture default in production, per-adapter isolation, timeouts, bounded retry | code done; **9 of 11 live endpoints are dead** — see below |
| D2 | Fix the port collision — headless scheduler, `PDX1_ENVIRONMENT` refusal, API bind address, documented schema init | **done** (code); DEPLOY.md deferred to D4, which must write it from a real deploy |
| D3 | Make the brief publishable — seed warnings surfaced, leads as search prompts, chain of custody, no placeholders, defined empty-day behaviour | not started |
| D4 | Deploy — VM, persistent SSD, two systemd services, DEPLOY.md from measured reality | **blocked** — see below |
| D5 | The clock — health-check line, one alert path, start the count | not started |
| D6 | When it breaks — fix, test, log, reset | standing |

---

## Measured baseline (fixture run)

Recorded 2026-08-21 on this checkout so the first live run has something to be
compared against. Fixture replay, not live — `PDX1_LIVE` unset.

```
5 adapters: ORESTAR 3, OLIS 2, SEI 2, WA_PDC 2, PORTLAND_PRESS 3
harvested     12
parsed        12
opportunities 10
dropped by    velocity=1, volume=1
written       10
brief         2 sections; 10 records across 5 feeds; 3 at elevated disposition
```

Suite on this checkout, Python 3.13: **484 passed**. Run with
`pytest > /tmp/pytest.log 2>&1; echo $?` and read the file — `pyproject.toml`
already sets `-q` in `addopts`, so passing `-q` again suppresses the summary
line entirely, and piping through `tail`/`grep` hides that it is missing.

Live counts will differ from this baseline. That is expected and is the point of
D1. A live adapter returning **zero** is the case to flag, because zero is
indistinguishable from a broken adapter.

---

## First live run — measured 2026-08-21

`PDX1_LIVE=true PDX1_ENVIRONMENT=production python -m pdx1`, from the development
sandbox, with the `live` extra installed. The cycle completed end to end, logged
`live=True`, wrote to the store, and produced a brief. It is not a publishable brief,
and the reason is below.

| adapter | items | HTTP | attempts | elapsed | outcome |
|---|---|---|---|---|---|
| ORESTAR | 0 | 404 | 1 | 1.18s | endpoint gone |
| OLIS | 0 | 200 | 1 | 6.25s | answers, but not JSON — `JSONDecodeError` |
| SEI | 0 | 200 | 1 | 1.61s | answers HTML; no API exists, by design |
| WA_PDC | 0 | 404 | 1 | 0.68s | endpoint gone |
| PORTLAND_PRESS | **60** | 200 | 1 | 3.46s | **live data** (3 of 5 outlets) |
| WATCH/OHSU | 0 | 404 | 1 | 0.64s | endpoint gone |
| WATCH/PPB | 0 | 403 | 1 | 0.51s | forbidden |
| WATCH/TriMet | **10** | 200 | 1 | 1.35s | **live data** |
| WATCH/PGE | 0 | — | 3 | 4.55s | proxy error; retried and still failed |
| WATCH/NW Natural | 0 | 404 | 1 | 1.42s | endpoint gone |
| WATCH/Portland Water Bureau | 0 | 403 | 1 | 0.18s | forbidden |

`pdx1 --check-endpoints` agrees: **5 of 15 registered URLs answered**, exit 1.

### Live vs the fixture baseline

| | fixture | live |
|---|---|---|
| adapters | 5 | 11 (5 feeds + 6 watch targets) |
| feeds returning | 5 | 2 |
| harvested | 12 | 70 |
| parsed | 12 | 70 |
| opportunities | 10 | 6 |
| dropped | 2 (velocity 1, volume 1) | 64 (velocity 9, volume 56) |
| written | 10 | 6 |
| brief sections | 2 | 2 |
| elevated disposition | 3 | 0 |

Live counts differing from fixture counts is expected. What is not expected, and is the
finding that matters: **nine of eleven feeds returned nothing.** Every record in that
live brief came from Portland Press and TriMet. The volume gate dropping 56 of 70 is
consistent with a harvest that is almost entirely RSS headlines rather than filed
records.

Zero returns are flagged rather than passed over, per D1: none of the nine were empty
answers. All nine raised, so all nine are `failed` rather than `empty` — the engine did
not silently treat a dead feed as a quiet day.

### What this means for the count

The four record feeds — ORESTAR, OLIS, SEI, WA_PDC — are the ones carrying filed public
records, and none of them currently return usable data. A daily brief built from press
RSS and one transit newsroom is not the product described in the definition of done.
**Fixing these endpoints is a prerequisite for D3 and therefore for the count.** Two
are not simple URL corrections:

- **SEI answers 200 with HTML** because OGEC publishes no API at all. This is
  documented, deliberate, and not a bug — live SEI means pointing `fixture_path` at a
  downloaded export. It cannot be fixed with a URL override.
- **OLIS answers 200 but not JSON.** The endpoint is alive; the response shape is not
  what the adapter expects. This needs a look at the actual payload, not a new URL.

ORESTAR, WA_PDC, and the four dead watch targets are 404/403 and may be URL rot, which
`PDX1_*_URL` overrides can fix without a release. No replacement URL is recorded here
because none has been verified — a documented 404 is more useful than a plausible guess,
because the guess reads as verified.

One caveat on WATCH/PGE: its `ProxyError` came from the development sandbox's outbound
proxy and is not evidence about the endpoint itself. It needs re-testing from the VM.

### Addendum 2026-09-16 — OLIS may no longer be in this state

Work landed on `main` (#33, #34) that rewrote the OLIS adapter: it re-adds `$format=json`
to every paged URL, which is aimed squarely at the failure measured above — OLIS answered
200 and then `response.json()` raised, because page 2 onward came back as Atom XML.

**That fix has not been re-measured live.** The table above is a dated measurement from
2026-08-21 and stays as recorded; it is not a claim about today. Whether OLIS now returns
usable data is open until someone runs it live and writes the number down. The other three
record feeds — ORESTAR, SEI, WA_PDC — are untouched by that work and still stand as
measured.

---

## D4 is blocked — 2026-09-22

Two blockers remain; the third is fixed.

**1. No VM exists yet.** D4 targets a Compute Engine instance with an attached
persistent SSD. Nothing has been provisioned, and the development sandbox has no
`gcloud`, no GCP credentials and no SSH keys, so it cannot provision one. D4's
acceptance — "reboot the VM, both services come back, the next run fires at 06:00
Pacific" — can only be executed and measured on the real host.

**2. ~~`main` is red.~~ Fixed 2026-09-22 — and it was a shipped UI bug, not just red
tests.**

`tests/test_patch_citizen_weights.py` was 16 failed / 11 passed on `a159af4`, having
been 26 passed at `85bba3e`. The cause was not what it first looked like. `1072ac6
fix: restore clean citizen UI baseline` set out to remove patcher output from the
committed UI file and removed **most** of it: the sliders' CSS, the slider HTML, and
the JS block. It left the patched sort comparator and note text behind.

That comparator calls `topicScore()`, which the deleted JS block was the only thing
defining. So `ui/citizen-cognisance.html` on `main` called an undefined function from
`renderList`, throwing a `ReferenceError` on every render of the list. No Python test
executes that file, so nothing caught it except the patcher's anchors drifting.

The fix completes the restore: the sort comparator and note text go back to their
pristine forms, and the dangling `topicScore()` call disappears with them. PR #26's
unrelated accessibility work on the signal filter is untouched.

Two regression tests were added, with deliberately different reach. One forbids
committing verbatim patcher output. The other forbids referencing a helper the
patcher defines without defining it -- that is the one that actually catches this,
since the committed output here had been hand-edited away from what the patcher
generates, so no sentinel matched.

#### It went red a second time the same day, from a clean merge

`main` was green for roughly two hours before `test_real_ui_file_is_patchable`
failed again. Two causes, in sequence, neither of them a bad edit.

First, #32 added `strip_v2_block` to `STEPS`. That test asserted every step in the
table prints its label, which held only while every step was unconditional. The new
step correctly no-ops on a page with no legacy block -- now the normal case -- so it
printed nothing and the assertion failed. The patcher was right and the test was
over-strict.

Then the repair itself broke. #37 and PR #39 each rewrote that same function from
separate branches. Git merged both bodies into one function **with no conflict
markers**, producing a `for` whose body was a bare `continue`, a stray docstring
parsed as an expression statement, and `_run` called twice -- so the second call
reported "No changes needed (already patched)" and every label assertion failed
against it. It parsed, so nothing flagged it until the suite ran. On PR #39's branch
the same merge clashed outright and gave an `IndentationError` instead.

Resolved on `9c8d6c6`: one function body, asserting over an explicit `ALWAYS_APPLIED`
list, with a separate test holding conditional steps silent on the committed file.
651 passed.

One gap outlived that fix by a few minutes. `test_always_applied_labels_match_the_step_table`
guarded the list with a subset check, which fails on a renamed step but passes when a
new unconditional step is added to `STEPS` and left out of the list -- at which point
`test_real_ui_file_is_patchable` silently stops checking that anchor, the same class of
gap that started this. Copilot caught it on review; the fix missed #39's merge by about
a minute and landed separately as #41 (`913c522`). It now asserts equality against
`STEPS - CONDITIONAL_STEPS`, so an added step fails as loudly as a renamed one.

Worth recording as a pattern rather than an incident. Five separate breakages in this
sequence came from two individually-correct changes to the same file merging cleanly,
and git reported no conflict in any of them. Two agents fixing the same failure within
the same hour is the cause, not carelessness. What catches this class is a test that
fails when the step table and its expectations drift apart in either direction.

**3. DEPLOY.md cannot be written yet, and must not be faked.** D4 step 5 says to write
it "from what you actually did, with measured times — not from what you intended."
Writing it before deploying would produce the one artifact whose whole value is that
it was measured. It is deliberately absent until there is a deploy to describe.

### What D2 delivered toward it

The port collision is fixed, which was the reason D4 step 3 could not have worked:

- `PDX1_SCHEDULER_EMBEDDED_API` defaults to false, so the scheduler runs headless and
  `pdx1-api` owns 8000 as its own unit. Previously the scheduler bound 8000
  unconditionally; under `Restart=on-failure` two units on one port is a crash loop.
- The scheduler refuses to start (exit 2) when `PDX1_ENVIRONMENT` is unset, because an
  unset value is indistinguishable from a unit file that dropped it.
- The API binds `127.0.0.1` by default rather than `0.0.0.0`. **Chosen deliberately:**
  the API exposes the store behind only an API key, and an instance with `0.0.0.0:8000`
  open is reachable by whatever finds the external IP. Public is now an explicit
  `.env` line.
- `pdx1 --init-store` makes schema creation an explicit command. It was implicit in
  `DualWriteStore.__init__`, which works but gives a deploy document nothing to cite.
  It prints the resolved paths, which is how the VM confirms the store landed on the
  SSD rather than the boot disk *before* a reboot proves otherwise.

---

## Feed status — measured 2026-09-22

Re-probed after the OLIS work landed on `main`. `--check-endpoints` still reports
**5 of 15**, but that count is misleading on its own: two of the four record feeds
now return usable data, and one of them needed a code fix rather than a URL.

| feed | before | now | how |
|---|---|---|---|
| OLIS | 200, unparseable | **307 measures + 1,283 transitions** | main's `$format=json` fix; first live measurement of it |
| WA_PDC | 404 | **49,367 rows, 23,244 from 2026** | new dataset id + a Socrata `url`-object fix |
| ORESTAR | 404 | **still 404** | four URL variants probed; superseded by the 2026-09-22 pass below |
| SEI | HTML | **unchanged** | OGEC publishes no API, by design |

## Endpoints re-probed — 2026-09-22 (second pass)

`--check-endpoints` now reports **10 of 15**, up from 5. Every replacement below was
confirmed by fetching it and parsing the body, never by the status code alone — a
newsroom serving HTML on an `/rss` path answers 200 and yields zero entries, which
reads identically to a healthy feed if you only check the code. `news.ohsu.edu/rss`
is exactly that trap; the feed is at `/rss.xml` on the same host.

| endpoint | before | now | change |
|---|---|---|---|
| WA_PDC | 404 (`tijg-9uu3` still registered) | **6,375,722 rows; adapter parses 49,350 signals, 3,907 distinct dates** | registered `kv7h-kjye` |
| WATCH/OHSU | 404 | **20 signals** | host moved to `news.ohsu.edu` |
| WATCH/PPB | 404 | **25 signals** | `/news/rss` — the `.xml` suffix went away |
| WATCH/Portland Water Bureau | 404 | **25 signals** | same suffix change |
| WATCH/PGE | unroutable | **10 signals** | `investors.` replaces `newsroom.` |
| WATCH/NW Natural | 404 | **still 404** | no discoverable feed; see below |
| ORESTAR | 404 | **still 404, and now known why** | see below |

The WA_PDC line is the one worth pausing on. The working dataset id had already been
found on 2026-08-21 and recorded here, but it lived only as a `PDX1_WA_PDC_URL`
override — the adapter still shipped `tijg-9uu3`, so a default run kept hitting a dead
dataset while the docs said the feed worked. Finding a URL and registering it are two
different acts, and only the second one reaches a cron job.

### ORESTAR has no public bulk endpoint, and that is now established

Not "four variants 404'd" — the absence itself is the finding:

- the registered URL 404s (re-confirmed);
- the SOS campaign-finance page and its historical-data page link to exactly three
  things — the ORESTAR web app, a `data.oregon.gov` catalogue query, and a support
  mailbox — and offer no bulk file;
- `data.oregon.gov` holds **no** ORESTAR transaction dataset. Its only campaign-finance
  datasets are Penalty Notices (`fku5-vh2b`, 844 rows, reachable; `t6qa-n2ph`, 403
  non-tabular). Those are enforcement actions, not contributions, so they are a
  different record type rather than a substitute;
- transactions are served only by the interactive app
  (`secure.sos.state.or.us/orestar/gotoPublicTransactionSearch.do`), a session-scoped
  JSP search whose results POST redirect-loops without the full hidden form state and
  which exposes no export link.

So the data sits behind an interactive search, not a public file. Harvesting it means
emulating that session and scraping paginated HTML — a different shape from an adapter
whose `parse` is pure and whose fetch expects one document. That is a design decision
to take deliberately, not a URL to correct.

### Willamette Week, OPB and NW Natural declare no feed

All three homepages were fetched and contain no `application/rss+xml` link, and the
plausible paths 404. Nothing is registered for them, and a documented absence beats a
guess that reads as verified.

### OLIS is verified, not merely reachable

307 measures with real URLs and dates, no epoch-defaulted timestamps, 39 distinct
dates. `title` is `None` on all of them, which is correct: `Signal.title` is optional
and publication falls back to the opening of `text`.

Procedural state works: 1,283 of 1,590 signals carry it, across 7 distinct states
(`introduced` 476, `passed` 326, `signed_by_presiding` 312, `enacted` 144, `adopted`
22, `failed` 2, `vetoed` 1), each stamped `rules_version=5d6c1b8bf766`. The whole
fetch takes 5.7s.

One trap: `_harvest_transitions` opens with `if not (self._live and self.store is not
None): return []`. Without a store it emits **zero** transitions and reports success.
The pipeline passes one, so production is fine, but a bare adapter in a script looks
healthy while producing nothing.

### WA_PDC needed a real fix, not just a URL

`tijg-9uu3` is gone. `kv7h-kjye` is the live dataset, recorded in `.env.example`.

Pointing at it was not enough. Socrata serialises a `url` column as an object --
`{"url": "...", "description": "..."}` -- and handing that to `Signal.url` raised out
of `parse`, taking the **entire feed** down rather than one row. Downstream that reads
as "Washington filed no contributions today", which is a false statement about public
records. Fixed in `_socrata_url`, with a regression test verified against the unfixed
mapping.

**Two known issues, deliberately not fixed:** `wa_pdc` pages with `$limit`/`$offset`
and no `$order`, which Socrata does not guarantee is stable across pages; and it hits
the 50-page ceiling at ~42s per run, pulling the whole historical dataset every cycle
rather than the day's filings. Neither blocks a brief, both are worth a session.

### ORESTAR: what was ruled out

Four paths tried (2024/2025/2026 `_report_transactions.zip` and bare
`transactions.zip`), all 404. The ORESTAR and campaign-finance landing pages answer
200, so the host is alive and the document path is what moved.

Oregon's Socrata catalog search returns hits for "campaign finance" -- but they are
**federated results from other Socrata domains**, not Oregon datasets. `3kfv-biw6`
appears under both `data.oregon.gov` and `data.wa.gov` searches, and none of the ids
resolve against `data.oregon.gov/resource/`. Registering one would have produced a
plausible-looking override that silently serves another state's data.

No replacement is recorded, because none was verified.

---

## The velocity anchor would have broken the count on day one — found 2026-09-22

The first full live cycle with the recovered feeds harvested **51,029 signals and
published a brief containing one record**. Velocity dropped 51,028 of them.

Cause: `run_cycle` anchored the velocity gate to the newest harvested signal rather
than to the clock. Exactly one WA PDC record was dated 2026-10-01, nine days in the
future. That record became the anchor, the 48-hour window moved with it, and every
genuinely recent signal fell outside it. The one record that survived was the
future-dated one — it was measuring itself.

The part that matters for the count: **the cycle reported success.** Exit 0, a brief
written, a section published, nothing in the run log saying the morning's output was
empty for a reason unrelated to the news. Thirty of those in a row would have counted
as thirty clean runs.

`run_cycle` now uses the real clock when `settings.live_fetch` is true and keeps the
newest-signal anchor for fixture replay, which is what the fixture rationale always
implied and what the README already said live runs should do. An explicit `--as-of`
still wins over both.

Measured on the scheduler's exact call path, with no `--as-of`:

| | before | after |
|---|---|---|
| harvested | 51,029 | 51,018 |
| dropped on velocity | 51,028 | 50,881 |
| written | **1** | **75** |
| brief | 1 section, 1 feed | **2 sections, 3 feeds, 1 elevated** |

Fixture baseline unchanged: 12 harvested, 10 written, 2 sections.

**Still open, and not fixed here:** one WA PDC record really is dated in the future.
A future timestamp no longer decides the window, but it still passes the velocity gate
as "fresh". That is a data-quality question about the source rather than a gate bug,
and it is worth a look before the count starts.

---

## Day 30 is the decision point

Only after thirty clean runs is it worth asking who this brief is for and
whether anyone pays for it. Deciding that earlier is how the last four things
stalled.
