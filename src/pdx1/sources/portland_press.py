"""
Portland metro press -- local news RSS.

Five tracked feeds: OregonLive, Willamette Week, KOIN, Pamplin Media, NW Politics.

Press items are secondary sources. They rate lower on credibility than a filed record
and map to the REPORTED confidence tier, because what they establish is that something
was reported -- not that it happened.
"""

from __future__ import annotations

from datetime import datetime, timezone

import feedparser

from ..models import Signal, SourceType
from .base import LiveSourceAdapter

#: Tracked feeds. URLs are used once live fetching is enabled; the fixture path
#: exercises the same parse method in the meantime.
FEEDS: dict[str, str] = {
    "OregonLive": "https://www.oregonlive.com/arc/outboundfeeds/rss/",
    "Willamette Week": "https://www.wweek.com/feed/",
    "KOIN": "https://www.koin.com/feed/",
    "Pamplin Media": "https://pamplinmedia.com/feed/",
    "NW Politics": "https://www.opb.org/news/feed/",
}

# Primary live feed (OregonLive); others can be added by instantiating with different
# fixture_path or by subclassing.
_PRIMARY_FEED_URL = FEEDS["OregonLive"]


def _entry_datetime(entry: object) -> datetime:
    """
    Pull a timezone-aware timestamp off a feed entry.

    feedparser normalises the parsed-time struct to UTC, so a naive struct is UTC by
    construction. Entries with no usable date fall back to now -- they will then be
    judged by the velocity gate like anything else rather than being silently dropped.
    """
    parsed = getattr(entry, "published_parsed", None) or getattr(
        entry, "updated_parsed", None
    )
    if parsed is None:
        return datetime.now(timezone.utc)
    return datetime(*parsed[:6], tzinfo=timezone.utc)


class PortlandPressAdapter(LiveSourceAdapter):
    """Parses Portland-area news RSS into signals."""

    name = "PORTLAND_PRESS"
    source_type = SourceType.PORTLAND_PRESS
    credibility = 0.6
    feed_url = _PRIMARY_FEED_URL

    def parse(self, raw: str) -> list[Signal]:
        feed = feedparser.parse(raw)
        outlet = getattr(feed.feed, "title", None) or self.name
        signals: list[Signal] = []

        for entry in feed.entries:
            title = getattr(entry, "title", "").strip()
            summary = getattr(entry, "summary", "").strip()
            # Join only the parts that exist -- composing "{title}. {summary}"
            # unconditionally turns an entry with neither into a bare ".".
            body = ". ".join(part for part in (title, summary) if part)
            if not body:
                continue

            signals.append(
                Signal(
                    source=self.name,
                    source_type=self.source_type,
                    text=body,
                    title=title or None,
                    url=getattr(entry, "link", None),
                    author=getattr(entry, "author", None) or outlet,
                    published_at=_entry_datetime(entry),
                    credibility=self.credibility,
                )
            )

        return signals
