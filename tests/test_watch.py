"""
Watch/ infrastructure monitors.

Proves the WatchAdapter parses RSS correctly and that the six targets are
wired to real-looking endpoints.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


from pdx1.models import ConfidenceTier, SourceType
from pdx1.watch import WATCH_TARGETS, WatchAdapter, WatchTarget


# ── Target definitions ────────────────────────────────────────────────────────


def test_six_watch_targets_registered():
    assert len(WATCH_TARGETS) == 6


def test_watch_target_names_match_graph_entities():
    names = {t.name for t in WATCH_TARGETS}
    expected = {"OHSU", "PPB", "TriMet", "PGE", "NW Natural", "Portland Water Bureau"}
    assert names == expected


def test_watch_targets_have_https_endpoints():
    for t in WATCH_TARGETS:
        assert t.endpoint.startswith("https://"), f"{t.name} endpoint must be HTTPS"


# ── WatchAdapter construction ─────────────────────────────────────────────────


def test_watch_adapter_name_incorporates_target_name():
    t = WatchTarget(name="TestBody", endpoint="https://example.com/rss")
    adapter = WatchAdapter(t)
    assert adapter.name == "WATCH/TestBody"


def test_watch_adapter_source_type_is_watch():
    t = WatchTarget(name="TestBody", endpoint="https://example.com/rss")
    assert WatchAdapter(t).source_type is SourceType.WATCH


def test_watch_adapter_credibility_equals_press():
    """Infrastructure body comms are secondary, same band as press."""
    from pdx1.sources import PortlandPressAdapter

    t = WatchTarget(name="TestBody", endpoint="https://example.com/rss")
    assert WatchAdapter(t).credibility == PortlandPressAdapter.credibility


# ── Parsing ───────────────────────────────────────────────────────────────────

_RSS = """\
<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Test Body News</title>
    <item>
      <title>Rate increase approved</title>
      <description>The board approved a 4.5 percent rate increase effective July 1.</description>
      <link>https://example.com/news/1</link>
      <pubDate>Wed, 28 May 2026 10:00:00 +0000</pubDate>
    </item>
    <item>
      <title>Infrastructure audit complete</title>
      <description>Annual infrastructure audit found no critical deficiencies across the service area.</description>
      <link>https://example.com/news/2</link>
      <pubDate>Tue, 27 May 2026 08:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>
"""


def test_watch_adapter_parses_rss():
    t = WatchTarget(name="TestBody", endpoint="https://example.com/rss")
    adapter = WatchAdapter(t)
    signals = adapter.parse(_RSS)

    assert len(signals) == 2
    assert all(s.source_type is SourceType.WATCH for s in signals)
    assert all(s.source == "WATCH/TestBody" for s in signals)


def test_watch_adapter_signals_are_well_formed():
    t = WatchTarget(name="TestBody", endpoint="https://example.com/rss")
    signals = WatchAdapter(t).parse(_RSS)

    for s in signals:
        assert s.signal_id.startswith("sig_")
        assert s.published_at.tzinfo is not None
        assert s.text.strip()


def test_watch_adapter_empty_feed_returns_empty_list():
    t = WatchTarget(name="TestBody", endpoint="https://example.com/rss")
    empty = """<?xml version="1.0"?><rss version="2.0"><channel><title>T</title></channel></rss>"""
    assert WatchAdapter(t).parse(empty) == []


def test_watch_adapter_fixture_backed(tmp_path):
    """WatchAdapter can be given a fixture file for CI replay."""
    fixture = tmp_path / "watch.xml"
    fixture.write_text(_RSS, encoding="utf-8")

    t = WatchTarget(name="TestBody", endpoint="https://example.com/rss")
    adapter = WatchAdapter(t, fixture_path=fixture)
    result = adapter.safe_fetch()

    assert result.ok
    assert len(result) == 2


def test_watch_adapter_live_calls_httpx():
    """With live=True, WatchAdapter fetches via httpx."""
    t = WatchTarget(name="TestBody", endpoint="https://example.com/rss")
    mock_response = MagicMock()
    mock_response.text = _RSS
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.get", return_value=mock_response) as mock_get:
        result = WatchAdapter(t, live=True).safe_fetch()

    mock_get.assert_called_once()
    assert result.ok
    assert len(result) == 2


# ── Pipeline integration ──────────────────────────────────────────────────────


def test_watch_source_type_maps_to_reported_tier():
    """WATCH signals carry REPORTED confidence, same as press."""
    from pdx1.pipeline import _TIER_BY_SOURCE

    assert _TIER_BY_SOURCE[SourceType.WATCH] is ConfidenceTier.REPORTED
