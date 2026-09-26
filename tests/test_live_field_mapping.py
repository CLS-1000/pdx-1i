"""
Live-feed field mapping and the last-good cache.

The engine's stated risk has always been that adapters parse the fixtures' schema and
would fail on a real export. These tests pin the two things that close that gap:

- the three-tier read (fixture, live, last-good cache) in `LiveSourceAdapter`
- the alias-driven mappings that let ORESTAR and OLIS read a real payload shape

Everything here runs offline. Live responses are constructed, not fetched -- the real
endpoints are unreachable from CI by design. That means these tests prove the mapping
logic, not the field names; see the alias tables' own notes on verification.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qsl, urlsplit

import pytest

from pdx1.sources import OlisAdapter, OrestarAdapter
from pdx1.sources.normalize import (
    build_column_map,
    first_present,
    header_key,
    parse_money,
    parse_timestamp,
)



# Single-GET path. The default ORESTAR URL is a search that needs a second request for
# the export; these tests exercise the plain-file path an override still takes.
PLAIN_URL = "https://example.invalid/transactions.zip"

def _response(
    *,
    text: str = "",
    content: bytes | None = None,
    payload=None,
    url: str = "https://example.invalid/feed",
) -> MagicMock:
    """
    A stand-in for an httpx response carrying exactly what a test needs.

    `url` is set because relative-link resolution reads `response.url`; leaving it a
    bare MagicMock would let a test pass against a nonsense base.
    """
    resp = MagicMock()
    resp.text = text
    resp.content = content if content is not None else text.encode("utf-8")
    resp.raise_for_status = MagicMock()
    resp.url = url
    if payload is not None:
        resp.json = MagicMock(return_value=payload)
    return resp


# ── normalize helpers ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("$12,500.00", 12500.0),
        ("12500", 12500.0),
        (12500, 12500.0),
        ("(500)", -500.0),  # accounting negative
        ("", 0.0),
        (None, 0.0),
        ("not a number", 0.0),
    ],
)
def test_parse_money_reads_export_spellings(raw, expected):
    assert parse_money(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "2026-05-27T16:40:00+00:00",
        "2026-05-27",
        "05/27/2026",
        "2026-05-27T16:40:00Z",  # OData trailing Z
    ],
)
def test_parse_timestamp_reads_export_spellings(raw):
    parsed = parse_timestamp(raw)
    assert parsed is not None
    assert parsed.tzinfo is not None, "every parsed timestamp must be aware"
    assert parsed.year == 2026 and parsed.month == 5 and parsed.day == 27


@pytest.mark.parametrize("raw", ["", None, "not a date", "13/45/2026"])
def test_parse_timestamp_returns_none_rather_than_now(raw):
    """
    An unreadable date must not become the current time.

    Defaulting to now would make an undated record look fresh and slip past the
    velocity gate, which is exactly the record the gate exists to drop.
    """
    assert parse_timestamp(raw) is None


def test_parse_timestamp_assumes_utc_for_naive_values():
    assert parse_timestamp("2026-05-27T16:40:00").tzinfo is timezone.utc


def test_header_key_collapses_case_and_punctuation():
    assert header_key("Tran  Id") == header_key("tran_id") == header_key("TRAN-ID") == "tran id"


def test_build_column_map_prefers_earlier_aliases():
    aliases = {"committee": ("filer", "committee")}
    resolved = build_column_map(["Committee", "Filer"], aliases)
    assert resolved["committee"] == "Filer", "first alias listed wins"


def test_build_column_map_omits_unmatched_fields():
    resolved = build_column_map(["Something Else"], {"committee": ("filer",)})
    assert "committee" not in resolved


def test_first_present_skips_empty_values():
    assert first_present({"a": "", "b": [], "c": "x"}, "a", "b", "c") == "x"
    assert first_present({"a": ""}, "a") is None


# ── Last-good cache ───────────────────────────────────────────────────────────


def test_no_cache_written_when_cache_dir_not_configured(tmp_path, fixture_dir):
    """Constructing an adapter directly must not touch the disk."""
    body = (fixture_dir / "orestar.json").read_text(encoding="utf-8")
    with patch("httpx.get", return_value=_response(text=body)):
        adapter = OrestarAdapter(live=True, feed_url=PLAIN_URL, retry_backoff_s=0)
        assert adapter.safe_fetch().ok
    assert adapter.cache_path() is None
    assert list(tmp_path.iterdir()) == []


def test_successful_live_fetch_writes_cache(tmp_path, fixture_dir):
    body = (fixture_dir / "orestar.json").read_text(encoding="utf-8")
    with patch("httpx.get", return_value=_response(text=body)):
        adapter = OrestarAdapter(live=True, feed_url=PLAIN_URL, cache_dir=tmp_path, retry_backoff_s=0)
        assert adapter.safe_fetch().ok

    assert adapter.cache_path().is_file()
    assert adapter.cache_path().read_text(encoding="utf-8") == body


def test_failed_live_fetch_falls_back_to_cache(tmp_path, fixture_dir):
    """A feed outage degrades to the last-good body rather than dropping the source."""
    import httpx

    body = (fixture_dir / "orestar.json").read_text(encoding="utf-8")
    adapter = OrestarAdapter(live=True, feed_url=PLAIN_URL, cache_dir=tmp_path, retry_backoff_s=0)

    with patch("httpx.get", return_value=_response(text=body)):
        assert adapter.safe_fetch().ok

    with patch("httpx.get", side_effect=httpx.ConnectError("connection refused")):
        result = adapter.safe_fetch()

    assert result.ok, "cached payload should carry the cycle through the outage"
    assert len(result) == 3


def test_failed_live_fetch_without_cache_still_errors(tmp_path):
    """No cache means the original failure surfaces -- silence would be worse."""
    import httpx

    with patch("httpx.get", side_effect=httpx.ConnectError("connection refused")):
        result = OrestarAdapter(live=True, feed_url=PLAIN_URL, cache_dir=tmp_path, retry_backoff_s=0).safe_fetch()

    assert not result.ok
    assert "ConnectError" in result.errors[0]


def test_cache_is_not_consulted_when_fetch_succeeds(tmp_path, fixture_dir):
    """A live success overwrites the cache rather than serving the stale copy."""
    adapter = OrestarAdapter(live=True, feed_url=PLAIN_URL, cache_dir=tmp_path, retry_backoff_s=0)
    adapter.cache_path().parent.mkdir(parents=True, exist_ok=True)
    adapter.cache_path().write_text("[]", encoding="utf-8")

    body = (fixture_dir / "orestar.json").read_text(encoding="utf-8")
    with patch("httpx.get", return_value=_response(text=body)):
        result = adapter.safe_fetch()

    assert len(result) == 3
    assert adapter.cache_path().read_text(encoding="utf-8") == body


def test_unwritable_cache_dir_does_not_fail_the_fetch(tmp_path, fixture_dir):
    """A cache we cannot write is a degraded next outage, not a failed cycle."""
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")

    body = (fixture_dir / "orestar.json").read_text(encoding="utf-8")
    with patch("httpx.get", return_value=_response(text=body)):
        result = OrestarAdapter(live=True, feed_url=PLAIN_URL, cache_dir=blocker).safe_fetch()

    assert result.ok


# ── ORESTAR: bulk ZIP + CSV ───────────────────────────────────────────────────

_CSV = (
    "Tran Id,Filer,Filer Id,Contributor/Payee,City,State,Employer,"
    "Sub Type,Amount,Aggregate Amount,Tran Date,Filed Date,Purpose\r\n"
    "4417392,Friends of Metro Transit Access,PAC-19442,Regional Utility Workers PAC,"
    "Portland,OR,Portland General Electric,Cash Contribution,"
    '"$12,500.00","$41,000.00",05/26/2026,2026-05-27T16:40:00+00:00,General campaign support\r\n'
)


def _zipped(csv_text: str, name: str = "transactions.csv") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, csv_text)
    return buffer.getvalue()


def test_orestar_unwraps_bulk_zip_and_parses_csv(tmp_path):
    with patch("httpx.get", return_value=_response(content=_zipped(_CSV))):
        result = OrestarAdapter(live=True, feed_url=PLAIN_URL, cache_dir=tmp_path, retry_backoff_s=0).safe_fetch()

    assert result.ok, result.errors
    assert len(result) == 1
    text = result.signals[0].text
    assert "Friends of Metro Transit Access" in text
    assert "Regional Utility Workers PAC" in text
    assert "$12,500.00" in text
    assert "$41,000.00" in text


def test_orestar_caches_the_unwrapped_csv_not_the_zip(tmp_path):
    """The cache holds decoded text, so a fallback read needs no unzip step."""
    adapter = OrestarAdapter(live=True, feed_url=PLAIN_URL, cache_dir=tmp_path, retry_backoff_s=0)
    with patch("httpx.get", return_value=_response(content=_zipped(_CSV))):
        assert adapter.safe_fetch().ok

    cached = adapter.cache_path().read_text(encoding="utf-8")
    assert cached.startswith("Tran Id,Filer")

    import httpx

    with patch("httpx.get", side_effect=httpx.ConnectError("down")):
        result = adapter.safe_fetch()
    assert len(result) == 1


def test_orestar_zip_without_csv_is_reported(tmp_path):
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("readme.txt", "no data here")

    with patch("httpx.get", return_value=_response(content=payload.getvalue())):
        result = OrestarAdapter(live=True, feed_url=PLAIN_URL, cache_dir=tmp_path, retry_backoff_s=0).safe_fetch()

    assert not result.ok
    assert "no CSV" in result.errors[0]


def test_orestar_header_matching_is_case_and_punctuation_insensitive(tmp_path):
    csv_text = (
        "TRAN_ID,FILER,CONTRIBUTOR/PAYEE,AMOUNT,FILED_DATE\r\n"
        "1,Committee A,Donor B,100,2026-05-27T00:00:00+00:00\r\n"
    )
    with patch("httpx.get", return_value=_response(content=_zipped(csv_text))):
        result = OrestarAdapter(live=True, feed_url=PLAIN_URL, cache_dir=tmp_path, retry_backoff_s=0).safe_fetch()

    assert len(result) == 1
    assert "Committee A" in result.signals[0].text
    assert "Donor B" in result.signals[0].text


def test_orestar_drops_rows_with_no_readable_date(tmp_path):
    csv_text = (
        "Tran Id,Filer,Contributor/Payee,Amount,Tran Date,Filed Date\r\n"
        "1,Committee A,Donor B,100,,\r\n"
        "2,Committee C,Donor D,200,05/26/2026,\r\n"
    )
    with patch("httpx.get", return_value=_response(content=_zipped(csv_text))):
        result = OrestarAdapter(live=True, feed_url=PLAIN_URL, cache_dir=tmp_path, retry_backoff_s=0).safe_fetch()

    assert len(result) == 1, "the undated row is dropped, the dated one survives"
    assert "Committee C" in result.signals[0].text


def test_orestar_falls_back_to_transaction_date_when_filed_date_missing(tmp_path):
    csv_text = "Tran Id,Filer,Contributor/Payee,Amount,Tran Date\r\n1,A,B,100,05/26/2026\r\n"
    with patch("httpx.get", return_value=_response(content=_zipped(csv_text))):
        result = OrestarAdapter(live=True, feed_url=PLAIN_URL, cache_dir=tmp_path, retry_backoff_s=0).safe_fetch()

    assert len(result) == 1
    assert result.signals[0].published_at.date() == datetime(2026, 5, 26).date()


def test_orestar_still_reads_the_fixture_json(fixture_dir):
    """The fixture path must keep working -- CI replays it on every run."""
    result = OrestarAdapter(fixture_path=fixture_dir / "orestar.json").safe_fetch()
    assert result.ok
    assert len(result) == 3


def test_orestar_default_feed_is_the_transaction_search():
    """The bulk ZIP path 404s; the default is the public search, windowed per fetch."""
    adapter = OrestarAdapter()
    assert "cneSearch.do" in adapter.feed_url
    assert "{start}" in adapter.feed_url and "{end}" in adapter.feed_url


def _xlsx(rows: list[list[str]]) -> bytes:
    """A minimal one-sheet .xlsx using inline strings, the way a test can build one."""
    def cell(col: int, row: int, value: str) -> str:
        ref = chr(65 + col) + str(row)
        return f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>'

    body = "".join(
        f'<row r="{r}">' + "".join(cell(c, r, v) for c, v in enumerate(vals)) + "</row>"
        for r, vals in enumerate(rows, start=1)
    )
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook xmlns="{ns}"/>')
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{ns}"><sheetData>{body}</sheetData></worksheet>')
    return buf.getvalue()


# Header spellings copied from a live XcelCNESearch export, 2026-09-22.
_LIVE_HEADER = ["Tran Id", "Tran Date", "Filer", "Contributor/Payee", "Sub Type",
                "Amount", "Aggregate Amount", "Filer Id", "Filed Date", "Emp Name",
                "City", "State", "Purp Desc"]
_LIVE_ROW = ["5818088", "09/21/2026", "Oregon Nurse Anesthetist PAC", "Kyle Bowling",
             "Cash Contribution", "20", "180", "3099", "09/21/2026",
             "Northwest Permanente", "Portland", "OR", ""]


def _search_page(found: int) -> MagicMock:
    page = MagicMock()
    page.text = f"<td>{found} records found for the above search criteria</td>"
    page.cookies = {"JSESSIONID": "abc"}
    page.raise_for_status = MagicMock()
    return page


def _export(content: bytes) -> MagicMock:
    resp = MagicMock()
    resp.content = content
    resp.headers = {"content-type": "application/vnd.ms-excel"}
    resp.raise_for_status = MagicMock()
    return resp


def test_orestar_searches_then_exports_in_the_same_session():
    workbook = _xlsx([_LIVE_HEADER, _LIVE_ROW])
    with patch("httpx.get", side_effect=[_search_page(1), _export(workbook)]) as get:
        result = OrestarAdapter(live=True, retry_backoff_s=0).safe_fetch()

    assert result.ok, result.errors
    assert len(result) == 1
    text = result.signals[0].text
    assert "Oregon Nurse Anesthetist PAC (3099)" in text
    assert "Kyle Bowling of Portland, OR" in text
    assert "employer or affiliation Northwest Permanente" in text
    assert "$20.00" in text

    search_call, export_call = get.call_args_list
    assert "cneSearch.do" in search_call.args[0]
    assert "{start}" not in search_call.args[0]
    assert export_call.args[0].endswith("XcelCNESearch")
    assert export_call.kwargs["cookies"] == {"JSESSIONID": "abc"}


def test_orestar_empty_window_skips_the_export():
    with patch("httpx.get", side_effect=[_search_page(0)]) as get:
        result = OrestarAdapter(live=True, retry_backoff_s=0).safe_fetch()
    assert result.ok, result.errors
    assert len(result) == 0
    assert get.call_count == 1


def test_orestar_window_over_the_row_cap_is_split():
    """ORESTAR exports at most 5,000 rows; a capped window is halved, not truncated."""
    workbook = _xlsx([_LIVE_HEADER, _LIVE_ROW])
    responses = [
        _search_page(6000),                      # 2-day window: over the cap
        _search_page(3000), _export(workbook),   # day 1
        _search_page(3000), _export(workbook),   # day 2
    ]
    with patch("httpx.get", side_effect=responses) as get:
        result = OrestarAdapter(live=True, lookback_days=2, retry_backoff_s=0).safe_fetch()

    assert result.ok, result.errors
    assert len(result) == 2
    assert get.call_count == 5


def test_orestar_export_that_is_not_a_workbook_is_reported():
    page = _export(b"<html>session expired</html>")
    page.headers = {"content-type": "text/html"}
    with patch("httpx.get", side_effect=[_search_page(1), page]):
        result = OrestarAdapter(live=True, max_attempts=1, retry_backoff_s=0).safe_fetch()
    assert not result.ok
    assert "not a workbook" in result.errors[0]


# ── OLIS: OData envelope + paging ─────────────────────────────────────────────


def _measure(number: int = 1147, **overrides) -> dict:
    record = {
        "MeasurePrefix": "SB",
        "MeasureNumber": number,
        "CatchLine": "Relating to energy facility siting review timelines",
        "SessionKey": "2026R1",
        "CurrentLocation": "In committee upon adjournment",
        "MeasureSummary": "Modifies the review period for siting applications.",
        "ActionDate": "2026-05-27T19:30:00Z",
    }
    record.update(overrides)
    return record


def test_olis_reads_the_odata_envelope(tmp_path):
    with patch("httpx.get", return_value=_response(payload={"value": [_measure()]})):
        result = OlisAdapter(live=True, cache_dir=tmp_path).safe_fetch()

    assert result.ok, result.errors
    assert len(result) == 1
    text = result.signals[0].text
    assert "SB1147" in text, "prefix and number recombine into the cited measure id"
    assert "energy facility siting" in text
    assert "2026R1" in text


def test_olis_follows_the_next_link(tmp_path):
    page_two = _response(payload={"value": [_measure(2)]})
    page_one = _response(
        payload={"value": [_measure(1)], "@odata.nextLink": "https://example.invalid/page2"}
    )

    # `sessions=` pins the session list so no LegislativeSessions request is made and
    # the call count measures the Measures walk alone.
    with patch("httpx.get", side_effect=[page_one, page_two]) as mock_get:
        result = OlisAdapter(live=True, cache_dir=tmp_path, sessions=["2026R1"]).safe_fetch()

    assert mock_get.call_count == 2
    assert len(result) == 2


def test_olis_follows_a_relative_next_link(tmp_path):
    """
    OData permits a relative nextLink and OLIS serves one.

    Regression test for the first real run against the live service: page 1 returned
    200, then the walk died with `UnsupportedProtocol: Request URL is missing an
    'http://' or 'https://' protocol` because the relative link went to httpx as-is.
    """
    page_one = _response(
        payload={"value": [_measure(1)], "@odata.nextLink": "Measures?$skiptoken=100"}
    )
    page_one.url = "https://api.oregonlegislature.gov/odata/odataservice.svc/Measures?$format=json"
    page_two = _response(payload={"value": [_measure(2)]})

    with patch("httpx.get", side_effect=[page_one, page_two]) as mock_get:
        result = OlisAdapter(live=True, cache_dir=tmp_path, sessions=["2026R1"]).safe_fetch()

    assert result.ok, result.errors
    assert len(result) == 2

    second_url = mock_get.call_args_list[1].args[0]
    assert second_url.startswith("https://"), f"relative link was not resolved: {second_url}"

    parts = urlsplit(second_url)
    assert parts.path == "/odata/odataservice.svc/Measures"
    query = dict(parse_qsl(parts.query))
    assert query["$skiptoken"] == "100"
    # OLIS drops $format from its own nextLink, so page 2 comes back as Atom XML unless
    # the walk puts it back. This is the second half of that first-run failure: fixing
    # the relative URL only got the walk as far as an unparseable page.
    assert query["$format"] == "json"


def test_olis_absolute_next_link_is_left_alone(tmp_path):
    """Resolving a relative link must not mangle an absolute one."""
    absolute = "https://api.oregonlegislature.gov/odata/odataservice.svc/Measures?$skip=100"
    page_one = _response(payload={"value": [_measure(1)], "@odata.nextLink": absolute})
    page_one.url = "https://api.oregonlegislature.gov/odata/odataservice.svc/Measures"
    page_two = _response(payload={"value": [_measure(2)]})

    with patch("httpx.get", side_effect=[page_one, page_two]) as mock_get:
        OlisAdapter(live=True, cache_dir=tmp_path, sessions=["2026R1"]).safe_fetch()

    second_url = mock_get.call_args_list[1].args[0]
    parts = urlsplit(second_url)
    # Same scheme, host and path as the link the service gave -- urljoin did not
    # rewrite it against the base. Only $format is added.
    assert (parts.scheme, parts.netloc, parts.path) == urlsplit(absolute)[:3]
    assert dict(parse_qsl(parts.query)) == {"$skip": "100", "$format": "json"}


def test_olis_paging_stops_at_the_ceiling(tmp_path):
    """A nextLink that never clears must not loop forever."""
    from pdx1.sources.olis import MAX_PAGES

    looping = _response(
        payload={"value": [_measure()], "@odata.nextLink": "https://example.invalid/next"}
    )

    with patch("httpx.get", return_value=looping) as mock_get:
        result = OlisAdapter(live=True, cache_dir=tmp_path, sessions=["2026R1"]).safe_fetch()

    assert mock_get.call_count == MAX_PAGES
    assert len(result) == MAX_PAGES


def test_olis_drops_measures_with_no_readable_date(tmp_path):
    undated = _measure(999)
    del undated["ActionDate"]

    with patch("httpx.get", return_value=_response(payload={"value": [undated, _measure()]})):
        result = OlisAdapter(live=True, cache_dir=tmp_path).safe_fetch()

    assert len(result) == 1
    assert "SB1147" in result.signals[0].text


def test_olis_warns_when_every_record_is_undated(tmp_path, caplog):
    """A silent empty result would read as a quiet session rather than a broken map."""
    undated = _measure()
    del undated["ActionDate"]

    with patch("httpx.get", return_value=_response(payload={"value": [undated]})):
        with caplog.at_level("WARNING"):
            result = OlisAdapter(live=True, cache_dir=tmp_path).safe_fetch()

    assert len(result) == 0
    assert any("none carried a readable date" in r.message for r in caplog.records)


def test_olis_constructs_a_measure_url_when_absent(tmp_path):
    with patch("httpx.get", return_value=_response(payload={"value": [_measure()]})):
        result = OlisAdapter(live=True, cache_dir=tmp_path).safe_fetch()

    assert result.signals[0].url == (
        "https://olis.oregonlegislature.gov/liz/2026R1/Measures/Overview/SB1147"
    )


def test_olis_prefers_a_url_the_payload_supplies(tmp_path):
    measure = _measure(MeasureUrl="https://example.invalid/measure")
    with patch("httpx.get", return_value=_response(payload={"value": [measure]})):
        result = OlisAdapter(live=True, cache_dir=tmp_path).safe_fetch()

    assert result.signals[0].url == "https://example.invalid/measure"


def test_olis_handles_a_bare_array_endpoint(tmp_path):
    with patch("httpx.get", return_value=_response(payload=[_measure()])):
        result = OlisAdapter(live=True, cache_dir=tmp_path).safe_fetch()

    assert len(result) == 1


def test_olis_still_reads_the_fixture_json(fixture_dir):
    result = OlisAdapter(fixture_path=fixture_dir / "olis.json").safe_fetch()
    assert result.ok
    assert len(result) == 2


def test_olis_combined_pages_are_cached_as_one_array(tmp_path):
    with patch("httpx.get", return_value=_response(payload={"value": [_measure()]})):
        adapter = OlisAdapter(live=True, cache_dir=tmp_path)
        assert adapter.safe_fetch().ok

    cached = json.loads(adapter.cache_path().read_text(encoding="utf-8"))
    assert isinstance(cached, list) and len(cached) == 1


# ── Socrata column types ─────────────────────────────────────────────────────


def test_socrata_url_column_object_is_unwrapped():
    """
    A Socrata `url` column serialises as an object, not a string.

    Found against the live WA PDC dataset: `{"url": "...", "description": "..."}`
    went straight into `Signal.url`, pydantic rejected it, and `parse` raised -- which
    takes down the *whole feed*, not one row. Downstream that reads as "Washington
    filed no contributions today", which is a false statement about public records.
    """
    from pdx1.sources.wa_pdc import _socrata_url

    assert _socrata_url({"url": "https://example.invalid/r/1"}) == "https://example.invalid/r/1"
    assert _socrata_url({"url": "https://example.invalid/r/1", "description": "x"}) == (
        "https://example.invalid/r/1"
    )


def test_socrata_url_accepts_the_plain_string_form():
    """Not every dataset declares the column as a url type; the adapter shouldn't care."""
    from pdx1.sources.wa_pdc import _socrata_url

    assert _socrata_url("https://example.invalid/r/2") == "https://example.invalid/r/2"
    assert _socrata_url("  https://example.invalid/r/3  ") == "https://example.invalid/r/3"


