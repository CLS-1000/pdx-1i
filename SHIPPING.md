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

Three blockers, in the order they have to clear.

**1. No VM exists yet.** D4 targets a Compute Engine instance with an attached
persistent SSD. Nothing has been provisioned, and the development sandbox has no
`gcloud`, no GCP credentials and no SSH keys, so it cannot provision one. D4's
acceptance — "reboot the VM, both services come back, the next run fires at 06:00
Pacific" — can only be executed and measured on the real host.

**2. `main` is red.** `tests/test_patch_citizen_weights.py` is **16 failed, 11 passed**
on `a159af4`. It was 26 passed at `85bba3e`, so the regression arrived with PR #26.
Cause: that PR hand-applied what its own patcher script exists to apply, changing the
note text in `ui/citizen-cognisance.html` from `'Ranked by signal freshness'` to
`'Ranked by topic score'`. The patcher's `OLD_NOTE` anchor no longer matches, so it
exits 2 and every test asserting the real UI file is patchable fails.

This blocks D4 step 2 specifically, which says to run the full suite on the VM and
record the exact count. That count would be 16 failures inherited from `main`, which
is not a baseline worth writing down. Whether the patcher should be updated to the new
anchors or retired as vestigial is a question about PR #26's intent, not this script's.

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

## Day 30 is the decision point

Only after thirty clean runs is it worth asking who this brief is for and
whether anyone pays for it. Deciding that earlier is how the last four things
stalled.
