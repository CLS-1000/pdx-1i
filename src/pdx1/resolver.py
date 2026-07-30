"""
Entity resolution (publication path step 02).

Deterministic name -> node ID matching in three passes: exact, token-sort, substring.
No fuzzy-match library and no NLP. The reason is auditability, not simplicity: a
resolution that put the wrong entity on a published record has to be explainable by
reading the code, and every match here reports which pass produced it.

A name that no pass resolves returns None. The pipeline records that as an unresolved
mention rather than guessing -- a wrong entity link is worse than an absent one.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from .models import Node

_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")

# Dropped before token comparison. These carry no distinguishing information in
# Portland-metro body names -- "Multnomah County" and "County of Multnomah" are the
# same body, and "Portland General Electric Co." is "Portland General Electric".
_NOISE_TOKENS = frozenset(
    {
        "the",
        "of",
        "and",
        "co",
        "corp",
        "inc",
        "llc",
        "lp",
        "company",
        "bureau",
        "dept",
        "department",
    }
)


def normalize(name: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    lowered = _PUNCT.sub(" ", name.lower())
    return _WS.sub(" ", lowered).strip()


def token_key(name: str) -> str:
    """Normalized tokens, noise removed, sorted -- order-insensitive identity."""
    tokens = [t for t in normalize(name).split() if t not in _NOISE_TOKENS]
    return " ".join(sorted(tokens))


@dataclass(frozen=True)
class Resolution:
    """A resolved name, with the pass that resolved it."""

    node_id: str
    matched_on: str
    method: str


class EntityResolver:
    """
    Resolves free-text names to node IDs against a fixed registry.

    Aliases are explicit. If a source calls Portland General Electric "PGE", that alias
    is registered here rather than inferred at match time.
    """

    def __init__(
        self,
        nodes: Iterable[Node],
        aliases: dict[str, str] | None = None,
    ) -> None:
        self._nodes: dict[str, Node] = {}
        self._exact: dict[str, str] = {}
        self._tokens: dict[str, str] = {}

        for node in nodes:
            self._nodes[node.id] = node
            self._index(node.label, node.id)
            self._index(node.id, node.id)

        for alias, node_id in (aliases or {}).items():
            if node_id not in self._nodes:
                raise ValueError(f"alias {alias!r} points at unknown node {node_id!r}")
            self._index(alias, node_id)

    def _index(self, name: str, node_id: str) -> None:
        norm = normalize(name)
        if norm:
            self._exact.setdefault(norm, node_id)
            self._tokens.setdefault(token_key(name), node_id)

    def resolve(self, name: str) -> Resolution | None:
        """
        Resolve one name. Returns None if no pass matches.

        Pass 1 exact      normalized string equality
        Pass 2 token-sort same tokens in any order, noise words dropped
        Pass 3 substring  a registered name contains the query, or vice versa
        """
        norm = normalize(name)
        if not norm:
            return None

        hit = self._exact.get(norm)
        if hit:
            return Resolution(node_id=hit, matched_on=name, method="exact")

        hit = self._tokens.get(token_key(name))
        if hit:
            return Resolution(node_id=hit, matched_on=name, method="token-sort")

        # Substring is the loosest pass, so it must be unambiguous: exactly one
        # candidate. Two plausible matches means the name is genuinely ambiguous and
        # the caller gets None rather than a coin flip.
        candidates = {
            node_id
            for registered, node_id in self._exact.items()
            if len(registered) >= 4 and (registered in norm or norm in registered)
        }
        if len(candidates) == 1:
            return Resolution(
                node_id=candidates.pop(), matched_on=name, method="substring"
            )

        return None

    def extract(self, text: str) -> list[Resolution]:
        """
        Find registered entities mentioned in a passage.

        Scans for known names as whole-word substrings of the normalized text. Longer
        names are tried first so "Portland General Electric" wins over "Portland".
        Deterministic and registry-bound: an entity the registry does not know is not
        extracted, which is the intended failure mode.
        """
        norm = normalize(text)
        found: dict[str, Resolution] = {}

        for registered in sorted(self._exact, key=len, reverse=True):
            if len(registered) < 4:
                continue
            if re.search(rf"(?<!\w){re.escape(registered)}(?!\w)", norm):
                node_id = self._exact[registered]
                found.setdefault(
                    node_id,
                    Resolution(node_id=node_id, matched_on=registered, method="extract"),
                )

        return list(found.values())

    def resolve_all(self, names: Iterable[str]) -> list[Resolution]:
        """Resolve many names, dropping the ones that do not match."""
        out: list[Resolution] = []
        for name in names:
            hit = self.resolve(name)
            if hit is not None:
                out.append(hit)
        return out

    def node(self, node_id: str) -> Node | None:
        return self._nodes.get(node_id)

    def __len__(self) -> int:
        return len(self._nodes)