def test_socrata_url_absent_or_empty_is_none_not_a_crash():
    """A missing URL is a row with no link, never a dead feed."""
    from pdx1.sources.wa_pdc import _socrata_url

    for value in (None, "", "   ", {}, {"description": "no url key"}, 42, []):
        assert _socrata_url(value) is None, value


def test_a_url_object_does_not_take_down_the_whole_feed():
    """
    The regression, at the level that matters.

    One malformed column used to raise out of `parse` and cost every row in the
    payload. Rule 6: a dead feed must never halt a cycle -- and a feed that returns
    nothing because of one column is the same failure wearing a different hat.
    """
    import json

    from pdx1.sources.wa_pdc import WaPdcAdapter

    rows = [
        {
            "id": "1",
            "filer_name": "Committee A",
            "contributor_name": "Donor A",
            "amount": "100.00",
            "receipt_date": "2026-01-15T00:00:00.000",
            "url": {"url": "https://example.invalid/report/1"},
        },
        {
            "id": "2",
            "filer_name": "Committee B",
            "contributor_name": "Donor B",
            "amount": "250.00",
            "receipt_date": "2026-01-16T00:00:00.000",
            "url": "https://example.invalid/report/2",
        },
    ]

    signals = WaPdcAdapter().parse(json.dumps(rows))

    assert len(signals) == 2, "one bad column must not cost the other rows"
    assert str(signals[0].url) == "https://example.invalid/report/1"
    assert str(signals[1].url) == "https://example.invalid/report/2"


