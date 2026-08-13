"""
Hedging check -- observation only.

The tone check matches language that asserts wrongdoing. This one matches language
that implies it without asserting anything -- the pattern the other two checks are
structurally blind to.

**This does not withhold a section.** `check_hedging` always passes; what it matched
is returned as observations and published alongside the section it describes. It moved
to observation-only with the tone check, for the same reason: the scan reads a section
body carrying harvested source text, so it cannot tell a source's hedging from the
engine's own.

The cost is worth naming. Insinuation is the one failure mode neither of the other two
checks can see -- an implication asserts nothing, so tone finds no loaded vocabulary
and attribution finds no claim to trace. Nothing now stops it reaching a reader; the
observation only records that it was there.

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


#: Name this check reports itself under, in logs and in stored observations.
GATE_NAME = "hedging_gate"

_NOTE = "Prosecutorial or subjective vocabulary detected in source text."


@dataclass(frozen=True)
class HedgingResult:
    """
    What the hedging scan matched in a passage.

    `passed` is always True -- the scan observes, it does not withhold. See the module
    docstring for why, and for what that gives up.
    """

    passed: bool = True
    conclusion_stealing: tuple[str, ...] = ()
    insinuation: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.passed

    @property
    def matched_terms(self) -> tuple[str, ...]:
        """Every term matched, across both vocabularies, sorted and deduplicated."""
        return tuple(sorted(set(self.conclusion_stealing) | set(self.insinuation)))

    @property
    def clean(self) -> bool:
        """True when nothing matched. This is the question `passed` used to answer."""
        return not self.matched_terms

    def reason(self) -> str:
        if self.clean:
            return "hedging gate: pass"
        parts = []
        if self.conclusion_stealing:
            parts.append(f"conclusion stated for the reader {list(self.conclusion_stealing)}")
        if self.insinuation:
            parts.append(f"implication without a claim {list(self.insinuation)}")
        return "hedging gate: " + "; ".join(parts)

    def observations(self) -> list[dict[str, object]]:
        """Audit payload for this passage -- empty when nothing matched."""
        if self.clean:
            return []
        return [
            {
                "gate": GATE_NAME,
                "rule": "observation_only",
                "severity": "info",
                "matched_terms": list(self.matched_terms),
                "note": _NOTE,
            }
        ]


def check_hedging(text: str) -> HedgingResult:
    """
    Scan a passage for conclusion-stealing adverbs and insinuating constructions.

    Always passes. Read `observations()` for what it found, or `clean` for whether it
    found anything.
    """
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
        passed=True,
        conclusion_stealing=conclusion,
        insinuation=insinuation,
    )
