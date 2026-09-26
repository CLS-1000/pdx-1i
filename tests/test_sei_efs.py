"""
SEI live fetch against OGEC's EFS public records -- all HTTP patched.

The shapes below are trimmed copies of real EFS responses captured 2026-09-22.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from pdx1.sources import SeiAdapter

LOOKUP = [
    {"OfficeID": 1433, "OfficeName": "Council", "JurisdictionID": 472,
     "JurisdictionName": "PORTLAND", "CategoryID": 0, "CategoryName": "City"},
    {"OfficeID": 1131, "OfficeName": "COUNCIL", "JurisdictionID": 362,
     "JurisdictionName": "METRO", "CategoryID": 2, "CategoryName": "State"},
]
GRID = {"current": 1, "rowCount": -1, "total": 1, "rows": [
    {"FilerID": 111, "LastName": "Doe", "FirstName": "Jan", "Year": 2026}]}
PROFILE = """
<table id="officesTable" class="grid"><caption>Offices Held</caption>
<thead><tr><th>Jurisdiction</th><th>Office Held</th><th>Appointment Date</th></tr></thead>
<tbody><tr><td> PORTLAND </td><td> Council </td><td> 01-01-2025 </td></tr>
<tr><td> SOMEWHERE ELSE </td><td> Board </td><td> 01-01-2020 </td></tr></tbody></table>
<table id="reportsTable"><tbody>
<tr><td> <a href='/OGEC/EFS/SEIReport/ViewReport/900'> 2025 </a></td><td> 04-01-2025</td><td> Filed</td></tr>
<tr><td> <a href='/OGEC/EFS/SEIReport/ViewReport/950'> 2026 </a></td><td> 04-13-2026</td><td> Filed</td></tr>
</tbody></table>
"""
MODEL = {
    "SEIReport": {
        "ID": 950, "Year": 2026, "DateFiled": "2026-04-13T18:04:17.177",
        "SourcesOfIncome": [{"NameOfSource": "City of Portland",
                             "DescriptionOfSource": "Salary", "BusinessAddress": "1221 SW 4th Ave"}],
        "BusinessOfficeOrDirectorshipHousehold": [{
            "BusinessName": "Acme LLC", "AddressLine": "1 Secret St", "City": "Portland",
            "State": "OR", "ZipCode": "97201", "DescriptionOfBusiness": "Consulting",
            "HeldByWhom": "Spouse Name"}],
        "RealProperty": [{"Description": "Rental duplex", "AddressLine": "2 Private Rd",
                          "City": "Gresham", "State": "OR", "ZipCode": "97030"}],
        "DebtOfThousandOrMore": None,
    },
    "SEIUser": {"Email": "home@example.com", "Address": "9 Home Ct", "Phone": "5035550100"},
}
REPORT = "<script>\n var model = " + json.dumps(MODEL) + ";\n</script>"


def _resp(url: str):
    r = MagicMock()
    r.raise_for_status = MagicMock()
    if "GetJurisdictionLookupData" in url:
        r.json.return_value = LOOKUP
    elif "GetGridData" in url:
        r.json.return_value = GRID if "472" in url else {"rows": []}
    elif "GetUserProfile" in url:
        r.text = PROFILE
    elif "ViewReport/950" in url:
        r.text = REPORT
    else:
        raise AssertionError(f"unexpected URL {url}")
    return r


def _fetch(**kwargs):
    with patch("httpx.get", side_effect=lambda url, **kw: _resp(url)) as get:
        result = SeiAdapter(
            live=True, year=2026, request_delay_s=0, retry_backoff_s=0,
            jurisdictions=("PORTLAND", "METRO"), **kwargs,
        ).safe_fetch()
    return result, get


def test_efs_walk_yields_one_signal_per_filing_year_report():
    result, get = _fetch()
    assert result.ok, result.errors
    assert len(result) == 1
    text = result.signals[0].text
    assert "SEI-950" in text
    assert "seat Council on PORTLAND" in text
    assert "covering calendar year 2025" in text
    assert "Prior-year filing for the same seat is SEI-900" in text
    assert "Source of income -- Salary (City of Portland)" in text
    assert "Real property -- Rental duplex (Gresham, OR)" in text
    assert result.signals[0].url.endswith("SEIReport/ViewReport/950")
    # The 2025 report is only referenced, never fetched.
    assert not any("ViewReport/900" in c.args[0] for c in get.call_args_list)


def test_personal_details_never_reach_the_signal():
    result, _ = _fetch()
    text = result.signals[0].text
    for leaked in ("home@example.com", "9 Home Ct", "5035550100",
                   "1 Secret St", "2 Private Rd", "Spouse Name", "97201", "1221 SW 4th"):
        assert leaked not in text, leaked


def test_personal_details_never_reach_the_cache(tmp_path):
    result, _ = _fetch(cache_dir=tmp_path)
    assert result.ok
    cached = "".join(p.read_text(encoding="utf-8") for p in tmp_path.rglob("*") if p.is_file())
    assert cached, "expected a cache file"
    for leaked in ("home@example.com", "9 Home Ct", "Spouse Name", "2 Private Rd"):
        assert leaked not in cached, leaked


def test_offices_outside_the_watched_jurisdictions_are_not_the_seat():
    result, _ = _fetch()
    assert "SOMEWHERE ELSE" not in result.signals[0].text


def test_unknown_jurisdiction_is_skipped_not_fatal():
    with patch("httpx.get", side_effect=lambda url, **kw: _resp(url)):
        result = SeiAdapter(
            live=True, year=2026, request_delay_s=0,
            jurisdictions=("PORTLAND", "NOT A PLACE"),
        ).safe_fetch()
    assert result.ok
    assert len(result) == 1
