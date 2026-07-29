"""
The four-gate deterministic filter (pipeline stage 03).

Every signal clears all four gates or it does not survive. There is no partial credit
and no weighted override -- a signal that fails one gate is dropped regardless of how
strongly it clears the other three.

    Gate          Criterion                                Default
    ------------  ---------------------------------------  -------------
    Credibility   source / analyst weight                  >= 0.5
    Volume        word count                               >= 50 words
    Velocity      recency                                  <= 48 hours
    Novelty       not a duplicate (content-hash dedup)     --

Thresholds are inclusive: a signal at exactly 0.5 credibility, exactly 50 words, or
exactly 48 hours old passes. Boundary behaviour is deliberate and covered by tests.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

from .config import GateConfig
from .models import GateResult, ParsedSignal, utcnow


class FourGateFilter:
    """
    Applies the four gates and tracks seen content for the novelty check.

    The filter is stateful by design: novelty is only meaningful relative to what the
    engine has already recorded. Seed it from the store at the start of a cycle so a
    signal republished across cycles is still recognised as a duplicate.
    """

    def __init__(
        self,
        config: GateConfig | None = None,
        seen_hashes: Iterable[str] | None = None,
    ) -> None:
        self.config = config or GateConfig()
        self._seen: set[str] = set(seen_hashes or ())

    def evaluate(self, parsed: ParsedSignal, now: datetime | None = None) -> GateResult:
        """
        Run all four gates against a parsed signal.

        Every gate is evaluated even once one has failed -- the analyst surface shows
        which gates a dropped signal cleared, so short-circuiting would lose information.
        """
        now = now or utcnow()
        signal = parsed.signal
        cfg = self.config
        detail: dict[str, str] = {}

        credibility = signal.credibility >= cfg.min_credibility
        detail["credibility"] = f"{signal.credibility:.2f} vs >= {cfg.min_credibility:.2f}"

        words = parsed.word_count
        volume = words >= cfg.min_words
        detail["volume"] = f"{words} words vs >= {cfg.min_words}"

        age = now - signal.published_at
        velocity = age <= timedelta(hours=cfg.max_age_hours) and age >= timedelta(0)
        detail["velocity"] = (
            f"{age.total_seconds() / 3600:.1f}h old vs <= {cfg.max_age_hours}h"
        )

        novelty = signal.dedup_hash not in self._seen
        detail["novelty"] = "first occurrence" if novelty else "duplicate content hash"

        return GateResult(
            credibility=credibility,
            volume=volume,
            velocity=velocity,
            novelty=novelty,
            detail=detail,
        )

    def register(self, parsed: ParsedSignal) -> None:
        """Record a signal's content hash so later duplicates fail the novelty gate."""
        self._seen.add(parsed.signal.dedup_hash)

    @property
    def seen_count(self) -> int:
        return len(self._seen)


def composite_score(
    parsed: ParsedSignal,
    gates: GateResult,
    config: GateConfig | None = None,
    now: datetime | None = None,
) -> float:
    """
    Composite priority score in [0, 1] for a signal that cleared the gates.

    This orders surviving signals for analyst attention; it does not decide survival --
    the gates already did that. A signal that failed any gate scores 0.

    The derivation is intentionally simple and inspectable: source credibility, how
    fresh the signal is within the velocity window, and how far past the volume floor
    it runs. Recency and volume are capped so a very long or very fresh item cannot
    compensate for a weak source.
    """
    if not gates.passed:
        return 0.0

    cfg = config or GateConfig()
    now = now or utcnow()
    signal = parsed.signal

    credibility = signal.credibility

    age_hours = max((now - signal.published_at).total_seconds() / 3600, 0.0)
    recency = 1.0 - min(age_hours / cfg.max_age_hours, 1.0)

    volume = min(parsed.word_count / (cfg.min_words * 4), 1.0)

    score = (0.5 * credibility) + (0.3 * recency) + (0.2 * volume)
    return round(min(max(score, 0.0), 1.0), 4)
