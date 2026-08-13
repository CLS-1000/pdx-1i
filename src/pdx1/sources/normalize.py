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

import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

_PUNCT = re.compile(r"[^a-z0-9]+")

# Keys a government export may nest its rows under when it wraps them in an object.
_ROW_KEYS = ("records", "results", "data", "value", "items", "filings", "contributions")

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


def union_keys(rows: Iterable[Mapping[str, Any]]) -> list[str]:
    """
    Every key appearing anywhere in `rows`, in first-seen order.

    Alias resolution must see the union rather than the first row's keys. Government
    exports omit empty optional columns per row, so building the map from `rows[0]`
    alone drops any field that first row happened to lack -- for every row, including
    the ones that carry it.
    """
    seen: dict[str, None] = {}
    for row in rows:
        for key in row:
            seen.setdefault(key, None)
    return list(seen)


def load_records(raw: str) -> list[dict[str, Any]]:
    """
    Read a payload into a list of row dicts, whichever shape it arrived in.

    Three shapes turn up across these feeds and all three are accepted:

    - a JSON array, which is what the fixtures use
    - a JSON object wrapping the rows under a key (`records`, `value`, ...)
    - JSONL, one JSON object per line, which is how bulk exports are usually shipped

    A blank line or an unparseable JSONL line is skipped rather than fatal, matching
    the pipeline's failure-first stance: one bad row must not cost the whole feed.
    """
    text = raw.strip()
    if not text:
        return []

    if text.startswith("["):
        loaded = json.loads(text)
        return [row for row in loaded if isinstance(row, dict)]

    if text.startswith("{"):
        # Either one object wrapping rows, or the first line of a JSONL stream.
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError:
            return _load_jsonl(text)
        if isinstance(loaded, dict):
            for key in _ROW_KEYS:
                rows = loaded.get(key)
                if isinstance(rows, list):
                    return [row for row in rows if isinstance(row, dict)]
            # A bare object with no recognised wrapper key is a single row.
            return [loaded]
        return [row for row in loaded if isinstance(row, dict)]

    return _load_jsonl(text)


def _load_jsonl(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows
