"""
Registered infrastructure watch targets.

Each WatchTarget names one monitored body and the RSS/Atom endpoint the pipeline polls
in live mode. The six bodies here are the infrastructure entities tracked in graph.py
that belong to Watch monitoring.

Endpoints are public feeds or press-release RSS channels. They are read-only polling;
nothing is written to these services.

Status from a live run on 2026-08-06 -- one of six answered:

    TriMet                  200
    OHSU                    404  -- URL needs correcting
    PPB                     404  -- URL needs correcting
    PGE                     DNS failure (host does not resolve)
    NW Natural              404  -- URL needs correcting
    Portland Water Bureau   404  -- URL needs correcting

A dead target costs that target only: `safe_fetch` records the failure and the cycle
completes. Correcting these is data entry against each body's newsroom page, not a
code change.
"""

from __future__ import annotations

from . import WatchTarget

#: Registered watch targets. Order matches the graph.py ENTITIES listing.
WATCH_TARGETS: tuple[WatchTarget, ...] = (
    WatchTarget(
        name="OHSU",
        endpoint="https://www.ohsu.edu/news/rss.xml",
    ),
    WatchTarget(
        name="PPB",
        endpoint="https://www.portland.gov/police/news/rss.xml",
    ),
    WatchTarget(
        name="TriMet",
        endpoint="https://news.trimet.org/feed/",
    ),
    WatchTarget(
        name="PGE",
        endpoint="https://newsroom.portlandgeneral.com/rss/news_releases.rss",
    ),
    WatchTarget(
        name="NW Natural",
        endpoint="https://www.nwnatural.com/news/rss",
    ),
    WatchTarget(
        name="Portland Water Bureau",
        endpoint="https://www.portland.gov/water/news/rss.xml",
    ),
)
