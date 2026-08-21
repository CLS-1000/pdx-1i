"""
Bounded retry and per-fetch measurement.

The retry exists so a momentary transport failure does not cost a feed for the day.
The bound exists because the alternative is worse than the failure: a cron job that
hangs inside a retry loop is invisible until someone goes looking, where a job that
fails is visible at 06:05.

Nothing here touches the network -- `httpx.get` is patched throughout.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from pdx1.sources.base import DEFAULT_MAX_ATTEMPTS, _is_retryable
from pdx1.sources.orestar import OrestarAdapter


def _response(status: int, text: str = "") -> httpx.Response:
    return httpx.Response(
        status_code=status,
        text=text,
        request=httpx.Request("GET", "https://example.invalid/feed"),
    )


def _adapter(**kwargs):
    """A live adapter with the backoff removed, so tests do not sleep."""
    kwargs.setdefault("retry_backoff_s", 0)
    return OrestarAdapter(live=True, **kwargs)


# ── The bound ────────────────────────────────────────────────────────────────


def test_retryable_failure_is_attempted_exactly_max_attempts_times():
    with patch("httpx.get", side_effect=httpx.ConnectError("refused")) as get:
        result = _adapter(max_attempts=3).safe_fetch()

    assert get.call_count == 3
    assert result.attempts == 3
    assert not result.ok


def test_attempt_count_is_configurable_and_honoured():
    with patch("httpx.get", side_effect=httpx.ConnectError("refused")) as get:
        _adapter(max_attempts=5).safe_fetch()
    assert get.call_count == 5


def test_max_attempts_of_one_means_no_retry():
    with patch("httpx.get", side_effect=httpx.ConnectError("refused")) as get:
        _adapter(max_attempts=1).safe_fetch()
    assert get.call_count == 1


def test_attempts_are_bounded_even_when_every_call_fails():
    """
    The guard against the loop that never ends.

    A permanently unreachable endpoint must cost a bounded number of calls, not spin
    until the process is killed.
    """
    with patch("httpx.get", side_effect=httpx.ConnectError("refused")) as get:
        _adapter(max_attempts=DEFAULT_MAX_ATTEMPTS).safe_fetch()
    assert get.call_count == DEFAULT_MAX_ATTEMPTS


# ── What is worth retrying ───────────────────────────────────────────────────


@pytest.mark.parametrize("status", [500, 502, 503, 504, 429])
def test_transient_status_codes_retry(status):
    with patch("httpx.get", return_value=_response(status)) as get:
        _adapter(max_attempts=3).safe_fetch()
    assert get.call_count == 3


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410])
def test_settled_client_errors_do_not_retry(status):
    """
    A 404 is the endpoint telling you it moved.

    Asking twice more spends the timeout budget to receive the same answer. The fix is
    the matching PDX1_*_URL override, and the run should report that quickly.
    """
    with patch("httpx.get", return_value=_response(status)) as get:
        result = _adapter(max_attempts=3).safe_fetch()

    assert get.call_count == 1
    assert result.attempts == 1
    assert not result.ok


def test_a_retry_that_succeeds_is_not_an_error(fixture_dir):
    body = (fixture_dir / "orestar.json").read_text(encoding="utf-8")
    responses = [httpx.ConnectError("refused"), _response(200, body)]

    with patch("httpx.get", side_effect=responses) as get:
        result = _adapter(max_attempts=3).safe_fetch()

    assert get.call_count == 2
    assert result.ok
    assert result.attempts == 2
    assert len(result) == 3


def test_non_http_failures_are_not_retried():
    """A missing dependency or a parse bug is deterministic; repeating it just waits."""
    assert _is_retryable(ValueError("bad payload")) is False
    assert _is_retryable(RuntimeError("no feed_url")) is False


# ── Measurements ─────────────────────────────────────────────────────────────


def test_status_and_attempts_are_recorded_on_success(fixture_dir):
    body = (fixture_dir / "orestar.json").read_text(encoding="utf-8")
    with patch("httpx.get", return_value=_response(200, body)):
        result = _adapter().safe_fetch()

    assert result.http_status == 200
    assert result.attempts == 1
    assert result.from_cache is False


def test_status_is_recorded_on_a_failing_response():
    with patch("httpx.get", return_value=_response(404)):
        result = _adapter().safe_fetch()
    assert result.http_status == 404


def test_status_is_none_when_no_response_was_ever_received():
    """A connection refused never produced a status, and must not invent one."""
    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        result = _adapter().safe_fetch()
    assert result.http_status is None


def test_elapsed_is_recorded_on_both_paths(fixture_dir):
    body = (fixture_dir / "orestar.json").read_text(encoding="utf-8")
    with patch("httpx.get", return_value=_response(200, body)):
        ok = _adapter().safe_fetch()
    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        bad = _adapter().safe_fetch()

    assert ok.elapsed_s >= 0.0
    assert bad.elapsed_s >= 0.0


def test_cache_fallback_is_flagged(tmp_path, fixture_dir):
    """
    Cached data is real but not fresh, and the run must be able to say which it served.
    """
    body = (fixture_dir / "orestar.json").read_text(encoding="utf-8")
    adapter = _adapter(cache_dir=tmp_path)

    with patch("httpx.get", return_value=_response(200, body)):
        first = adapter.safe_fetch()
    assert first.from_cache is False

    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        second = adapter.safe_fetch()

    assert second.ok
    assert second.from_cache is True
    assert len(second) == 3


# ── Zero is not success ──────────────────────────────────────────────────────


def test_an_empty_answer_is_flagged_separately_from_an_error():
    """
    Zero items and a broken adapter look identical downstream.

    `empty` is what lets the run tell them apart: this one answered 200 and returned
    nothing, which may be a quiet day or may be a feed that changed shape.
    """
    with patch("httpx.get", return_value=_response(200, "[]")):
        result = _adapter().safe_fetch()

    assert result.ok
    assert result.empty
    assert len(result) == 0


def test_a_populated_result_is_not_empty(fixture_dir):
    body = (fixture_dir / "orestar.json").read_text(encoding="utf-8")
    with patch("httpx.get", return_value=_response(200, body)):
        result = _adapter().safe_fetch()
    assert not result.empty


def test_a_failed_result_is_not_reported_as_empty():
    """`empty` means answered-with-nothing. A failure is an error, and says so."""
    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        result = _adapter().safe_fetch()
    assert not result.ok
    assert not result.empty
