"""
The source-mode contract: one explicit value, no fixture default, refuse when unsure.

Fixture replay and live fetch run the same code and both end in a publishable brief,
so the difference between them is invisible in the output. That is why the selection
is a single declared value rather than a boolean with a default: an operator who
believes they are running live and is not would otherwise see a plausible brief
assembled from a frozen May 2026 snapshot and nothing to contradict it.

Every test here is about the refusals. The happy paths are covered by the rest of the
suite, which declares fixture mode in conftest.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from pdx1.config import ConfigError, Settings, SourceMode
from pdx1.models import Signal, SourceType
from pdx1.pipeline import default_adapters, main, run_cycle
from pdx1.store import DualWriteStore


@pytest.fixture
def clean_env(monkeypatch):
    """An environment with nothing said about the source mode."""
    monkeypatch.delenv("PDX1_SOURCE_MODE", raising=False)
    monkeypatch.delenv("PDX1_LIVE", raising=False)
    return monkeypatch


# ── Reading the value ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("live", SourceMode.LIVE),
        ("fixture", SourceMode.FIXTURE),
        ("LIVE", SourceMode.LIVE),
        ("  Fixture  ", SourceMode.FIXTURE),
    ],
)
def test_a_declared_mode_is_read(clean_env, raw, expected):
    clean_env.setenv("PDX1_SOURCE_MODE", raw)
    assert Settings.from_env().source_mode is expected


def test_a_missing_mode_refuses_to_run(clean_env):
    with pytest.raises(ConfigError, match="PDX1_SOURCE_MODE is not set"):
        Settings.from_env()


def test_a_blank_mode_refuses_to_run(clean_env):
    """Blank is the shape an unfilled .env line has, and it is not a default."""
    clean_env.setenv("PDX1_SOURCE_MODE", "   ")
    with pytest.raises(ConfigError, match="PDX1_SOURCE_MODE is not set"):
        Settings.from_env()


@pytest.mark.parametrize("raw", ["true", "false", "1", "0", "yes", "replay", "prod"])
def test_an_unrecognised_mode_refuses_to_run(clean_env, raw):
    """
    Including the boolean spellings the removed PDX1_LIVE accepted.

    `PDX1_SOURCE_MODE=true` from a half-finished migration must not read as live, and
    must not read as fixture either.
    """
    clean_env.setenv("PDX1_SOURCE_MODE", raw)
    with pytest.raises(ConfigError, match="is not a source mode"):
        Settings.from_env()


@pytest.mark.parametrize("legacy", ["true", "false", ""])
def test_the_legacy_key_refuses_to_run(clean_env, legacy):
    """
    A leftover PDX1_LIVE is refused rather than ignored, whatever it says.

    Ignoring it is the exact failure being designed out: a deployment that set
    PDX1_LIVE=true would keep serving fixtures, and the brief would not say so.
    """
    clean_env.setenv("PDX1_SOURCE_MODE", "fixture")
    clean_env.setenv("PDX1_LIVE", legacy)
    with pytest.raises(ConfigError, match="PDX1_LIVE is no longer read"):
        Settings.from_env()


def test_settings_has_no_source_mode_default():
    """Constructed directly, the mode is undeclared -- not fixture."""
    assert Settings().source_mode is None


# ── Selecting adapters ────────────────────────────────────────────────────────


def test_undeclared_mode_refuses_to_build_adapters():
    with pytest.raises(ConfigError, match="source mode is not declared"):
        default_adapters(Settings())


def test_live_mode_with_a_fixture_directory_refuses(tmp_path):
    """The two arguments contradict each other; honouring either one is a guess."""
    settings = Settings(source_mode=SourceMode.LIVE)
    with pytest.raises(ConfigError, match="fixture directory was passed"):
        default_adapters(settings, tmp_path)


def test_fixture_mode_builds_fixture_backed_adapters(fixture_dir):
    adapters = default_adapters(Settings(source_mode=SourceMode.FIXTURE), fixture_dir)
    assert [a.fixture_path is not None for a in adapters] == [True] * 5


# ── The CLI refuses rather than crashing ──────────────────────────────────────


def test_cli_refuses_without_a_declared_mode(clean_env, capsys):
    assert main([]) == 2
    assert "PDX1_SOURCE_MODE is not set" in capsys.readouterr().err


def test_cli_refuses_fixtures_flag_under_live_mode(clean_env, capsys, tmp_path):
    clean_env.setenv("PDX1_SOURCE_MODE", "live")
    assert main(["--fixtures", str(tmp_path)]) == 2
    assert "fixture directory was passed" in capsys.readouterr().err


# ── What the mode anchors ─────────────────────────────────────────────────────


class _StaleAdapter:
    """A feed that stopped updating. Duck-typed, so it reaches no network."""

    name = "STALE"

    def fetch(self) -> list[Signal]:
        return [
            Signal(
                source="STALE",
                source_type=SourceType.ORESTAR,
                text=" ".join(f"token{i}" for i in range(80)),
                published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                credibility=0.9,
            )
        ]


def _run(tmp_path, mode) -> tuple:
    settings = Settings(
        store_path=tmp_path / "s.jsonl", db_path=tmp_path / "p.db", source_mode=mode
    )
    store = DualWriteStore(settings.store_path, settings.db_path)
    return run_cycle(settings=settings, adapters=[_StaleAdapter()], store=store)


def test_a_live_run_anchors_the_velocity_gate_to_wall_clock(tmp_path):
    """
    A stale feed must not be able to say what "now" is.

    Anchoring to the newest harvested signal is what makes a fixture replay
    deterministic; under live fetch it would let a feed that stopped updating in
    January present its last batch as fresh.
    """
    result = _run(tmp_path, SourceMode.LIVE)

    assert result.opportunities == 0
    assert result.dropped.get("velocity") == 1
    anchored = datetime.strptime(result.run_id, "pdx1_%Y_%m%d_%H%M%S").replace(
        tzinfo=timezone.utc
    )
    assert datetime.now(timezone.utc) - anchored < timedelta(minutes=5)


def test_a_fixture_run_still_anchors_to_the_newest_signal(tmp_path):
    """Replay determinism is unchanged -- the same payload survives every run."""
    result = _run(tmp_path, SourceMode.FIXTURE)

    assert result.opportunities == 1
    assert result.run_id == "pdx1_2026_0101_000000"


# ── The API refuses to start ──────────────────────────────────────────────────


def test_the_api_refuses_to_start_without_a_declared_mode(clean_env, tmp_path):
    """
    Startup is where this has to fail, not the first POST /cycle/run.

    A served API is the surface an operator trusts to say what the engine is doing;
    one that starts without knowing which sources it reads has nothing truthful to
    report on that line.
    """
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    from pdx1.api.app import create_app

    clean_env.setenv("PDX1_STORE_PATH", str(tmp_path / "s.jsonl"))
    clean_env.setenv("PDX1_DB_PATH", str(tmp_path / "d.db"))

    with pytest.raises(ConfigError, match="PDX1_SOURCE_MODE is not set"):
        with fastapi_testclient.TestClient(create_app()):
            pass
