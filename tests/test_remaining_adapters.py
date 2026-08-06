"""
Live payload handling for SEI, WA PDC and Portland Press.

These three were the adapters left on the fixture schema after ORESTAR and OLIS were
mapped. Each needed something different, and the differences are the point:

- **WA PDC** has a real API — a Socrata dataset — so it gets an alias map and paging.
- **SEI** has no API at all. Oregon publishes downloads, not endpoints, so the honest
  behaviour is to accept export shapes and refuse HTML loudly.
- **Portland Press** needed no mapping — RSS is standard and feedparser already read a
  real feed. Its gap was that only one of five declared feeds was ever polled.

Everything here runs offline.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from pdx1.sources import PortlandPressAdapter, SeiAdapter, WaPdcAdapter
from pdx1.sources.normalize import load_records


def _response(*, text: str = "", payload=None) -> MagicMock:
    resp = MagicMock()
    resp.text = text
    resp.content = text.encode("utf-8")
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=payload if payload is not None else [])
    return resp


# ── load_records: the shapes a government export arrives in ───────────────────


def test_load_records_reads_a_json_array():
    assert load_records('[{"a": 1}, {"a": 2}]') == [{"a": 1}, {"a": 2}]


def test_load_records_unwraps_a_wrapper_object():
    assert load_records('{"records": [{"a": 1}]}') == [{"a": 1}]
    assert load_records('{"value": [{"a": 2}]}') == [{"a": 2}]


def test_load_records_reads_jsonl():
    assert load_records('{"a": 1}\n{"a": 2}\n') == [{"a": 1}, {"a": 2}]


def test_load_records_skips_unreadable_jsonl_lines():
    """One bad row must not cost the whole feed."""
    assert load_records('{"a": 1}\nnot json\n\n{"a": 2}') == [{"a": 1}, {"a": 2}]


def test_load_records_treats_a_bare_object_as_one_row():
    assert load_records('{"a": 1}') == [{"a": 1}]


def test_load_records_handles_empty_input():
    assert load_records("") == []
    assert load_records("   ") == []


def test_union_keys_sees_fields_the_first_row_omits():
    """
    Alias resolution must see every row's keys, not just the first row's.

    Government exports omit empty optional columns per row. Building the column map
    from `rows[0]` alone drops any field that row happened to lack -- for *every* row,
    including the ones carrying it. That silently empties a whole feed on the strength
    of whichever record sorted first.
    """
    from pdx1.sources.normalize import union_keys

    rows = [{"a": 1}, {"a": 2, "b": 3}]
    assert union_keys(rows) == ["a", "b"]


# ── WA PDC: Socrata ───────────────────────────────────────────────────────────

_SOCRATA_ROW = {
    "id": "WA-2026-778341",
    "filer_name": "Clark County Council District 2 Committee",
    "filer_type": "Candidate committee",
    "jurisdiction": "Clark County",
    "election_year": "2026",
    "contributor_name": "Cascade Energy Advocacy Fund",
    "contributor_city": "Portland",
    "contributor_state": "OR",
    "amount": "6000.00",
    "receipt_date": "2026-05-25T00:00:00.000",
}


def test_wa_pdc_points_at_the_socrata_dataset():
    """
    The previous endpoint was a guess at a bespoke API. Washington publishes disclosure
    data through the state open-data portal instead.
    """
    assert WaPdcAdapter.feed_url.startswith("https://data.wa.gov/resource/")


def test_wa_pdc_reads_a_socrata_row(tmp_path):
    with patch("httpx.get", return_value=_response(payload=[_SOCRATA_ROW])):
        result = WaPdcAdapter(live=True, cache_dir=tmp_path).safe_fetch()

    assert result.ok, result.errors
    assert len(result) == 1
    text = result.signals[0].text
    assert "Clark County Council District 2 Committee" in text
    assert "Cascade Energy Advocacy Fund" in text
    assert "$6,000.00" in text
    assert "contributor state is OR against recipient state WA" in text


def test_wa_pdc_pages_until_a_short_page(tmp_path):
    from pdx1.sources.wa_pdc import PAGE_SIZE

    full = [dict(_SOCRATA_ROW, id=f"r{i}") for i in range(PAGE_SIZE)]
    with patch("httpx.get", side_effect=[_response(payload=full), _response(payload=[_SOCRATA_ROW])]) as mock_get:
        result = WaPdcAdapter(live=True, cache_dir=tmp_path).safe_fetch()

    assert mock_get.call_count == 2, "a full page is followed, a short page ends it"
    assert len(result) == PAGE_SIZE + 1


def test_wa_pdc_sends_socrata_paging_params(tmp_path):
    with patch("httpx.get", return_value=_response(payload=[_SOCRATA_ROW])) as mock_get:
        WaPdcAdapter(live=True, cache_dir=tmp_path).safe_fetch()

    params = mock_get.call_args.kwargs["params"]
    assert params["$limit"] and params["$offset"] == 0


def test_wa_pdc_drops_undated_rows(tmp_path):
    undated = {k: v for k, v in _SOCRATA_ROW.items() if k != "receipt_date"}
    with patch("httpx.get", return_value=_response(payload=[undated, _SOCRATA_ROW])):
        result = WaPdcAdapter(live=True, cache_dir=tmp_path).safe_fetch()

    assert len(result) == 1


def test_wa_pdc_still_reads_the_fixture(fixture_dir):
    result = WaPdcAdapter(fixture_path=fixture_dir / "wa_pdc.json").safe_fetch()
    assert result.ok
    assert len(result) == 2


# ── SEI: exports, not an API ──────────────────────────────────────────────────

_SEI_ROW = {
    "filing_id": "SEI-2026-00871",
    "seat": "Metro Councilor - District 4",
    "jurisdiction": "Metro Council",
    "year": 2025,
    "filed_at": "2026-05-26T15:20:00+00:00",
    "interests": [
        {"kind": "Position held", "description": "Unpaid advisory board position", "entity": "OHSU"}
    ],
}


def test_sei_rejects_html_rather_than_reporting_nothing():
    """
    A live fetch of the OGEC landing page returns HTML. Returning [] would read as
    "no official filed anything", which is a false statement about public records.
    """
    with pytest.raises(ValueError, match="not a JSON or JSONL export"):
        SeiAdapter().parse("<!DOCTYPE html><html><body>SEI filings</body></html>")


def test_sei_html_surfaces_as_an_adapter_error_not_a_crash(tmp_path):
    with patch("httpx.get", return_value=_response(text="<html>landing page</html>")):
        result = SeiAdapter(live=True, cache_dir=tmp_path).safe_fetch()

    assert not result.ok
    assert "not a JSON or JSONL export" in result.errors[0]
    assert result.signals == []


def test_sei_reads_a_jsonl_export():
    adapter = SeiAdapter()
    signals = adapter.parse("\n".join(json.dumps(_SEI_ROW) for _ in range(3)))
    assert len(signals) == 3
    assert "Metro Councilor - District 4" in signals[0].text


def test_sei_maps_alternative_export_spellings():
    row = {
        "statement id": "SEI-2026-00999",
        "position": "Metro Councilor - District 1",
        "agency": "Metro Council",
        "reporting year": 2025,
        "date filed": "2026-05-26T15:20:00+00:00",
        "declared_interests": [
            {"type": "Position held", "detail": "Board seat", "organization": "TriMet"}
        ],
    }
    signals = SeiAdapter().parse(json.dumps([row]))
    assert len(signals) == 1
    text = signals[0].text
    assert "Metro Councilor - District 1" in text
    assert "Position held -- Board seat (TriMet)" in text


def test_sei_renders_a_filing_with_no_interests():
    row = {k: v for k, v in _SEI_ROW.items() if k != "interests"}
    signals = SeiAdapter().parse(json.dumps([row]))
    assert "none declared" in signals[0].text
    assert "lists 0 declared interest entries" in signals[0].text


def test_sei_keeps_malformed_interest_entries_in_the_count():
    """
    The number of declared entries is part of what the filing says. Dropping an entry
    the parser cannot read would misreport the record rather than degrade gracefully.
    """
    row = dict(_SEI_ROW, interests=[{"kind": "Position held"}, "a bare string"])
    signals = SeiAdapter().parse(json.dumps([row]))
    assert "lists 2 declared interest entries" in signals[0].text


def test_sei_drops_undated_filings():
    undated = {k: v for k, v in _SEI_ROW.items() if k != "filed_at"}
    signals = SeiAdapter().parse(json.dumps([undated, _SEI_ROW]))
    assert len(signals) == 1


def test_sei_still_reads_the_fixture(fixture_dir):
    result = SeiAdapter(fixture_path=fixture_dir / "sei.json").safe_fetch()
    assert result.ok
    assert len(result) == 2


# ── Portland Press: every feed, not just one ──────────────────────────────────

_RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
<title>{outlet}</title>
<item><title>{headline}</title><link>https://example.invalid/{n}</link>
<pubDate>Wed, 27 May 2026 22:15:00 +0000</pubDate>
<description>Body copy for {headline}.</description></item></channel></rss>"""


