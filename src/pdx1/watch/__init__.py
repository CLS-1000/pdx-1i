from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WatchTarget:
    name: str
    endpoint: str
