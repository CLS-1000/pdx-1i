from __future__ import annotations

from typing import Protocol, Sequence

from pdx1.models import Signal


class SourceAdapter(Protocol):
    name: str

    def fetch(self) -> Sequence[Signal]:
        """Collect and normalize source records into Signal objects."""
