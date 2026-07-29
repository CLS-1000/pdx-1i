"""
Source adapter contract.

Each adapter turns one feed's raw payload into Signal objects. Adapters are
independently fault-tolerant: `safe_fetch` catches anything a single adapter raises and
returns it as an error on the result rather than propagating. A dead feed must never
halt a cycle -- the other adapters still run and the write still happens.

This pass reads from checked-in fixture payloads. Live HTTP is a thin layer over the
same `parse` methods: swap where `_read_raw` gets its bytes and nothing else changes.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from ..models import Signal, SourceType

logger = logging.getLogger(__name__)


@dataclass
class FetchResult:
    """Outcome of one adapter run."""

    source: str
    signals: list[Signal] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def __len__(self) -> int:
        return len(self.signals)


class SourceAdapter(ABC):
    """Base class for every PDX-1i feed adapter."""

    #: Human-readable feed name, carried onto every Signal.
    name: str = "UNKNOWN"

    #: Which feed this is.
    source_type: SourceType

    #: Default source credibility, fed to the credibility gate. Filed public records
    #: rate higher than press because the record is the primary artifact.
    credibility: float = 0.5

    def __init__(self, fixture_path: Path | str | None = None, timeout: int = 30) -> None:
        self.fixture_path = Path(fixture_path) if fixture_path else None
        self.timeout = timeout

    @abstractmethod
    def parse(self, raw: str) -> list[Signal]:
        """Turn a raw payload into signals. Pure -- no I/O, so it is directly testable."""

    def _read_raw(self) -> str:
        """Load the payload. Fixture-backed in this pass."""
        if self.fixture_path is None:
            raise RuntimeError(
                f"{self.name}: no fixture_path configured and live fetch is not enabled"
            )
        return self.fixture_path.read_text(encoding="utf-8")

    def fetch(self) -> FetchResult:
        """Read and parse. Raises on failure -- callers usually want `safe_fetch`."""
        return FetchResult(source=self.name, signals=self.parse(self._read_raw()))

    def safe_fetch(self) -> FetchResult:
        """
        Fetch, converting any failure into an error on the result.

        This is the method the pipeline calls. One broken feed degrades that feed only.
        """
        try:
            return self.fetch()
        except Exception as exc:  # noqa: BLE001 - deliberate: no adapter may halt a cycle
            logger.warning("adapter %s failed: %s", self.name, exc)
            return FetchResult(source=self.name, errors=[f"{type(exc).__name__}: {exc}"])
