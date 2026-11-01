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