# ── ORESTAR: nothing unmapped reaches the cache ───────────────────────────────

_PRIVATE_HEADER = [*_LIVE_HEADER, "Addr Line1", "Zip"]
_PRIVATE_ROW = [*_LIVE_ROW, "1234 NE Example St", "97211"]


def test_orestar_cache_holds_only_mapped_columns(tmp_path):
    """The live export carries donor street addresses; the cache must not."""
    workbook = _xlsx([_PRIVATE_HEADER, _PRIVATE_ROW])
    adapter = OrestarAdapter(live=True, cache_dir=tmp_path, retry_backoff_s=0)
    with patch("httpx.get", side_effect=[_search_page(1), _export(workbook)]):
        result = adapter.safe_fetch()

    assert result.ok, result.errors
    assert len(result) == 1
    cached = adapter.cache_path().read_text(encoding="utf-8")
    assert "Addr Line1" not in cached
    assert "1234 NE Example St" not in cached
    assert "97211" not in cached
    # What parse needs is still there, so a cache fallback reads the same records.
    assert "Oregon Nurse Anesthetist PAC" in cached
    assert "1234 NE Example St" not in result.signals[0].text


def test_orestar_plain_csv_override_is_projected_too(tmp_path):
    csv_text = ",".join(_PRIVATE_HEADER) + "\n" + ",".join(_PRIVATE_ROW) + "\n"
    adapter = OrestarAdapter(live=True, feed_url=PLAIN_URL, cache_dir=tmp_path, retry_backoff_s=0)
    with patch("httpx.get", return_value=_response(content=_zipped(csv_text))):
        assert adapter.safe_fetch().ok
    cached = adapter.cache_path().read_text(encoding="utf-8")
    assert "Zip" not in cached.splitlines()[0]
    assert "97211" not in cached