def _rss(outlet: str, n: int = 1) -> str:
    return _RSS.format(outlet=outlet, headline=f"{outlet} story", n=n)


def test_press_polls_every_declared_feed(tmp_path):
    """Five feeds are tracked; live mode used to fetch only OregonLive."""
    from pdx1.sources.portland_press import FEEDS

    responses = [_response(text=_rss(outlet)) for outlet in FEEDS]
    with patch("httpx.get", side_effect=responses) as mock_get:
        result = PortlandPressAdapter(live=True, cache_dir=tmp_path).safe_fetch()

    assert mock_get.call_count == len(FEEDS)
    assert len(result) == len(FEEDS), "one item per feed, all of them harvested"

    fetched = {call.args[0] for call in mock_get.call_args_list}
    assert fetched == set(FEEDS.values())


def test_press_one_dead_feed_does_not_cost_the_others(tmp_path):
    import httpx

    from pdx1.sources.portland_press import FEEDS

    responses = [httpx.ConnectError("down")] + [
        _response(text=_rss(outlet)) for outlet in list(FEEDS)[1:]
    ]
    with patch("httpx.get", side_effect=responses):
        result = PortlandPressAdapter(live=True, cache_dir=tmp_path).safe_fetch()

    assert result.ok
    assert len(result) == len(FEEDS) - 1


