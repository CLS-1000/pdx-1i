"""
Session resolution.

The adapter must not be told which session to read. Hardcoding one means the adapter
goes quiet the moment the legislature moves on, and the quiet looks exactly like a
session with no activity.

The specific trap: `DefaultSession` currently points at `2025I1`, the *interim*, which
carries no measure actions at all. An adapter that trusted it would report healthy
every cycle and harvest nothing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from pdx1.sources import OlisAdapter
from pdx1.sources.olis import _select_sessions

NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)

#: Shaped like the real LegislativeSessions payload, including the null EndDate that
#: makes that field useless as a filter.
SESSIONS = [
    {"SessionKey": "2023R1", "BeginDate": "2023-01-09T00:00:00", "EndDate": None, "DefaultSession": False},
    {"SessionKey": "2024R1", "BeginDate": "2024-02-05T00:00:00", "EndDate": None, "DefaultSession": False},
    {"SessionKey": "2025I1", "BeginDate": "2025-06-27T00:00:00", "EndDate": None, "DefaultSession": True},
    {"SessionKey": "2025R1", "BeginDate": "2025-01-13T00:00:00", "EndDate": None, "DefaultSession": False},
    {"SessionKey": "2025S1", "BeginDate": "2025-08-29T00:00:00", "EndDate": None, "DefaultSession": False},
    {"SessionKey": "2026R1", "BeginDate": "2026-02-02T00:00:00", "EndDate": None, "DefaultSession": False},
]


def test_interim_sessions_are_dropped():
    """`2025I1` is the DefaultSession and must still not be selected."""
    selected = _select_sessions(SESSIONS, lookback_days=900, now=NOW)

    assert "2025I1" not in selected
    assert not [key for key in selected if "I" in key]


def test_default_session_is_not_consulted():
    """
    Flipping DefaultSession onto a different key changes nothing.

    If this ever fails, the resolver has started reading the field and the adapter is
    one interim away from going silently dead.
    """
    baseline = _select_sessions(SESSIONS, lookback_days=900, now=NOW)
    moved = [{**row, "DefaultSession": row["SessionKey"] == "2023R1"} for row in SESSIONS]

    assert _select_sessions(moved, lookback_days=900, now=NOW) == baseline


def test_lookback_window_bounds_the_selection():
    assert _select_sessions(SESSIONS, lookback_days=540, now=NOW) == ["2025S1", "2026R1"]
    assert _select_sessions(SESSIONS, lookback_days=900, now=NOW) == [
        "2025R1",
        "2025S1",
        "2026R1",
    ]


def test_empty_window_falls_back_to_the_newest_non_interim():
    """Harvesting one stale session beats harvesting nothing."""
    assert _select_sessions(SESSIONS, lookback_days=1, now=NOW) == ["2026R1"]


def test_fallback_never_returns_an_interim_key():
    interim_newest = SESSIONS + [
        {"SessionKey": "2027I1", "BeginDate": "2027-06-01T00:00:00", "EndDate": None, "DefaultSession": True}
    ]

    assert _select_sessions(interim_newest, lookback_days=1, now=NOW) == ["2026R1"]


def test_undated_sessions_are_skipped():
    rows = [{"SessionKey": "2026R1", "BeginDate": None, "DefaultSession": True}]

    assert _select_sessions(rows, lookback_days=540, now=NOW) == []


def test_explicit_override_skips_the_network_entirely():
    adapter = OlisAdapter(live=True, sessions=["2026R1", "2025S1"])

    with patch("httpx.get") as mock_get:
        assert adapter.resolve_sessions(now=NOW) == ["2026R1", "2025S1"]

    mock_get.assert_not_called()


def test_resolution_reads_the_sessions_collection():
    response = MagicMock()
    response.json.return_value = {"value": SESSIONS}
    response.url = OlisAdapter.feed_url
    response.raise_for_status = MagicMock()

    adapter = OlisAdapter(live=True, session_lookback_days=540)
    with patch("httpx.get", return_value=response) as mock_get:
        selected = adapter.resolve_sessions(now=NOW)

    assert selected == ["2025S1", "2026R1"]
    requested = mock_get.call_args[0][0]
    assert "LegislativeSessions" in requested
    # Without $format=json the service answers in Atom XML.
    assert "%24format=json" in requested or "$format=json" in requested
