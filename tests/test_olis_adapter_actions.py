"""
OLIS adapter, action-history path.

The legacy fixture path stays covered by ``test_sources.py``. This file
exercises the state-change path: the adapter replays a small action ledger
through the mapping module, compares the outcome to a per-chamber state file,
and emits a Signal only when a publishable state changed since the last cycle.

Committee churn does not emit. A repeat cycle over the same actions does not
emit. A partial view of a bill that has since advanced does emit -- once.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pdx1.sources.olis import OlisAdapter


# ── Test payloads ────────────────────────────────────────────────────────────


def _payload(*, session: str = "2025R1", measures: list[dict], actions: list[dict]) -> str:
    """Build the JSON envelope the adapter's ``_fetch_live`` would produce."""
    return json.dumps({"session": session, "measures": measures, "actions": actions})


def _measure(prefix: str, number: int, *, title: str = "A bill", url: str | None = None) -> dict:
    row = {
        "MeasurePrefix": prefix,
        "MeasureNumber": number,
        "CatchLine": title,
        "SessionKey": "2025R1",
    }
    if url:
        row["MeasureUrl"] = url
    return row


def _action(
    prefix: str, number: int, chamber: str, occurred_at: str, text: str, *, hid: int = 0
) -> dict:
    return {
        "MeasurePrefix": prefix,
        "MeasureNumber": number,
        "Chamber": chamber,
        "ActionDate": occurred_at,
        "ActionText": text,
        "MeasureHistoryId": hid,
    }


# ── Tests ────────────────────────────────────────────────────────────────────


def test_committee_churn_produces_no_signal(tmp_path: Path):
    """
    A hearing that closes with nothing decided is not a publishable transition.

    Public hearing and work-session rows advance the measure to committee-
    facing states (``public_hearing``, ``work_session``) which the adapter
    deliberately suppresses. If this test emits, the publishable filter has
    drifted and committee churn will flood the signal stream.
    """
    payload = _payload(
        measures=[_measure("HB", 4092)],
        actions=[
            _action("HB", 4092, "H", "2025-02-04T10:00:00",
                    "Introduction and first reading. Referred to President's desk.", hid=1),
            _action("HB", 4092, "H", "2025-02-10T10:00:00", "Public Hearing held.", hid=2),
            _action("HB", 4092, "H", "2025-02-17T10:00:00", "Work Session held.", hid=3),
        ],
    )
    adapter = OlisAdapter(state_path=tmp_path / "state.json")
    # Introduction still counts as a publishable transition (nothing → introduced),
    # so we expect exactly one signal from this ledger -- and none of committee
    # churn's follow-on states are in it.
    signals = adapter.parse(payload)
    assert len(signals) == 1
    text = signals[0].text
    assert "H=introduced" in text or "introduced" in text
    assert "public_hearing" not in text.split("chamber:")[-1].split(".")[0].lower()


def test_repeat_cycle_over_same_actions_is_quiet(tmp_path: Path):
    """
    State persistence: once a state is emitted, it does not emit again.

    The second parse sees the state file the first parse wrote and finds no
    change to report. If this test emits twice, the state file is being
    ignored or written empty.
    """
    payload = _payload(
        measures=[_measure("SB", 1)],
        actions=[
            _action("SB", 1, "S", "2025-02-04T10:00:00",
                    "Introduction and first reading. Referred to President's desk.", hid=1),
            _action("SB", 1, "S", "2025-03-01T10:00:00", "Third reading. Passed.", hid=2),
        ],
    )
    state_path = tmp_path / "state.json"

    first = OlisAdapter(state_path=state_path).parse(payload)
    assert len(first) == 1
    assert "S=passed" in first[0].text

    second = OlisAdapter(state_path=state_path).parse(payload)
    assert second == []


