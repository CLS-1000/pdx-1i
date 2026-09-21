"""
Live HTTP fetching.

Proves that LiveSourceAdapter uses httpx when live=True and a fixture is not
provided, and falls back to the original error message otherwise.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


from pdx1.sources import OrestarAdapter, PortlandPressAdapter
from pdx1.sources.base import LiveSourceAdapter


# ── Class-attribute checks ────────────────────────────────────────────────────


def test_all_adapters_are_live_source_adapters():
    from pdx1.sources import OlisAdapter, SeiAdapter, WaPdcAdapter

    for cls in (OrestarAdapter, OlisAdapter, SeiAdapter, WaPdcAdapter, PortlandPressAdapter):
        assert issubclass(cls, LiveSourceAdapter), f"{cls.__name__} must subclass LiveSourceAdapter"


def test_every_live_adapter_has_a_feed_url():
    from pdx1.sources import OlisAdapter, SeiAdapter, WaPdcAdapter

    for cls in (OrestarAdapter, OlisAdapter, SeiAdapter, WaPdcAdapter, PortlandPressAdapter):
        assert cls.feed_url, f"{cls.__name__}.feed_url must be non-empty"
        assert cls.feed_url.startswith("https://"), f"{cls.__name__}.feed_url must be HTTPS"


# ── live=False (default) ──────────────────────────────────────────────────────


def test_adapter_without_fixture_and_live_false_reports_clearly():
    """When live=False and no fixture, error message says live fetch is not enabled."""
    result = OrestarAdapter().safe_fetch()
    assert not result.ok
    assert "live fetch is not enabled" in result.errors[0]


def test_adapter_with_fixture_ignores_live_flag(fixture_dir):
    """fixture_path takes priority over the live flag."""
    adapter = OrestarAdapter(fixture_path=fixture_dir / "orestar.json", live=True)
    # Should not attempt any network call -- reads from the file.
    result = adapter.safe_fetch()
    assert result.ok
    assert len(result) == 3


# ── live=True — httpx path ────────────────────────────────────────────────────


def test_live_adapter_calls_httpx_when_live_true(fixture_dir):
    """With live=True and no fixture, _read_raw calls httpx.get."""
    fixture_content = (fixture_dir / "orestar.json").read_text(encoding="utf-8")

    mock_response = MagicMock()
    mock_response.text = fixture_content
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.get", return_value=mock_response) as mock_get:
        adapter = OrestarAdapter(live=True)
        result = adapter.safe_fetch()

    # The instance URL, not the class attribute: ORESTAR's bulk export is published
    # per calendar year, so the adapter resolves `{year}` at construction.
    mock_get.assert_called_once_with(
        adapter.feed_url,
        timeout=adapter.timeout,
        follow_redirects=True,
    )
    assert "{year}" not in adapter.feed_url
    assert result.ok
    assert len(result) == 3


def test_live_adapter_propagates_http_error(fixture_dir):
    """A non-2xx response from the live endpoint is reported as an adapter error."""
    import httpx

    with patch("httpx.get", side_effect=httpx.ConnectError("connection refused")):
        result = OrestarAdapter(live=True, retry_backoff_s=0).safe_fetch()

    assert not result.ok
    assert result.signals == []
    assert "ConnectError" in result.errors[0]


def test_live_adapter_timeout_is_forwarded():
    """The timeout set on the adapter reaches httpx.get."""
    mock_response = MagicMock()
    mock_response.text = "[]"
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.get", return_value=mock_response) as mock_get:
        adapter = OrestarAdapter(timeout=99, live=True)
        adapter._read_raw()

    _, kwargs = mock_get.call_args
    assert kwargs.get("timeout") == 99 or mock_get.call_args[0][1] == 99 or mock_get.call_args[1].get("timeout") == 99


# ── Default adapters in live mode ─────────────────────────────────────────────


def test_default_adapters_fixture_mode_uses_fixture_paths(fixture_dir):
    from pdx1.config import Settings
    from pdx1.pipeline import default_adapters

    settings = Settings(live_fetch=False)
    adapters = default_adapters(settings, fixture_dir)

    assert all(a.fixture_path is not None for a in adapters)
    assert all(not getattr(a, "_live", False) for a in adapters)


def test_default_adapters_live_mode_sets_live_flag():
    from pdx1.config import Settings
    from pdx1.pipeline import default_adapters

    settings = Settings(live_fetch=True)
    adapters = default_adapters(settings)

    # All adapters should have live=True and no fixture_path.
    for adapter in adapters:
        assert getattr(adapter, "_live", False), f"{adapter.name} should have _live=True"
        assert adapter.fixture_path is None


def test_default_adapters_live_mode_includes_watch_targets():
    from pdx1.config import Settings
    from pdx1.pipeline import default_adapters
    from pdx1.watch import WatchAdapter

    settings = Settings(live_fetch=True)
    adapters = default_adapters(settings)

    watch = [a for a in adapters if isinstance(a, WatchAdapter)]
    assert len(watch) == 6, "Expected 6 watch targets"
