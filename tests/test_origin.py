"""
Every signal and record says where its payload came from.

A fixture replay emits invented records with real-looking URLs. On 2026-11-01 eight
of them, written to the same store as a live run, reached the newsroom ledger as
leads. `origin` is what lets a consumer refuse them; the replay-store default keeps
them out of the live store in the first place.

Nothing here touches the network -- `httpx.get` is patched throughout.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest

from pdx1.config import Settings
from pdx1.models import IntelligenceRecord
from pdx1.pipeline import default_adapters, run_cycle
from pdx1.sources.orestar import OrestarAdapter
from pdx1.store import DualWriteStore

PLAIN_URL = "https://example.invalid/feed.json"


def _response(status: int, text: str = "") -> httpx.Response:
    return httpx.Response(
        status_code=status, text=text, request=httpx.Request("GET", PLAIN_URL)
    )


def _live(**kwargs) -> OrestarAdapter:
    return OrestarAdapter(live=True, feed_url=PLAIN_URL, retry_backoff_s=0, **kwargs)


def _origins(result) -> set:
    return {signal.origin for signal in result.signals}


def test_fixture_replay_is_stamped_fixture(fixture_dir):
    result = OrestarAdapter(fixture_path=fixture_dir / "orestar.json").safe_fetch()
    assert len(result) == 3
    assert _origins(result) == {"fixture"}


def test_fixture_path_wins_even_on_a_live_adapter(fixture_dir):
    """`_read_raw` reads fixture_path first, so the stamp must say fixture too."""
    adapter = OrestarAdapter(
        fixture_path=fixture_dir / "orestar.json", live=True, feed_url=PLAIN_URL
    )
    with patch("httpx.get") as get:
        result = adapter.safe_fetch()
    get.assert_not_called()
    assert _origins(result) == {"fixture"}


def test_live_fetch_is_stamped_live(fixture_dir):
    body = (fixture_dir / "orestar.json").read_text(encoding="utf-8")
    with patch("httpx.get", return_value=_response(200, body)):
        result = _live().safe_fetch()
    assert _origins(result) == {"live"}


def test_cache_fallback_is_stamped_cache_and_resets(tmp_path, fixture_dir):
    body = (fixture_dir / "orestar.json").read_text(encoding="utf-8")
    adapter = _live(cache_dir=tmp_path)

    with patch("httpx.get", return_value=_response(200, body)):
        assert _origins(adapter.safe_fetch()) == {"live"}
    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        cached = adapter.safe_fetch()
    assert cached.from_cache is True
    assert _origins(cached) == {"cache"}

    with patch("httpx.get", return_value=_response(200, body)):
        again = adapter.safe_fetch()
    assert again.from_cache is False
    assert _origins(again) == {"live"}


def test_records_carry_origin_into_ground_truth(tmp_path, fixture_dir):
    settings = Settings(store_path=tmp_path / "s.jsonl", db_path=tmp_path / "s.db")
    store = DualWriteStore(settings.store_path, settings.db_path)
    result = run_cycle(
        settings=settings, adapters=default_adapters(settings, fixture_dir), store=store
    )
    assert result.records
    lines = settings.store_path.read_text(encoding="utf-8").splitlines()
    written = [json.loads(line) for line in lines if line.strip()]
    assert written and {row["origin"] for row in written} == {"fixture"}


def test_a_pre_origin_record_still_loads():
    """Records written before the field existed parse, with origin unknown."""
    row = {
        "record_id": "rec_old",
        "run_id": "pdx1_2026_0806_040600",
        "source": "ORESTAR",
        "source_type": "ORESTAR",
        "pattern": "x",
        "outcome": "MONITOR",
        "priority": "MONITOR",
        "confidence": 0.7,
        "tier": "HARD_RECORD",
        "gates": {
            "credibility": True,
            "volume": True,
            "velocity": True,
            "novelty": True,
            "detail": {},
        },
        "signal_id": "sig_old",
        "published_at": "2026-05-27T16:40:00Z",
    }
    assert IntelligenceRecord.model_validate(row).origin is None


@pytest.mark.parametrize(
    "live, store, db",
    [
        ("false", "pdx1_signals_replay.jsonl", "pdx1_replay.db"),
        ("true", "pdx1_signals.jsonl", "pdx1.db"),
    ],
)
def test_replay_defaults_to_its_own_store(monkeypatch, live, store, db):
    monkeypatch.delenv("PDX1_STORE_PATH", raising=False)
    monkeypatch.delenv("PDX1_DB_PATH", raising=False)
    monkeypatch.setenv("PDX1_ENVIRONMENT", "development")
    monkeypatch.setenv("PDX1_LIVE", live)
    settings = Settings.from_env()
    assert str(settings.store_path) == store
    assert str(settings.db_path) == db


def test_an_explicit_store_path_still_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("PDX1_ENVIRONMENT", "development")
    monkeypatch.setenv("PDX1_LIVE", "false")
    monkeypatch.setenv("PDX1_STORE_PATH", str(tmp_path / "mine.jsonl"))
    assert Settings.from_env().store_path == tmp_path / "mine.jsonl"


# ── evidence_url ─────────────────────────────────────────────────────────────

from urllib.parse import parse_qs, urlsplit  # noqa: E402

from pdx1.sources.wa_pdc import EVIDENCE_COLUMNS, WaPdcAdapter  # noqa: E402

SOCRATA_ROW = {
    "id": "21807083",
    "report_number": "110401493",
    "filer_name": "Yes for Wenatchee Schools",
    "type": "Political Committee",
    "jurisdiction": "WENATCHEE SD 246",
    "election_year": "2026",
    "amount": "96.62",
    "cash_or_in_kind": "Cash",
    "receipt_date": "2026-11-01T00:00:00.000",
    "contributor_name": "SMALL CONTRIBUTIONS",
    "url": {"url": "https://my.pdc.wa.gov/public/registrations/campaign-finance-report/110401493"},
}


def test_wa_pdc_evidence_url_is_the_projected_socrata_row():
    signal = WaPdcAdapter().parse(json.dumps([SOCRATA_ROW]))[0]
    assert signal.url.startswith("https://my.pdc.wa.gov/")  # the brief's link is unchanged
    parts = urlsplit(signal.evidence_url)
    assert (parts.scheme, parts.netloc, parts.path) == (
        "https", "data.wa.gov", "/resource/kv7h-kjye.json"
    )
    query = parse_qs(parts.query)
    assert query["id"] == ["21807083"]
    assert query["$select"] == [EVIDENCE_COLUMNS]
    selected = set(EVIDENCE_COLUMNS.split(","))
    assert not {c for c in selected if "address" in c or "zip" in c or c == "contributor_name"}


def test_wa_pdc_evidence_url_needs_a_numeric_receipt_and_a_socrata_feed():
    row = dict(SOCRATA_ROW, id="not-a-number")
    assert WaPdcAdapter().parse(json.dumps([row]))[0].evidence_url is None
    elsewhere = WaPdcAdapter(feed_url="https://example.invalid/pdc.json")
    assert elsewhere.parse(json.dumps([SOCRATA_ROW]))[0].evidence_url is None


def test_evidence_url_reaches_the_record(tmp_path, fixture_dir):
    settings = Settings(store_path=tmp_path / "s.jsonl", db_path=tmp_path / "s.db")
    store = DualWriteStore(settings.store_path, settings.db_path)
    run_cycle(settings=settings, adapters=default_adapters(settings, fixture_dir), store=store)
    rows = [json.loads(line) for line in settings.store_path.read_text().splitlines() if line]
    by_source = {row["source_type"]: row for row in rows}
    assert "evidence_url" in by_source["ORESTAR"] and by_source["ORESTAR"]["evidence_url"] is None


def test_wa_pdc_evidence_url_ignores_a_report_number_fallback():
    """Without Socrata's `id` column, receipt_id comes from report_number: no URL."""
    row = {k: v for k, v in SOCRATA_ROW.items() if k != "id"}
    signal = WaPdcAdapter().parse(json.dumps([row]))[0]
    assert "110401493" in signal.text  # the report number did become receipt_id
    assert signal.evidence_url is None


