"""
Portland metro press -- local news RSS.

Five tracked feeds: OregonLive, Willamette Week, KOIN, Pamplin Media, NW Politics.

Press items are secondary sources. They rate lower on credibility than a filed record
and map to the REPORTED confidence tier, because what they establish is that something
was reported -- not that it happened.

Unlike the filed-record adapters, nothing here needs a field mapping: RSS is a standard
format and `feedparser` reads a real feed the same way it reads the fixture. What live
mode does need is *all five* feeds. `_fetch_live` polls each and wraps the bodies in
one envelope, so a single cached payload covers the whole set and one dead outlet costs
that outlet only.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import feedparser

from ..models import Signal, SourceType
from .base import LiveSourceAdapter

logger = logging.getLogger(__name__)

#: Tracked feeds. Live mode polls every one of them.
#
#: Status from a live run on 2026-08-06 -- two of five answered, and the adapter
#: harvested from those two rather than failing:
#:   OregonLive       200
#:   KOIN             200
#:   Willamette Week  404  -- URL needs correcting
#:   Pamplin Media    SSL handshake failure -- host may not serve modern TLS
#:   NW Politics      404  -- URL needs correcting
FEEDS: dict[str, str] = {
    "OregonLive": "https://www.oregonlive.com/arc/outboundfeeds/rss/",
    "Willamette Week": "https://www.wweek.com/feed/",
    "KOIN": "https://www.koin.com/feed/",
    "Pamplin Media": "https://pamplinmedia.com/feed/",
    "NW Politics": "https://www.opb.org/news/feed/",
}

#: Declared so the adapter still satisfies the single-`feed_url` contract every other
#: adapter follows. `_fetch_live` polls the whole FEEDS map rather than just this one.
_PRIMARY_FEED_URL = FEEDS["OregonLive"]

#: Marks the multi-feed envelope `_fetch_live` produces, so `parse` can tell it from a
#: bare RSS document without guessing.
_ENVELOPE_KEY = "pdx1_press_feeds"


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

    def __init__(self, *args, feeds: dict[str, str] | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.feeds = dict(feeds) if feeds is not None else dict(FEEDS)

    # ── Live fetch ───────────────────────────────────────────────────────────

    def _fetch_live(self) -> str:
        """
        Poll every tracked feed and wrap the bodies in one envelope.

        One outlet being down must not cost the other four, so a failed feed is logged
        and skipped. If every feed fails the error is raised, which lets the base class
        fall back to the last-good cache exactly as a single failed GET would.
        """
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                f"{self.name}: httpx is required for live fetch -- "
                "install it with: pip install 'pdx-1i[live]'"
            ) from exc

        bodies: list[dict[str, str]] = []
        failures: list[str] = []

        for outlet, url in self.feeds.items():
            try:
                response = httpx.get(url, timeout=self.timeout, follow_redirects=True)
                response.raise_for_status()
            except Exception as exc:  # noqa: BLE001 - one dead outlet is not fatal
                logger.warning("%s: feed %s failed: %s", self.name, outlet, exc)
                failures.append(f"{outlet}: {type(exc).__name__}")
                continue
            bodies.append({"outlet": outlet, "body": response.text})

        if not bodies:
            raise RuntimeError(
                f"{self.name}: every tracked feed failed ({'; '.join(failures)})"
            )
        if failures:
            logger.info(
                "%s: %d of %d feeds returned (%s unavailable)",
                self.name,
                len(bodies),
                len(self.feeds),
                len(failures),
            )
        return json.dumps({_ENVELOPE_KEY: bodies})

    # ── Parsing ──────────────────────────────────────────────────────────────

    def parse(self, raw: str) -> list[Signal]:
        """Parse either a single RSS document or the multi-feed envelope."""
        text = raw.lstrip()
        if text.startswith("{"):
            envelope = json.loads(text)
            entries = envelope.get(_ENVELOPE_KEY)
            if isinstance(entries, list):
                signals: list[Signal] = []
                for item in entries:
                    signals.extend(self._parse_feed(item.get("body", "")))
                return signals
        return self._parse_feed(raw)

    def _parse_feed(self, raw: str) -> list[Signal]:
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
