"""
Registered infrastructure watch targets.

Each WatchTarget names one monitored body and the RSS/Atom endpoint the pipeline polls
in live mode. The six bodies here are the infrastructure entities tracked in graph.py
that belong to Watch monitoring.

Endpoints are public feeds or press-release RSS channels. They are read-only polling;
nothing is written to these services.
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
