"""
Tone gate.

Rejects prosecutorial framing and motive attribution before a section can be published.
The governing constraints, from the SPEC-1 publication rules:

    Descriptive, not prosecutorial.
    No motive attribution.
    Mirror principle -- the text must be usable by its subject.

The engine reports structure and timing. Whether a pattern means anything is the
reader's call, and language that makes that call for them does not ship.

The check is a deterministic vocabulary scan, not a language model. It is deliberately
conservative: it will flag some innocuous phrasing, and a false rejection costs a
rewrite while a false acceptance costs a published accusation.
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


@dataclass(frozen=True)
class ToneResult:
    """Whether a passage clears the tone gate, and what tripped it."""

    passed: bool
    prosecutorial: tuple[str, ...] = ()
    motive: tuple[str, ...] = ()
    loaded: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.passed

    def reason(self) -> str:
        if self.passed:
            return "tone gate: pass"
        parts = []
        if self.prosecutorial:
            parts.append(f"prosecutorial language {list(self.prosecutorial)}")
        if self.motive:
            parts.append(f"motive attribution {list(self.motive)}")
        if self.loaded:
            parts.append(f"loaded framing {list(self.loaded)}")
        return "tone gate: " + "; ".join(parts)


def check_tone(text: str) -> ToneResult:
    """Scan a passage for prosecutorial, motive-attributing or loaded language."""
    lowered = text.lower()
    words = set(_WORD.findall(lowered))

    prosecutorial = tuple(sorted(words & _PROSECUTORIAL))
    loaded = tuple(sorted(words & _LOADED))

    # Motive terms include multi-word phrases, so match against the raw string.
    motive = tuple(sorted(phrase for phrase in _MOTIVE if phrase in lowered))

    return ToneResult(
        passed=not (prosecutorial or motive or loaded),
        prosecutorial=prosecutorial,
        motive=motive,
        loaded=loaded,
    )