def test_signals_route_returns_origin_and_evidence_url(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from pdx1.api.app import create_app
    from pdx1.models import GateResult

    monkeypatch.setenv("PDX1_STORE_PATH", str(tmp_path / "s.jsonl"))
    monkeypatch.setenv("PDX1_DB_PATH", str(tmp_path / "s.db"))
    monkeypatch.delenv("PDX1_API_KEY", raising=False)
    store = DualWriteStore(tmp_path / "s.jsonl", tmp_path / "s.db")
    record = IntelligenceRecord(
        record_id="rec_pdc",
        run_id="pdx1_2026_1101_200057",
        source="WA_PDC",
        source_type="WA_PDC",
        pattern="Washington PDC receipt 21807083",
        outcome="MONITOR",
        priority="MONITOR",
        confidence=0.7,
        tier="HARD_RECORD",
        gates=GateResult(credibility=True, volume=True, velocity=True, novelty=True),
        signal_id="sig_pdc",
        url="https://my.pdc.wa.gov/public/registrations/campaign-finance-report/110401493",
        published_at="2026-11-01T00:00:00Z",
        origin="live",
        evidence_url="https://data.wa.gov/resource/kv7h-kjye.json?id=21807083",
    )
    store.write([record])
    with TestClient(create_app()) as client:
        item = client.get("/signals").json()["items"][0]
    assert item["origin"] == "live"
    assert item["evidence_url"].endswith("id=21807083")
