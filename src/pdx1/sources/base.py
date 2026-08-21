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
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from ..models import Signal, SourceType

logger = logging.getLogger(__name__)

#: Where last-good payloads are written when no cache_dir is passed. Overridden by
#: PDX1_CACHE_DIR via Settings, which the pipeline forwards to each adapter.
DEFAULT_CACHE_DIR = Path("cache/pdx1")

#: Total live attempts per adapter per cycle, retries included. Three is two retries.
#: Kept small deliberately: five adapters times a 30s timeout times a generous retry
#: count is a cycle that runs past the hour it was scheduled in.
DEFAULT_MAX_ATTEMPTS = 3

#: Seconds before the first retry. Doubles each attempt, so 2.0 gives 2s then 4s.
DEFAULT_RETRY_BACKOFF_S = 2.0

#: Status codes worth a second attempt. 429 is the server asking for one; 5xx is the
#: server having a bad moment. Every other 4xx is a settled answer about the request.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504, 507, 508, 509})


def _is_retryable(exc: Exception) -> bool:
    """
    Whether a second attempt could plausibly succeed.

    Transport-level failures -- connection refused, DNS, read timeout -- retry, because
    they are usually about the moment rather than the request. Status errors retry only
    on `_RETRYABLE_STATUS`. Anything that is not an httpx error at all (a parse failure
    reaching this far, a missing dependency) does not retry: repeating a deterministic
    failure just delays the report of it.
    """
    try:
        import httpx
    except ImportError:  # pragma: no cover - live extra not installed
        return False
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS
    return isinstance(exc, httpx.TransportError)

_UNSAFE = re.compile(r"[^a-z0-9]+")


