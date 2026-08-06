"""
Watch adapter -- infrastructure monitor.

Polls a public endpoint (RSS or plain text) for a monitored body and emits Signal
objects with source_type=WATCH. Credibility is REPORTED because the signals come from
the body's own communications channel rather than a regulatory filing.

In fixture mode the adapter reads from a local file, as all adapters do. In live mode
it fetches the endpoint registered on the WatchTarget via httpx.
"""

from __future__ import annotations

from datetime import datetime, timezone

import feedparser

from ..models import Signal, SourceType
from ..sources.base import LiveSourceAdapter
from . import WatchTarget


class WatchAdapter(LiveSourceAdapter):
    """
    Infrastructure monitor adapter.

    Takes a WatchTarget (name + endpoint), polls its RSS feed, and returns Signals.
    One WatchAdapter instance covers one monitored body.
    """

    source_type = SourceType.WATCH
    # Infrastructure body communications are secondary; same band as press.
    credibility = 0.6

    def __init__(
        self,
        target: WatchTarget,
        fixture_path=None,
        timeout: int = 60,
        live: bool = False,
        cache_dir=None,
    ) -> None:
        super().__init__(
            fixture_path=fixture_path, timeout=timeout, live=live, cache_dir=cache_dir
        )
        self._target = target
        self.name = f"WATCH/{target.name}"
        self.feed_url = target.endpoint

    def parse(self, raw: str) -> list[Signal]:
        """Parse an RSS/Atom payload from the monitored endpoint."""
        feed = feedparser.parse(raw)
        outlet = getattr(feed.feed, "title", None) or self._target.name
        signals: list[Signal] = []

        for entry in feed.entries:
            title = getattr(entry, "title", "").strip()
            summary = getattr(entry, "summary", "").strip()
            body = ". ".join(part for part in (title, summary) if part)
            if not body:
                continue

            published_at = _entry_datetime(entry)

            signals.append(
                Signal(
                    source=self.name,
                    source_type=self.source_type,
                    text=body,
                    title=title or None,
                    url=getattr(entry, "link", None),
                    author=outlet,
                    published_at=published_at,
                    credibility=self.credibility,
                )
            )

        return signals


def _entry_datetime(entry: object) -> datetime:
    """Return a timezone-aware timestamp for a feed entry, defaulting to UTC now."""
    parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if parsed is None:
        return datetime.now(timezone.utc)
    return datetime(*parsed[:6], tzinfo=timezone.utc)