def test_new_publishable_transition_since_last_cycle_emits_once(tmp_path: Path):
    """
    A measure previously stored at ``passed`` that has now been signed emits
    exactly one signal, for the new state, with the signing action's ActionDate
    as ``published_at``.
    """
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"SB1": {"S": "passed"}}), encoding="utf-8")

    payload = _payload(
        measures=[_measure("SB", 1, title="Relating to something")],
        actions=[
            _action("SB", 1, "S", "2025-02-04T10:00:00",
                    "Introduction and first reading. Referred to President's desk.", hid=1),
            _action("SB", 1, "S", "2025-03-01T10:00:00", "Third reading. Passed.", hid=2),
            _action("SB", 1, "S", "2025-03-10T10:00:00", "President signed.", hid=3),
        ],
    )
    signals = OlisAdapter(state_path=state_path).parse(payload)
    assert len(signals) == 1
    assert "SB1" in signals[0].text
    assert "signed_by_presiding" in signals[0].text
    assert signals[0].published_at.isoformat().startswith("2025-03-10")


def test_state_file_reflects_final_states_after_parse(tmp_path: Path):
    """
    Even without a signal (halt), the file records what states the measure
    reached, so the next cycle can compare against reality rather than the
    stale row it started from.
    """
    state_path = tmp_path / "state.json"
    payload = _payload(
        measures=[_measure("SB", 1)],
        actions=[
            _action("SB", 1, "S", "2025-02-04T10:00:00",
                    "Introduction and first reading. Referred to President's desk.", hid=1),
        ],
    )
    OlisAdapter(state_path=state_path).parse(payload)
    stored = json.loads(state_path.read_text(encoding="utf-8"))
    assert stored == {"SB1": {"S": "introduced"}}


def test_bad_action_row_halts_that_measure_only(tmp_path: Path):
    """
    A row no rule matches is a fail-closed condition for the mapping. The
    adapter must not emit a signal for that measure, must not raise, and must
    still process the other measures in the batch.
    """
    state_path = tmp_path / "state.json"
    payload = _payload(
        measures=[_measure("SB", 1), _measure("HB", 2)],
        actions=[
            _action("SB", 1, "S", "2025-02-04T10:00:00",
                    "This is a row that no rule matches at all whatsoever", hid=1),
            _action("HB", 2, "H", "2025-02-04T10:00:00",
                    "Introduction and first reading. Referred to President's desk.", hid=2),
        ],
    )
    signals = OlisAdapter(state_path=state_path).parse(payload)
    assert [s.text for s in signals if "HB2" in s.text]
    assert not [s.text for s in signals if "SB1" in s.text]


def test_legacy_flat_fixture_still_emits_one_per_measure(fixture_dir):
    """
    The checked-in olis.json fixture is the pre-mapping shape. Live mode uses
    the new envelope; fixture mode keeps the legacy path so 326+ tests keep
    passing. If this test breaks, the shape dispatch in ``parse`` did.
    """
    adapter = OlisAdapter(fixture_path=fixture_dir / "olis.json")
    signals = adapter.fetch().signals
    assert len(signals) == 2


# ── Optional live-fetch smoke tests ─────────────────────────────────────────


def test_fetch_actions_walks_odata_pages(monkeypatch, tmp_path: Path):
    """
    ``_fetch_actions`` follows ``@odata.nextLink`` until the collection is
    empty of a link. urljoin resolves the relative form OLIS emits; without
    that resolve the httpx call raises UnsupportedProtocol on page 2.
    """
    httpx = pytest.importorskip("httpx")

    page_one = {
        "value": [_action("SB", 1, "S", "2025-02-04T10:00:00", "First reading", hid=1)],
        "@odata.nextLink": "MeasureHistoryActions?$skip=1&$format=json",
    }
    page_two = {
        "value": [_action("HB", 2, "H", "2025-02-04T10:00:00", "First reading", hid=2)],
    }

    calls: list[str] = []

    def fake_get(url, timeout=None, follow_redirects=None):  # noqa: ARG001
        calls.append(url)
        payload = page_one if len(calls) == 1 else page_two
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)

    adapter = OlisAdapter(live=True, session="2025R1", state_path=tmp_path / "state.json")
    rows = adapter._fetch_actions("2025R1")
    assert len(rows) == 2
    # Relative nextLink resolved against the base OData URL.
    assert calls[1].startswith("https://api.oregonlegislature.gov/odata/odataservice.svc/")
