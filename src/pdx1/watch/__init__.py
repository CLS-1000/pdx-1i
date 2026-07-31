from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WatchTarget:
    name: str
    endpoint: str


from .adapter import WatchAdapter
from .targets import WATCH_TARGETS

__all__ = ["WatchAdapter", "WatchTarget", "WATCH_TARGETS"]
