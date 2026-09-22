"""
Registered infrastructure watch targets.

Each WatchTarget names one monitored body and the RSS/Atom endpoint the pipeline polls
in live mode. The six bodies here are the infrastructure entities tracked in graph.py
that belong to Watch monitoring.

Endpoints are public feeds or press-release RSS channels. They are read-only polling;
nothing is written to these services.

Status re-probed 2026-09-22 -- five of six answer, up from one. Each replacement was
confirmed by fetching it and parsing the body, not by the status code alone: a
newsroom that serves an HTML page on an /rss path returns 200 and yields zero entries,
which is indistinguishable from a healthy feed if you only read the code.

    TriMet                  200, 10 signals  unchanged
    OHSU                    200, 20 signals  news.ohsu.edu/rss.xml (host moved)
    PPB                     200, 25 signals  .../news/rss (the .xml suffix went away)
    Portland Water Bureau   200, 25 signals  .../news/rss (same suffix change)
    PGE                     200, 10 signals  investors. replaces newsroom. (unroutable)
    NW Natural              404              no discoverable feed -- see below

NW Natural is the one left dead, and deliberately so. Its homepage declares no
`application/rss+xml` link and three plausible paths 404, so there is no endpoint to
register. A documented absence is worth more than a plausible-looking guess, because
the guess reads as verified.

`news.ohsu.edu/rss` is a near-miss worth recording: it answers 200 and parses to zero
entries. It is not a feed. The working path is `/rss.xml` on the same host.

A dead target costs that target only: `safe_fetch` records the failure and the cycle
completes.
"""

from __future__ import annotations

from . import WatchTarget

#: Registered watch targets. Order matches the graph.py ENTITIES listing.
WATCH_TARGETS: tuple[WatchTarget, ...] = (
    WatchTarget(
        name="OHSU",
        endpoint="https://news.ohsu.edu/rss.xml",
    ),
    WatchTarget(
        name="PPB",
        endpoint="https://www.portland.gov/police/news/rss",
    ),
    WatchTarget(
        name="TriMet",
        endpoint="https://news.trimet.org/feed/",
    ),
    WatchTarget(
        name="PGE",
        endpoint="https://investors.portlandgeneral.com/rss/news-releases.xml",
    ),
    WatchTarget(
        name="NW Natural",
        endpoint="https://www.nwnatural.com/news/rss",
    ),
    WatchTarget(
        name="Portland Water Bureau",
        endpoint="https://www.portland.gov/water/news/rss",
    ),
)
