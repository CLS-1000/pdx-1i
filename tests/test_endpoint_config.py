"""
Endpoint overrides and the `--check-endpoints` probe.

Feed URLs rot. A live run on 2026-08-06 found most of the registered defaults
returning 404, and the failure mode is quiet by design -- a dead adapter is recorded
as an error and the cycle carries on, which is right for a cron job and useless when
you are trying to work out what still answers.

Two things address that, and both are tested here: every endpoint is overridable from
config so a correction costs an .env line rather than a release, and the probe reports
what each one actually answers.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

from pdx1.config import Settings, SourceUrls
from pdx1.pipeline import check_endpoints, default_adapters
from pdx1.sources import OlisAdapter, PortlandPressAdapter


def _live(**urls) -> Settings:
    return replace(Settings(), live_fetch=True, urls=SourceUrls(**urls))


# ── Overrides ─────────────────────────────────────────────────────────────────


def test_an_adapter_uses_its_registered_url_by_default():
    adapters = {a.name: a for a in default_adapters(_live())}
    assert adapters["OLIS"].feed_url == OlisAdapter.feed_url


@pytest.mark.parametrize(
    "field,name",
    [
        ("orestar", "ORESTAR"),
        ("olis", "OLIS"),
        ("sei", "SEI"),
        ("wa_pdc", "WA_PDC"),
        ("portland_press", "PORTLAND_PRESS"),
    ],
)
def test_each_endpoint_can_be_overridden(field, name):
    """A publisher moving an endpoint must not require a code change."""
    override = "https://example.invalid/moved"
    adapters = {a.name: a for a in default_adapters(_live(**{field: override}))}
    assert adapters[name].feed_url == override


def test_orestar_override_survives_year_binding():
    """
    Regression: ORESTAR re-read the class attribute when binding `{year}`, which threw
    away the override entirely -- the one endpoint hardest to correct was the one that
    could not be.
    """
    override = "https://example.invalid/moved.zip"
    adapters = {a.name: a for a in default_adapters(_live(orestar=override))}
    assert adapters["ORESTAR"].feed_url == override


def test_a_year_templated_override_is_still_formatted():
    """A corrected URL is allowed to carry `{year}` too."""
    from pdx1.sources import OrestarAdapter

    adapter = OrestarAdapter(feed_url="https://example.invalid/{year}/tx.zip", year=2031)
    assert adapter.feed_url == "https://example.invalid/2031/tx.zip"


def test_an_empty_override_leaves_the_default_alone():
    """Unset means "use the registered default", not "use an empty URL"."""
    adapters = {a.name: a for a in default_adapters(_live(olis=""))}
    assert adapters["OLIS"].feed_url == OlisAdapter.feed_url


def test_an_override_does_not_leak_onto_the_class():
    """Setting an instance URL must not rewrite the default for everything else."""
    default_adapters(_live(olis="https://example.invalid/moved"))
    assert OlisAdapter.feed_url != "https://example.invalid/moved"
    assert OlisAdapter().feed_url == OlisAdapter.feed_url


def test_settings_reads_the_url_environment_keys(monkeypatch):
    monkeypatch.setenv("PDX1_OLIS_URL", "https://example.invalid/olis")
    monkeypatch.setenv("PDX1_WA_PDC_URL", "https://example.invalid/wa")
    settings = Settings.from_env()
    assert settings.urls.olis == "https://example.invalid/olis"
    assert settings.urls.wa_pdc == "https://example.invalid/wa"


def test_url_keys_default_to_empty(monkeypatch):
    for key in ("PDX1_ORESTAR_URL", "PDX1_OLIS_URL", "PDX1_SEI_URL", "PDX1_WA_PDC_URL"):
        monkeypatch.delenv(key, raising=False)
    assert Settings.from_env().urls == SourceUrls()


# ── The probe ─────────────────────────────────────────────────────────────────


def _probe(status: int | None = 200, error: Exception | None = None):
    if error is not None:
        return patch("httpx.get", side_effect=error)
    response = MagicMock()
    response.status_code = status
    return patch("httpx.get", return_value=response)


def test_probe_returns_zero_when_everything_answers(capsys):
    with _probe(200):
        assert check_endpoints(_live()) == 0
    out = capsys.readouterr().out
    assert "FAIL" not in out
    assert "endpoints answered" in out


def test_probe_returns_nonzero_when_something_fails(capsys):
    """Usable as a check, so a broken endpoint can fail a scheduled job."""
    with _probe(404):
        assert check_endpoints(_live()) == 1
    assert "FAIL" in capsys.readouterr().out


def test_probe_reports_a_transport_failure_by_exception_name(capsys):
    import httpx

    with _probe(error=httpx.ConnectError("name or service not known")):
        assert check_endpoints(_live()) == 1
    assert "ConnectError" in capsys.readouterr().out


def test_probe_covers_every_press_feed_not_just_the_registered_one(capsys):
    """All five outlets are polled live, so all five must be probed."""
    from pdx1.sources.portland_press import FEEDS

    with _probe(200):
        check_endpoints(_live())
    out = capsys.readouterr().out
    for outlet in FEEDS:
        assert f"PORTLAND_PRESS/{outlet}" in out


def test_probe_does_not_double_count_portland_press(capsys):
    """The aggregate adapter and its per-outlet feeds must not both be listed."""
    with _probe(200):
        check_endpoints(_live())
    lines = [ln for ln in capsys.readouterr().out.splitlines() if " PORTLAND_PRESS" in ln]
    assert all("PORTLAND_PRESS/" in ln for ln in lines)


def test_probe_covers_the_watch_targets(capsys):
    from pdx1.watch import WATCH_TARGETS

    with _probe(200):
        check_endpoints(_live())
    out = capsys.readouterr().out
    for target in WATCH_TARGETS:
        assert f"WATCH/{target.name}" in out


def test_probe_reports_the_overridden_url(capsys):
    with _probe(200):
        check_endpoints(_live(olis="https://example.invalid/moved"))
    assert "https://example.invalid/moved" in capsys.readouterr().out


def test_probe_harvests_nothing(tmp_path):
    """It is a read-only check: no signals parsed, no cache written."""
    with _probe(200):
        check_endpoints(replace(_live(), cache_dir=tmp_path))
    assert not list(tmp_path.iterdir())


def test_press_adapter_is_excluded_from_the_adapter_pass():
    """Guards the isinstance check the probe uses to avoid the duplicate."""
    adapters = default_adapters(_live())
    assert any(isinstance(a, PortlandPressAdapter) for a in adapters)