@dataclass
class FetchResult:
    """
    Outcome of one adapter run.

    The measurements below exist so a run can be diagnosed after the fact from the
    record alone. `ok` says the adapter did not raise; it does not say the adapter
    returned anything, which is why `empty` is separate -- a feed that answers 200 with
    an empty list is indistinguishable from a broken one until you look at the status
    and the elapsed time next to it.
    """

    source: str
    signals: list[Signal] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    #: Wall-clock seconds spent in `fetch`, including retries and parse.
    elapsed_s: float = 0.0
    #: Status of the last HTTP response, when one was made. None in fixture mode, and
    #: None when every attempt failed at the transport layer before a response existed.
    http_status: int | None = None
    #: How many times the live fetch was attempted. 0 in fixture mode. Greater than 1
    #: means a retry happened and is worth seeing in a log.
    attempts: int = 0
    #: True when the live fetch failed and the last-good cache answered instead. The
    #: signals are real but not fresh; the velocity gate still judges them on their own
    #: timestamps.
    from_cache: bool = False

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def empty(self) -> bool:
        """
        Answered without raising, and returned nothing.

        Not an error -- a feed with no filings today is a real answer. It is reported
        separately because zero items and a dead adapter look identical downstream, and
        only one of them means the brief is missing something.
        """
        return self.ok and not self.signals

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

    def _annotate(self, result: FetchResult) -> FetchResult:
        """
        Copy per-fetch measurements off the adapter and onto the result.

        The live subclass records what its attempts cost on `self`; this lifts them
        onto the result so the pipeline sees one object per adapter and nothing has to
        reach back into adapter state after the fact.
        """
        return result

    def safe_fetch(self) -> FetchResult:
        """
        Fetch, converting any failure into an error on the result.

        This is the method the pipeline calls. One broken feed degrades that feed only.
        The timing is recorded on both paths -- an adapter that fails after 30 seconds
        of timeout and one that fails instantly on a 404 are different problems, and
        the elapsed time is what tells them apart in the morning.
        """
        started = time.monotonic()
        try:
            result = self.fetch()
        except Exception as exc:  # noqa: BLE001 - deliberate: no adapter may halt a cycle
            logger.warning("adapter %s failed: %s", self.name, exc)
            result = FetchResult(source=self.name, errors=[f"{type(exc).__name__}: {exc}"])
        result.elapsed_s = round(time.monotonic() - started, 3)
        return self._annotate(result)


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
        feed_url: str | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        retry_backoff_s: float = DEFAULT_RETRY_BACKOFF_S,
    ) -> None:
        super().__init__(fixture_path=fixture_path, timeout=timeout)
        self._live = live
        self._cache_dir = Path(cache_dir) if cache_dir is not None else None
        self._max_attempts = max(1, int(max_attempts))
        self._retry_backoff_s = max(0.0, float(retry_backoff_s))
        # Per-fetch measurements, lifted onto the FetchResult by `_annotate`.
        self._http_status: int | None = None
        self._attempts = 0
        self._from_cache = False
        # A publisher moving an endpoint should not need a code change. The class
        # attribute is the default; config supplies an override from PDX1_*_URL.
        if feed_url:
            self.feed_url = feed_url

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

    def _get(self, url: str, **kwargs):
        """
        One instrumented HTTP GET.

        Every live read goes through here -- the single-GET base implementation and the
        paging overrides alike -- so `http_status` is recorded no matter which shape the
        feed has. An adapter that fetched its own way would report `http=-` on a run
        that plainly did reach the server, which is the sort of gap that costs an hour
        at 06:30.
        """
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                f"{self.name}: httpx is required for live fetch -- "
                "install it with: pip install 'pdx-1i[live]'"
            ) from exc
        response = httpx.get(url, timeout=self.timeout, follow_redirects=True, **kwargs)
        self._http_status = getattr(response, "status_code", None)
        return response

    def _fetch_live(self) -> str:
        """One HTTP GET against `feed_url`. Raises on any transport or status error."""
        response = self._get(self.feed_url)
        response.raise_for_status()
        return self._decode(response)

    def _fetch_live_with_retry(self) -> str:
        """
        `_fetch_live`, retried a bounded number of times.

        Bounded by construction: the attempt count comes from `range`, so there is no
        condition under which this loops forever. A cron job that hangs on a retry is
        worse than one that fails -- the failure is visible at 06:05 and the hang is
        not visible until someone looks.

        Only failures that a second attempt could plausibly fix are retried: transport
        errors, 429, and 5xx. A 404 is the endpoint telling you it moved, and asking it
        twice more just spends the timeout budget before the same answer -- fix the URL
        with the matching `PDX1_*_URL` override instead.
        """
        last: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            self._attempts = attempt
            try:
                return self._fetch_live()
            except Exception as exc:  # noqa: BLE001 - classified below, then re-raised
                last = exc
                if attempt == self._max_attempts or not _is_retryable(exc):
                    raise
                delay = self._retry_backoff_s * (2 ** (attempt - 1))
                logger.warning(
                    "%s: attempt %d/%d failed (%s) -- retrying in %.1fs",
                    self.name,
                    attempt,
                    self._max_attempts,
                    exc,
                    delay,
                )
                time.sleep(delay)
        raise last  # pragma: no cover - the loop always returns or raises above

    def _read_raw(self) -> str:
        if self.fixture_path is not None:
            return self.fixture_path.read_text(encoding="utf-8")
        if self._live and self.feed_url:
            try:
                text = self._fetch_live_with_retry()
            except Exception as exc:  # noqa: BLE001 - any failure may fall back to cache
                cached = self._read_cache()
                if cached is None:
                    raise
                self._from_cache = True
                logger.warning(
                    "%s: live fetch failed after %d attempt(s) (%s) -- "
                    "serving last-good cache from %s",
                    self.name,
                    self._attempts,
                    exc,
                    self.cache_path(),
                )
                return cached
            self._write_cache(text)
            return text
        raise RuntimeError(
            f"{self.name}: no fixture_path configured and live fetch is not enabled"
        )

    def _annotate(self, result: FetchResult) -> FetchResult:
        result.http_status = self._http_status
        result.attempts = self._attempts
        result.from_cache = self._from_cache
        return result
