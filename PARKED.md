# Parked

Things noticed while doing something else, deliberately not done. Each entry says what
was measured, what was not, and why it was left. Nothing here is required for an
unattended daily run to produce a correct brief; that is the bar for taking one off.

## From the 2026-08-21 go-live pass

**Both record feeds read their whole collection every night.** OLIS fetches 31,405
measures (44 MB, 7 pages, 21 s) and WA PDC fetches 50,000 rows (44 MB, 50 pages, 98 s),
and the velocity gate then discards all but the last 48 hours of each — 91 records
survived out of 81,475 harvested. Both services support server-side date filtering
(OData `$filter` on `ModifiedDate`, SoQL `$where` on `receipt_date`), which would turn
each walk into roughly one page. Left alone because the adapter would have to know the
gate's window to build the filter, and that coupling is a design decision, not a tidy-up.
The cost as it stands is measured and tolerable: 3 m 45 s per cycle and 88 MB of
last-good cache rewritten each run.

**WA PDC stops at its 50-page ceiling every run.** With `receipt_date DESC NULLS LAST`
the rows the gate can accept are on page one, so the remaining 49 pages are read and
discarded. Harmless, and it disappears if the date filter above ever lands.

**OLIS paging is all-or-nothing.** A failure on page N discards pages 1 through N-1 —
which is how a single bad page cost the whole feed before the `$format=json` fix. The
base class then falls back to the last-good cache, so a cycle still gets data. Keeping
a partial walk instead would mean publishing a partial harvest as though it were
complete, which is a judgment call worth making deliberately rather than in passing.

**The SEI cache can only ever hold unparseable HTML.** `_read_raw` writes the last-good
cache on a successful *fetch*, before `parse` sees the body. SEI's `feed_url` is a
landing page, so every live run caches HTML that `parse` then rejects — and would reject
again if it were ever served from cache. Costs nothing today (SEI has no API and returns
nothing either way), but "last-good" is the wrong name for what is stored.

**Nine of fifteen endpoints do not answer.** Measured 2026-08-21:

| Endpoint | Status |
|---|---|
| ORESTAR bulk export | 404 |
| Willamette Week, NW Politics (OPB) | 404 |
| Pamplin Media | SSL handshake failure |
| WATCH/OHSU, WATCH/NW Natural | 404 |
| WATCH/PPB, WATCH/Portland Water Bureau | 403 |
| WATCH/PGE | connect failure |

Finding current URLs is data entry and each is one `PDX1_*_URL` line, except ORESTAR:
the Secretary of State publishes no static bulk export at any path checked on
2026-08-21, and the public transaction search is a session-based web application
(`CFSearchPage.do`), so that one needs a fetch strategy rather than a URL.

*Untested hypothesis, recorded so nobody re-derives it from scratch:* both 403s are
`portland.gov`, which may be refusing the default httpx client rather than the request.
A User-Agent header would test that in one request. Not tried.

**The live brief is 87 small-dollar Washington contributions and 4 press items.** Every
one passed all four gates, so this is the filter working as specified — but whether a
$15 PAC contribution is what the brief should lead with is an editorial question about
the gate thresholds, not a defect. Flagged because it is the most visible difference
between the fixture baseline (10 records across 5 feeds) and a live run, and because
changing it means changing what the engine publishes.

**Two OLIS fields have no live source.** The Measures collection carries no action field
and no URL field; `_to_signal` falls back to the measure's status and a constructed OLIS
web link. Measure actions are a separate OData collection. Reading it would mean a
second walk and a join, which is new machinery.
