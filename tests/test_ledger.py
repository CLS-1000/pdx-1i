"""Dead-man check over the run ledger: `pdx1.ledger` and `pdx1 --check-ledger`."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pdx1.config import Settings
from pdx1.ledger import DEFAULT_MAX_AGE_HOURS, check_ledger, read_ledger
from pdx1.pipeline import check_ledger_health, main
from pdx1.store import DualWriteStore

NOW = datetime(2026, 9, 27, 14, 0, tzinfo=timezone.utc)


def _line(hours_ago: float, status: str = "ok", live: bool = True, **extra) -> dict:
    return {
        "run_id": f"run-{hours_ago}",
        "started_at": (NOW - timedelta(hours=hours_ago)).isoformat(),
        "status": status,
        "live": live,
        **extra,
    }


def test_fresh_live_ok_run_passes() -> None:
    health = check_ledger([_line(8)], NOW)
    assert health.ok
    assert health.alerts == []
    assert health.last_run_id == "run-8"
    assert health.age_hours == 8.0


def test_empty_ledger_is_missing() -> None:
    health = check_ledger([], NOW)
    assert not health.ok
    assert health.alerts == ["missing"]


def test_lines_without_a_readable_start_are_not_evidence_of_a_run() -> None:
    health = check_ledger([{"run_id": "x", "started_at": "not a date"}, {}], NOW)
    assert health.alerts == ["missing"]
    assert health.lines == 2


def test_age_at_exactly_the_window_passes_and_one_minute_past_fails() -> None:
    at_edge = check_ledger([_line(DEFAULT_MAX_AGE_HOURS)], NOW)
    past_edge = check_ledger([_line(DEFAULT_MAX_AGE_HOURS + 1 / 60)], NOW)
    assert at_edge.ok
    assert past_edge.alerts == ["stale"]


def test_newest_is_chosen_by_start_time_not_file_order() -> None:
    health = check_ledger([_line(2), _line(50)], NOW)
    assert health.ok
    assert health.last_run_id == "run-2"


def test_failed_cycle_alerts_even_when_fresh() -> None:
    health = check_ledger([_line(3, status="fail")], NOW)
    assert health.alerts == ["failed"]


def test_fixture_replay_alerts_as_not_live() -> None:
    # The 2026-09-24..26 failure: the task ran daily, wrote lines, and never fetched.
    health = check_ledger([_line(3, live=False)], NOW)
    assert health.alerts == ["not_live"]
    assert check_ledger([_line(3, live=False)], NOW, require_live=False).ok


def test_missing_live_field_is_not_trusted_as_live() -> None:
    line = _line(3)
    del line["live"]
    assert check_ledger([line], NOW).alerts == ["not_live"]


def test_future_dated_line_alerts_instead_of_reading_fresh() -> None:
    health = check_ledger([_line(-2)], NOW)
    assert health.alerts == ["future"]
    # Within an hour of the clock is skew, not a bad line.
    assert check_ledger([_line(-0.5)], NOW).ok


def test_partial_run_passes_with_a_note() -> None:
    health = check_ledger([_line(3, status="partial", failed_adapters=["sei"])], NOW)
    assert health.ok
    assert health.notes == ["partial run: failed adapters sei"]


def test_alerts_accumulate() -> None:
    health = check_ledger([_line(30, status="fail", live=False)], NOW)
    assert health.alerts == ["stale", "failed", "not_live"]


def test_naive_clock_is_treated_as_utc() -> None:
    assert check_ledger([_line(3)], NOW.replace(tzinfo=None)).ok


def test_read_ledger_skips_torn_and_non_object_lines(tmp_path: Path) -> None:
    path = tmp_path / "pdx1_signals_runs.jsonl"
    path.write_text('{"run_id": "a"}\n\n{"run_id": \n[1, 2]\n{"run_id": "b"}\n', encoding="utf-8")
    assert [line["run_id"] for line in read_ledger(path)] == ["a", "b"]
    assert list(read_ledger(tmp_path / "absent.jsonl")) == []


def _settings(tmp_path: Path) -> Settings:
    base = Settings()
    return replace(
        base,
        store_path=tmp_path / "pdx1_signals.jsonl",
        db_path=tmp_path / "pdx1.db",
        ledger_max_age_hours=26,
    )


def test_cli_check_reads_the_store_ledger_and_exits_zero_when_healthy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = _settings(tmp_path)
    store = DualWriteStore(settings.store_path, settings.db_path)
    store.write_run(_line(5))
    assert check_ledger_health(settings, now=NOW) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert report["ledger"] == str(store.runs_path)


def test_cli_check_exits_one_and_creates_nothing_when_ledger_absent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = _settings(tmp_path)
    assert check_ledger_health(settings, now=NOW) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["alerts"] == ["missing"]
    assert "ledger check failed -- missing" in captured.err
    assert list(tmp_path.iterdir()) == []


def test_main_routes_check_ledger_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PDX1_STORE_PATH", str(tmp_path / "pdx1_signals.jsonl"))
    monkeypatch.setenv("PDX1_LEDGER_MAX_AGE_HOURS", "26")
    assert main(["--check-ledger"]) == 1
    assert json.loads(capsys.readouterr().out)["alerts"] == ["missing"]
