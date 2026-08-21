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
| D1 | Go live — explicit live/fixture config, no fixture default in production, per-adapter isolation, timeouts, bounded retry | not started |
| D2 | Fix the port collision — headless scheduler, `PDX1_ENVIRONMENT` refusal, API bind address, documented schema init | not started |
| D3 | Make the brief publishable — seed warnings surfaced, leads as search prompts, chain of custody, no placeholders, defined empty-day behaviour | not started |
| D4 | Deploy — VM, persistent SSD, two systemd services, DEPLOY.md from measured reality | not started |
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

## Day 30 is the decision point

Only after thirty clean runs is it worth asking who this brief is for and
whether anyone pays for it. Deciding that earlier is how the last four things
stalled.
