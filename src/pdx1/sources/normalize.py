"""
Field normalisation shared by the live adapters.

Fixtures use one set of field names; the real feeds use another. Rather than let each
adapter carry its own ad-hoc coercions, the conversions every feed needs -- money,
dates, and header-alias resolution -- live here and are tested directly.

The governing rule for dates: an unreadable timestamp returns None, never "now".
The velocity gate measures recency, so dating an undated record to the current moment
would make it look fresh and publish something the gate exists to drop.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

_PUNCT = re.compile(r"[^a-z0-9]+")

# Date spellings seen across the government exports this engine reads, tried in order.
# ISO first because the fixtures use it and a real export may too.
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%m/%d/%y",
    "%Y/%m/%d",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
)


def header_key(header: str) -> str:
    """Normalise a column header for alias matching: lowercase, punctuation collapsed."""
    return _PUNCT.sub(" ", header.strip().lower()).strip()


def build_column_map(
    fieldnames: Iterable[str],
    aliases: Mapping[str, tuple[str, ...]],
) -> dict[str, str]:
    """
    Resolve canonical field name -> actual header, for one payload.

    Built once per payload rather than per row: a bulk export runs to hundreds of
    thousands of rows and the header does not change between them. Canonical names
    with no matching header are simply absent from the result.
    """
    seen = {header_key(name): name for name in fieldnames if name}
    resolved: dict[str, str] = {}
    for canonical, candidates in aliases.items():
        for alias in candidates:
            if alias in seen:
                resolved[canonical] = seen[alias]
                break
    return resolved


def parse_money(raw: Any) -> float:
    """Read a currency cell. `$12,500.00`, `12500` and `(500)` all parse."""
    if raw is None or raw == "":
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    cleaned = str(raw).replace("$", "").replace(",", "").strip()
    if cleaned.startswith("(") and cleaned.endswith(")"):  # accounting negative
        cleaned = "-" + cleaned[1:-1]
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def parse_timestamp(raw: Any) -> datetime | None:
    """
    Read a date or timestamp into an aware datetime, or None if unreadable.

    Naive values are read as UTC. Returning None rather than a default is deliberate --
    see the module docstring.
    """
    if raw in (None, ""):
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)

    text = str(raw).strip()
    if not text:
        return None

    # OData serialises timestamps with a trailing Z that fromisoformat rejects
    # before Python 3.11; normalise it either way.
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text

    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        for fmt in _DATE_FORMATS:
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue

    if parsed is None:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def first_present(record: Mapping[str, Any], *keys: str) -> Any:
    """Return the first key present and non-empty in `record`, else None."""
    for key in keys:
        value = record.get(key)
        if value not in (None, "", [], {}):
            return value
    return None
