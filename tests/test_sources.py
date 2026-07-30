"""
Source adapters.

Two things matter here: the parse produces well-formed signals, and a broken feed
degrades only itself. The second is what keeps a dead RSS endpoint from taking down a
cycle.
"""

from __future__ import annotations

import pytest

from pdx1.models import SourceType
from pdx1.sources import (
    OlisAdapter,
    OrestarAdapter,
    PortlandPressAdapter,
    SeiAdapter,
    WaPdcAdapter,
)
from pdx1.sources.base import SourceAdapter

ADAPTERS = [
    (OrestarAdapter, "orestar.json", SourceType.ORESTAR, 3),
    (OlisAdapter, "olis.json", SourceType.OLIS, 2),
    (SeiAdapter, "sei.json", SourceType.SEI, 2),
    (WaPdcAdapter, "wa_pdc.json", SourceType.WA_PDC, 2),
    (PortlandPressAdapter, "portland_press.xml", SourceType.PORTLAND_PRESS, 3),
]


@pytest.mark.parametrize(("cls", "fixture", "source_type", "count"), ADAPTERS)
def test_adapter_parses_its_fixture(fixture_dir, cls, fixture, source_type, count):
    result = cls(fixture_path=fixture_dir / fixture).safe_fetch()

    assert result.ok, result.errors
    assert len(result) == count
    assert all(s.source_type is source_type for s in result.signals)


@pytest.mark.parametrize(("cls", "fixture", "source_type", "count"), ADAPTERS)
def test_signals_are_well_formed(fixture_dir, cls, fixture, source_type, count):
    for signal in cls(fixture_path=fixture_dir / fixture).fetch().signals:
        assert signal.signal_id.startswith("sig_")
        assert signal.text.strip()
        assert signal.published_at.tzinfo is not None
        assert 0.0 <= signal.credibility <= 1.0


@pytest.mark.parametrize(("cls", "fixture", "source_type", "count"), ADAPTERS)
def test_parsing_is_deterministic(fixture_dir, cls, fixture, source_type, count):
    """Same payload, same IDs -- otherwise dedup and idempotent writes both break."""
    adapter = cls(fixture_path=fixture_dir / fixture)
    first = [s.signal_id for s in adapter.fetch().signals]
    second = [s.signal_id for s in adapter.fetch().signals]
    assert first == second


def test_filed_records_outrank_press_on_credibility():
    """A filed record is a primary artifact; press reports that something was said."""
    assert OrestarAdapter().credibility > PortlandPressAdapter().credibility
    assert OlisAdapter().credibility > PortlandPressAdapter().credibility


# ── Fault tolerance ──────────────────────────────────────────────────────────


class _BrokenAdapter(SourceAdapter):
    name = "BROKEN"
    source_type = SourceType.ORESTAR

    def parse(self, raw: str):
        raise RuntimeError("upstream returned garbage")

    def _read_raw(self) -> str:
        return "{}"


def test_safe_fetch_contains_a_failure():
    result = _BrokenAdapter().safe_fetch()
    assert not result.ok
    assert result.signals == []
    assert "RuntimeError" in result.errors[0]


def test_fetch_propagates_while_safe_fetch_does_not():
    with pytest.raises(RuntimeError):
        _BrokenAdapter().fetch()
    assert _BrokenAdapter().safe_fetch().errors


def test_missing_fixture_is_reported_not_raised():
    result = OrestarAdapter(fixture_path="/nonexistent/orestar.json").safe_fetch()
    assert not result.ok
    assert result.signals == []


def test_adapter_without_fixture_reports_clearly():
    result = OrestarAdapter().safe_fetch()
    assert not result.ok
    assert "live fetch is not enabled" in result.errors[0]


# ── Per-adapter content ──────────────────────────────────────────────────────


def test_orestar_carries_transaction_detail(fixture_dir):
    signals = OrestarAdapter(fixture_path=fixture_dir / "orestar.json").fetch().signals
    text = signals[0].text
    assert "ORE-2026-4417392" in text
    assert "$12,500.00" in text


def test_olis_carries_committee_action(fixture_dir):
    signals = OlisAdapter(fixture_path=fixture_dir / "olis.json").fetch().signals
    assert "SB 1147" in signals[0].text
    assert "Work session" in signals[0].text


def test_sei_renders_declared_interests(fixture_dir):
    signals = SeiAdapter(fixture_path=fixture_dir / "sei.json").fetch().signals
    assert "OHSU" in signals[0].text
    assert "declared interest entries" in signals[0].text


def test_sei_carries_seats_not_names(fixture_dir):
    """Officials stay role-based throughout the module."""
    signals = SeiAdapter(fixture_path=fixture_dir / "sei.json").fetch().signals
    assert "Metro Councilor - District 4" in signals[0].text


def test_wa_pdc_marks_cross_border_state(fixture_dir):
    signals = WaPdcAdapter(fixture_path=fixture_dir / "wa_pdc.json").fetch().signals
    assert "Cross-border status" in signals[0].text
    assert "contributor state is OR" in signals[0].text


def test_press_skips_empty_entries():
    empty = """<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
    <item><link>https://example.org/a</link></item></channel></rss>"""
    assert PortlandPressAdapter().parse(empty) == []


def test_press_entry_without_date_still_parses():
    """A missing date must not drop the item -- the velocity gate judges it instead."""
    feed = """<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
    <item><title>Headline</title><description>Body text.</description>
    <link>https://example.org/a</link></item></channel></rss>"""
    signals = PortlandPressAdapter().parse(feed)
    assert len(signals) == 1
    assert signals[0].published_at.tzinfo is not None


def test_press_feed_names_five_tracked_outlets():
    from pdx1.sources.portland_press import FEEDS

    assert len(FEEDS) == 5
