"""
The fixture-vs-live switch.

These tests exist because the failure they guard against is silent. An unset or
misspelled `PDX1_LIVE` used to resolve to fixture mode, which on the VM means a brief
published every morning, on schedule, entirely from checked-in payloads -- with a
successful exit status and nothing in the log to say the engine never touched a real
source.
"""

from __future__ import annotations

import pytest

from pdx1.config import ConfigError, Settings
from pdx1.pipeline import main

#: Every environment variable these tests touch. Cleared before each case so a value
#: inherited from the developer's shell or a checked-out .env cannot decide the result.
_KEYS = ("PDX1_LIVE", "PDX1_ENVIRONMENT")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in _KEYS:
        monkeypatch.delenv(key, raising=False)


# ── Absence ──────────────────────────────────────────────────────────────────


def test_unset_live_defaults_to_fixtures_outside_production():
    """Development and CI keep the reproducible default."""
    assert Settings.from_env().live_fetch is False


def test_unset_live_refuses_in_production(monkeypatch):
    """The case that matters: no fixture default on the VM."""
    monkeypatch.setenv("PDX1_ENVIRONMENT", "production")
    with pytest.raises(ConfigError, match="PDX1_LIVE is unset"):
        Settings.from_env()


def test_blank_live_refuses_in_production(monkeypatch):
    """`PDX1_LIVE=` in an .env file is absence, not a choice."""
    monkeypatch.setenv("PDX1_ENVIRONMENT", "production")
    monkeypatch.setenv("PDX1_LIVE", "   ")
    with pytest.raises(ConfigError):
        Settings.from_env()


def test_production_is_matched_case_insensitively(monkeypatch):
    monkeypatch.setenv("PDX1_ENVIRONMENT", "Production")
    with pytest.raises(ConfigError):
        Settings.from_env()


# ── Ambiguity ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["ture", "maybe", "2", "y", "enabled", "TRUE!"])
def test_unrecognised_value_refuses_in_every_environment(monkeypatch, value):
    """
    A typo is not a request for fixture mode.

    `PDX1_LIVE=ture` resolving to False is exactly the silent fixture run this whole
    module exists to prevent, and it would do so in development too.
    """
    monkeypatch.setenv("PDX1_LIVE", value)
    with pytest.raises(ConfigError, match="not a recognised boolean"):
        Settings.from_env()


# ── Explicit choices ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " true "])
def test_true_spellings_select_live(monkeypatch, value):
    monkeypatch.setenv("PDX1_ENVIRONMENT", "production")
    monkeypatch.setenv("PDX1_LIVE", value)
    assert Settings.from_env().live_fetch is True


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off"])
def test_false_spellings_select_fixtures_even_in_production(monkeypatch, value):
    """
    Production may replay fixtures -- it just has to say so.

    The requirement is an explicit choice, not a ban. An operator debugging the VM
    against known payloads is legitimate; doing it by accident is not.
    """
    monkeypatch.setenv("PDX1_ENVIRONMENT", "production")
    monkeypatch.setenv("PDX1_LIVE", value)
    assert Settings.from_env().live_fetch is False


# ── The CLI surface ──────────────────────────────────────────────────────────


def test_cli_refuses_with_exit_2_and_no_traceback(monkeypatch, capsys):
    """
    A refusal must read as a refusal to whatever is watching.

    Exit 2 rather than 0 so systemd and the scheduler register a failed run; the reason
    on stderr rather than a traceback so the operator can act on it.
    """
    monkeypatch.setenv("PDX1_ENVIRONMENT", "production")
    assert main([]) == 2
    captured = capsys.readouterr()
    assert "refusing to run" in captured.err
    assert "PDX1_LIVE" in captured.err
    assert "Traceback" not in captured.err


def test_cli_refusal_writes_nothing(monkeypatch, tmp_path, capsys):
    """A refused run must not leave a half-built store behind."""
    store = tmp_path / "signals.jsonl"
    db = tmp_path / "pdx1.db"
    monkeypatch.setenv("PDX1_ENVIRONMENT", "production")
    monkeypatch.setenv("PDX1_STORE_PATH", str(store))
    monkeypatch.setenv("PDX1_DB_PATH", str(db))

    assert main([]) == 2
    assert not store.exists()
    assert not db.exists()