def test_press_every_feed_failing_falls_back_to_cache(tmp_path):
    """All five down is a total failure, which the cache fallback should catch."""
    import httpx

    from pdx1.sources.portland_press import FEEDS

    adapter = PortlandPressAdapter(live=True, cache_dir=tmp_path)
    with patch("httpx.get", side_effect=[_response(text=_rss(o)) for o in FEEDS]):
        assert adapter.safe_fetch().ok

    with patch("httpx.get", side_effect=httpx.ConnectError("down")):
        result = adapter.safe_fetch()

    assert result.ok, "the last-good envelope carries the cycle"
    assert len(result) == len(FEEDS)


def test_press_reports_total_failure_without_a_cache(tmp_path):
    import httpx

    with patch("httpx.get", side_effect=httpx.ConnectError("down")):
        result = PortlandPressAdapter(live=True, cache_dir=tmp_path).safe_fetch()

    assert not result.ok
    assert "every tracked feed failed" in result.errors[0]


def test_press_attributes_items_to_their_own_outlet(tmp_path):
    with patch(
        "httpx.get",
        side_effect=[_response(text=_rss(o)) for o in ("OregonLive", "KOIN")],
    ):
        result = PortlandPressAdapter(
            live=True,
            cache_dir=tmp_path,
            feeds={"OregonLive": "https://a.invalid", "KOIN": "https://b.invalid"},
        ).safe_fetch()

    authors = {s.author for s in result.signals}
    assert authors == {"OregonLive", "KOIN"}, "each item keeps its own outlet"


def test_press_still_reads_a_bare_rss_fixture(fixture_dir):
    result = PortlandPressAdapter(fixture_path=fixture_dir / "portland_press.xml").safe_fetch()
    assert result.ok
    assert len(result) == 3
