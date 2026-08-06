"""
Tone check -- observation only.

Scans for prosecutorial framing and motive attribution. The governing constraints,
from the SPEC-1 publication rules:

    Descriptive, not prosecutorial.
    No motive attribution.
    Mirror principle -- the text must be usable by its subject.

**This does not withhold a section.** `check_tone` always passes; what it matched is
returned as observations and published alongside the section it describes.

The change was made deliberately, after a live run showed why. The scan reads the
assembled section body, and a record's `pattern` carries harvested source text into
that body -- so a newspaper reporting that someone pleaded guilty to fraud tripped the
same vocabulary as the engine alleging fraud. The scan cannot tell those apart, and
dropping the section punished the second case by silencing the first. Recording the
match and publishing anyway keeps the signal without the false positive.

What this costs is real and worth stating: the engine no longer refuses to publish
prosecutorial vocabulary, it only annotates it. Editorial judgement now sits with
whoever reads the observations. The attribution gate is untouched and still rejects --
traceability is not a matter of tone.

The scan is a deterministic vocabulary lookup, not a language model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Words that assert wrongdoing rather than describe a record.
_PROSECUTORIAL = frozenset(
    {
        "corrupt",
        "corruption",
        "bribe",
        "bribery",
        "kickback",
        "fraud",
        "fraudulent",
        "criminal",
        "illegal",
        "unlawful",
        "crooked",
        "scandal",
        "scheme",
        "conspiracy",
        "collusion",
        "cover-up",
        "coverup",
        "payoff",
        "graft",
        "racket",
        "guilty",
        "culpable",
        "malfeasance",
        "wrongdoing",
    }
)

# Words that assign intent or state of mind. The engine observes records; it cannot
# observe why anyone did anything.
_MOTIVE = frozenset(
    {
        "deliberately",
        "intentionally",
        "knowingly",
        "purposely",
        "sought",
        "wanted",
        "intended",
        "motivated",
        "in order to",
        "so that they could",
        "designed to",
        "meant to",
        "in exchange for",
        "to benefit",
        "to reward",
        "to conceal",
        "to hide",
        "to avoid scrutiny",
    }
)

# Editorialising intensifiers. Numbers carry the weight; adjectives do not.
_LOADED = frozenset(
    {
        "shocking",
        "brazen",
        "blatant",
        "egregious",
        "outrageous",
        "troubling",
        "alarming",
        "suspicious",
        "questionable",
        "cozy",
        "sweetheart",
        "shadowy",
        "secretive",
        "notorious",
    }
)

_WORD = re.compile(r"[a-z][a-z\-]*")


#: Name this check reports itself under, in logs and in stored observations.
GATE_NAME = "tone_gate"

_NOTE = "Prosecutorial or subjective vocabulary detected in source text."


@dataclass(frozen=True)
class ToneResult:
    """
    What the tone scan matched in a passage.

    `passed` is always True -- the scan observes, it does not withhold. It is kept on
    the result so existing callers that test the result for truthiness keep working
    and simply stop dropping anything.
    """

    passed: bool = True
    prosecutorial: tuple[str, ...] = ()
    motive: tuple[str, ...] = ()
    loaded: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.passed

    @property
    def matched_terms(self) -> tuple[str, ...]:
        """Every term matched, across all three vocabularies, sorted and deduplicated."""
        return tuple(sorted(set(self.prosecutorial) | set(self.motive) | set(self.loaded)))

    @property
    def clean(self) -> bool:
        """True when nothing matched. This is the question `passed` used to answer."""
        return not self.matched_terms

    def reason(self) -> str:
        if self.clean:
            return "tone gate: pass"
        parts = []
        if self.prosecutorial:
            parts.append(f"prosecutorial language {list(self.prosecutorial)}")
        if self.motive:
            parts.append(f"motive attribution {list(self.motive)}")
        if self.loaded:
            parts.append(f"loaded framing {list(self.loaded)}")
        return "tone gate: " + "; ".join(parts)

    def observations(self) -> list[dict[str, object]]:
        """
        Audit payload for this passage -- empty when nothing matched.

        A list rather than a single item so a caller can concatenate the tone and
        hedging payloads without special-casing either.
        """
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


def check_tone(text: str) -> ToneResult:
    """
    Scan a passage for prosecutorial, motive-attributing or loaded language.

    Always passes. Read `observations()` for what it found, or `clean` for whether it
    found anything.
    """
    lowered = text.lower()
    words = set(_WORD.findall(lowered))

    prosecutorial = tuple(sorted(words & _PROSECUTORIAL))
    loaded = tuple(sorted(words & _LOADED))

    # Motive terms include multi-word phrases, so match against the raw string.
    motive = tuple(sorted(phrase for phrase in _MOTIVE if phrase in lowered))

    return ToneResult(
        passed=True,
        prosecutorial=prosecutorial,
        motive=motive,
        loaded=loaded,
    )
