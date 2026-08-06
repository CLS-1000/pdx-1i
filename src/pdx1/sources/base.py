"""
Source adapter contract.

Each adapter turns one feed's raw payload into Signal objects. Adapters are
independently fault-tolerant: `safe_fetch` catches anything a single adapter raises and
returns it as an error on the result rather than propagating. A dead feed must never
halt a cycle -- the other adapters still run and the write still happens.

Two adapter shapes exist:

- `SourceAdapter`      — fixture-backed; reads from a local file. Default for CI and
                         replay mode. Subclass this when you only need fixture support.
- `LiveSourceAdapter`  — extends SourceAdapter; falls back to live HTTP (via httpx) when
                         no fixture_path is configured. Only `_read_raw` changes; parse
                         logic is identical. Requires the `live` extra to fetch over the
                         network.

Gating: the pipeline passes `fixture_path=` in fixture mode and omits it in live mode.
Individual adapters declare their `feed_url` as a class attribute; `_read_raw` uses it
when no fixture is present.

Live reads resolve in three tiers, in order:

    1. fixture_path      an explicit local payload; wins over everything
    2. live HTTP         the registered feed_url; writes a last-good cache on success
    3. last-good cache   the previous successful body, when the live fetch fails

The third tier is why a cycle survives a feed outage with real data rather than no
data. It is deliberately not a substitute for the velocity gate: a cached payload
carries its original timestamps, so stale records are dropped downstream exactly as
they would be if the feed had served them. The cache makes an outage non-fatal; it
does not make old records publishable.
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from ..models import Signal, SourceType

logger = logging.getLogger(__name__)

#: Where last-good payloads are written when no cache_dir is passed. Overridden by
#: PDX1_CACHE_DIR via Settings, which the pipeline forwards to each adapter.
DEFAULT_CACHE_DIR = Path("cache/pdx1")

_UNSAFE = re.compile(r"[^a-z0-9]+")


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


class LiveSourceAdapter(SourceAdapter):
    """
    SourceAdapter that can fetch its payload over HTTP when live mode is enabled.

    Subclasses declare ``feed_url`` as a class attribute. The pipeline passes
    ``fixture_path=`` in fixture/CI mode and ``live=True`` (without a fixture_path) in
    production. When ``live=False`` (default) and no ``fixture_path`` is given, the
    adapter raises the same error as the base class -- live fetch must be explicitly
    opted in to.

    Requires the ``live`` extra for network fetches::

        pip install "pdx-1i[live]"
    """

    #: Live endpoint for this feed. Set as a class attribute on each subclass.
    feed_url: str = ""

    def __init__(
        self,
        fixture_path=None,
        timeout: int = 30,
        live: bool = False,
        cache_dir: Path | str | None = None,
    ) -> None:
        super().__init__(fixture_path=fixture_path, timeout=timeout)
        self._live = live
        self._cache_dir = Path(cache_dir) if cache_dir is not None else None

    # ── Last-good cache ──────────────────────────────────────────────────────

    def cache_path(self) -> Path | None:
        """
        Where this adapter's last-good payload lives, or None when caching is off.

        Caching is opt-in: constructing an adapter directly never touches the disk.
        The pipeline turns it on by passing `settings.cache_dir`, which defaults to
        DEFAULT_CACHE_DIR and is overridden by PDX1_CACHE_DIR.
        """
        if self._cache_dir is None:
            return None
        slug = _UNSAFE.sub("_", self.name.lower()).strip("_") or "adapter"
        return self._cache_dir / f"{slug}.raw"

    def _write_cache(self, text: str) -> None:
        """
        Record a successful fetch. Never raises -- a cache we cannot write is a
        degraded next outage, not a failed cycle.
        """
        path = self.cache_path()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        except OSError as exc:
            logger.warning("%s: cache write failed (%s): %s", self.name, path, exc)

    def _read_cache(self) -> str | None:
        """Return the last-good payload, or None if there is not one to read."""
        path = self.cache_path()
        if path is None:
            return None
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("%s: cache read failed (%s): %s", self.name, path, exc)
        return None

    # ── Live fetch ───────────────────────────────────────────────────────────

    def _decode(self, response) -> str:
        """
        Turn the HTTP response into the text `parse` expects.

        The default is `response.text`, so httpx's charset handling applies. Adapters
        whose feed ships a container format -- ORESTAR serves a ZIP of CSV -- override
        this to unwrap `response.content`, so the cache and `parse` both see the same
        unwrapped text.
        """
        return response.text

    def _fetch_live(self) -> str:
        """One HTTP GET against `feed_url`. Raises on any transport or status error."""
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                f"{self.name}: httpx is required for live fetch -- "
                "install it with: pip install 'pdx-1i[live]'"
            ) from exc
        response = httpx.get(self.feed_url, timeout=self.timeout, follow_redirects=True)
        response.raise_for_status()
        return self._decode(response)

    def _read_raw(self) -> str:
        if self.fixture_path is not None:
            return self.fixture_path.read_text(encoding="utf-8")
        if self._live and self.feed_url:
            try:
                text = self._fetch_live()
            except Exception as exc:  # noqa: BLE001 - any failure may fall back to cache
                cached = self._read_cache()
                if cached is None:
                    raise
                logger.warning(
                    "%s: live fetch failed (%s) -- serving last-good cache from %s",
                    self.name,
                    exc,
                    self.cache_path(),
                )
                return cached
            self._write_cache(text)
            return text
        raise RuntimeError(
            f"{self.name}: no fixture_path configured and live fetch is not enabled"
        )
