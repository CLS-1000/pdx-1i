"""
Hedging gate.

The tone gate catches language that asserts wrongdoing. This one catches language
that implies it without asserting anything -- the failure mode the other two gates
are structurally blind to.

Two families, and they fail for opposite reasons:

- **Conclusion-stealing.** "Clearly", "obviously", "of course" tell the reader what to
  conclude before they have read the measurement. The engine reports structure and
  timing; whether a pattern means anything is the reader's call, and a word that makes
  that call for them does not ship.

- **Insinuation by hedge.** "Appears to", "raises questions", "stops short of" assert
  nothing, which is exactly the problem: an implication carries the force of a finding
  while presenting no claim to source. Because nothing is asserted, the attribution
  gate has nothing to check and waves it through. That gap is why this gate exists.

The check is a deterministic vocabulary scan, not a language model, and it is
deliberately conservative in the same way the tone gate is: a false rejection costs a
rewrite, a false acceptance costs a published insinuation.

Terms here are disjoint from the tone gate's vocabulary -- a passage that trips both
should report two distinct reasons, not the same word twice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Adverbs and framings that present a conclusion as self-evident. The measurement is
# the argument; a word insisting the reader agree is doing work the numbers should do.
_CONCLUSION_STEALING = frozenset(
    {
        "clearly",
        "obviously",
        "evidently",
        "plainly",
        "undoubtedly",
        "unquestionably",
        "certainly",
        "of course",
        "naturally",
        "tellingly",
        "unsurprisingly",
        "predictably",
        "needless to say",
        "it is no surprise",
        "as expected",
    }
)

# Constructions that imply a finding while asserting nothing. Nothing is claimed, so
# nothing can be sourced -- which is what makes these publishable-looking and wrong.
_INSINUATION = frozenset(
    {
        "appears to",
        "appear to",
        "seems to",
        "seem to",
        "would seem",
        "raises questions",
        "raises the question",
        "raises concerns",
        "casts doubt",
        "calls into question",
        "no coincidence",
        "stops short of",
        "stopped short of",
        "declined to say",
        "refused to say",
        "failed to explain",
        "has yet to explain",
        "remains unclear why",
        "leaves open the question",
        "worth noting",
    }
)

_WORD = re.compile(r"[a-z][a-z\-]*")


@dataclass(frozen=True)
class HedgingResult:
    """Whether a passage clears the hedging gate, and what tripped it."""

    passed: bool
    conclusion_stealing: tuple[str, ...] = ()
    insinuation: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.passed

    def reason(self) -> str:
        if self.passed:
            return "hedging gate: pass"
        parts = []
        if self.conclusion_stealing:
            parts.append(f"conclusion stated for the reader {list(self.conclusion_stealing)}")
        if self.insinuation:
            parts.append(f"implication without a claim {list(self.insinuation)}")
        return "hedging gate: " + "; ".join(parts)


def check_hedging(text: str) -> HedgingResult:
    """Scan a passage for conclusion-stealing adverbs and insinuating constructions."""
    lowered = text.lower()
    words = set(_WORD.findall(lowered))

    def hits(vocabulary: frozenset[str]) -> tuple[str, ...]:
        # Single tokens match the word set so "certainly" does not fire inside
        # "uncertainly"; multi-word phrases match the raw string.
        return tuple(
            sorted(
                term
                for term in vocabulary
                if (term in lowered if " " in term else term in words)
            )
        )

    conclusion = hits(_CONCLUSION_STEALING)
    insinuation = hits(_INSINUATION)

    return HedgingResult(
        passed=not (conclusion or insinuation),
        conclusion_stealing=conclusion,
        insinuation=insinuation,
    )
