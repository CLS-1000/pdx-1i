"""Shared fixtures."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pdx1.models import ParsedSignal, Signal, SourceType

FIXTURE_DIR = Path(__file__).parent / "fixtures"

#: Anchor for fixture replays. The checked-in source payloads are dated around this
#: instant, so tests that exercise the velocity gate pass it as `now`.
EPOCH = datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def fixture_dir() -> Path:
    return FIXTURE_DIR


@pytest.fixture
def epoch() -> datetime:
    return EPOCH


def make_signal(
    *,
    text: str | None = None,
    words: int = 60,
    credibility: float = 0.8,
    age_hours: float = 2.0,
    source: str = "ORESTAR",
    source_type: SourceType = SourceType.ORESTAR,
    now: datetime = EPOCH,
) -> Signal:
    """Build a signal with precise control over what each gate will see."""
    body = text if text is not None else " ".join(f"token{i}" for i in range(words))
    return Signal(
        source=source,
        source_type=source_type,
        text=body,
        published_at=now - timedelta(hours=age_hours),
        credibility=credibility,
    )


def make_parsed(**kwargs) -> ParsedSignal:
    signal = make_signal(**kwargs)
    return ParsedSignal(signal=signal, clean_text=signal.text, keywords=[])
