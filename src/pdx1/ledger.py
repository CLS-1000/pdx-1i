"""
Dead-man check over the run ledger.

Every cycle appends one line to `<store>_runs.jsonl`, including a quiet day and a cycle
that raised (see `pipeline.run_cycle`). That makes the *absence* of a line the one
failure the ledger cannot report about itself: a scheduled task that never started, a
host that was down, a job that cloned the wrong branch and never reached the store.

`check_ledger` reads the newest line and says whether the unattended run can be
trusted today. It has to be run by something other than the cycle it watches -- a
second scheduled job, or a monitor polling `pdx1 --check-ledger` -- because a cycle
that did not run cannot raise its own alarm.

Pure over the ledger lines and the clock, so every rule is testable without a store.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

#: A daily 05:56 cycle plus slack for a slow run and a DST shift. Anything later than
#: this means a morning was missed, not that the run was late.
DEFAULT_MAX_AGE_HOURS = 26


@dataclass
class LedgerHealth:
    """What the newest ledger line says about the unattended run."""

    ok: bool
    checked_at: str
    max_age_hours: int
    #: Machine-readable reasons the check failed; empty when `ok`.
    alerts: list[str] = field(default_factory=list)
    #: Conditions worth reading that do not fail the check (a partial day).
    notes: list[str] = field(default_factory=list)
    last_run_id: str | None = None
    last_started_at: str | None = None
    last_status: str | None = None
    age_hours: float | None = None
    lines: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def read_ledger(path: Path) -> Iterator[dict]:
    """Every run-ledger line at `path`, oldest first. Blank or torn lines are skipped."""
    if not path.exists():
        return
    with path.open(encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                line = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("runs ledger: skipped unreadable line")
                continue
            if isinstance(line, dict):
                yield line


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        ts = datetime.fromisoformat(value)
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def check_ledger(
    lines: Iterable[dict],
    now: datetime,
    max_age_hours: int = DEFAULT_MAX_AGE_HOURS,
    require_live: bool = True,
) -> LedgerHealth:
    """
    Judge the newest ledger line against the clock.

    Alerts, any of which fails the check:

    - ``missing`` -- no readable line at all, so there is no evidence any run happened.
    - ``stale`` -- the newest line started more than `max_age_hours` ago.
    - ``failed`` -- the newest cycle raised, or no adapter answered.
    - ``not_live`` -- the newest cycle replayed fixtures. A fixture run writes a normal
      ledger line and a normal-looking brief about nothing current, which is exactly
      how 2026-09-24 to 09-26 went unnoticed.
    - ``future`` -- the newest line is dated ahead of the clock by more than an hour.
      Taken at face value it would read as fresh indefinitely and mask every missed
      morning after it -- the same failure as the future-dated WA PDC record that once
      anchored the velocity gate.

    "Newest" is by `started_at`, not file order, so a ledger rebuilt or concatenated out
    of order is still judged by its latest run. Lines whose `started_at` cannot be read
    are counted but never treated as evidence of a run.
    """
    now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    health = LedgerHealth(
        ok=False, checked_at=now.isoformat(), max_age_hours=max_age_hours
    )

    newest: dict | None = None
    newest_ts: datetime | None = None
    for line in lines:
        health.lines += 1
        ts = _parse_ts(line.get("started_at"))
        if ts is None:
            continue
        if newest_ts is None or ts > newest_ts:
            newest, newest_ts = line, ts

    if newest is None or newest_ts is None:
        health.alerts.append("missing")
        return health

    health.last_run_id = newest.get("run_id")
    health.last_started_at = newest_ts.isoformat()
    health.last_status = newest.get("status")
    age = now - newest_ts
    health.age_hours = round(age.total_seconds() / 3600, 2)

    if age < -timedelta(hours=1):
        health.alerts.append("future")
    if age > timedelta(hours=max_age_hours):
        health.alerts.append("stale")
    if newest.get("status") == "fail":
        health.alerts.append("failed")
    if require_live and newest.get("live") is not True:
        health.alerts.append("not_live")
    if newest.get("status") == "partial":
        failed = ", ".join(newest.get("failed_adapters") or []) or "unknown"
        health.notes.append(f"partial run: failed adapters {failed}")

    health.ok = not health.alerts
    return health
