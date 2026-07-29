"""
Attribution gate.

Every published claim must trace back to a record the engine actually holds. A section
that cites no record, or cites a record ID the store does not contain, does not publish.

This is what makes "every claim traces to a run_id" enforceable rather than aspirational.
The gate also rejects vague sourcing -- "sources say", "it is understood" -- because an
unnamed source is not a traceable one.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

# Constructions that gesture at sourcing without providing any.
_VAGUE_ATTRIBUTION = (
    "sources say",
    "sources confirm",
    "sources close to",
    "it is understood",
    "it is believed",
    "widely believed",
    "people familiar with",
    "reportedly",
    "allegedly",
    "rumored",
    "some say",
    "critics say",
    "observers say",
)


@dataclass(frozen=True)
class AttributionResult:
    """Whether a passage clears the attribution gate."""

    passed: bool
    cited: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()
    vague: tuple[str, ...] = ()
    missing_citation: bool = False

    def __bool__(self) -> bool:
        return self.passed

    def reason(self) -> str:
        if self.passed:
            return f"attribution gate: pass ({len(self.cited)} record(s) cited)"
        parts = []
        if self.missing_citation:
            parts.append("no source record cited")
        if self.unknown:
            parts.append(f"unknown record ids {list(self.unknown)}")
        if self.vague:
            parts.append(f"vague attribution {list(self.vague)}")
        return "attribution gate: " + "; ".join(parts)


def check_attribution(
    text: str,
    cited_record_ids: Iterable[str],
    known_record_ids: Iterable[str],
) -> AttributionResult:
    """
    Verify a passage's sourcing.

    Passes when the section cites at least one record, every cited ID is one the engine
    holds, and the prose contains no vague-attribution construction.
    """
    cited = tuple(cited_record_ids)
    known = set(known_record_ids)
    lowered = text.lower()

    unknown = tuple(rid for rid in cited if rid not in known)
    vague = tuple(phrase for phrase in _VAGUE_ATTRIBUTION if phrase in lowered)

    # Every section must cite something. A numeric claim with nothing cited is the
    # sharpest form of this failure, but an uncited section fails either way.
    missing = not cited

    return AttributionResult(
        passed=not (missing or unknown or vague),
        cited=cited,
        unknown=unknown,
        vague=vague,
        missing_citation=missing,
    )
