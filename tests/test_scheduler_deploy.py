"""
Deployment preconditions for the scheduler and API.

These exist because all three defects they guard are invisible at runtime. A
scheduler that also binds port 8000 loses the race with `pdx1-api` and, under
`Restart=on-failure`, crash-loops instead of failing once. A scheduler running with
an undeclared environment is not distinguishable from one whose systemd unit dropped
the variable. And an API that binds 0.0.0.0 by default is reachable from outside the
VM by whatever finds the external IP, guarded only by an API key.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from pdx1.config import Settings

_KEYS = (
    "PDX1_ENVIRONMENT",
    "PDX1_LIVE",
    "PDX1_SCHEDULER_EMBEDDED_API",
    "PDX1_API_HOST",
    "PDX1_API_PORT",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in _KEYS:
        monkeypatch.delenv(key, raising=False)


# ── The port collision ───────────────────────────────────────────────────────


def test_embedded_api_is_off_by_default():
    """The VM runs pdx1-api as its own unit; the scheduler must not also bind 8000."""
    assert Settings.from_env().scheduler_embedded_api is False


def test_embedded_api_can_be_opted_into(monkeypatch):
    monkeypatch.setenv("PDX1_SCHEDULER_EMBEDDED_API", "true")
    assert Settings.from_env().scheduler_embedded_api is True


def test_headless_scheduler_starts_no_api_thread(monkeypatch):
    """The acceptance criterion: both can run on one host because only one binds."""
    monkeypatch.setenv("PDX1_ENVIRONMENT", "production")
    monkeypatch.setenv("PDX1_LIVE", "false")

    started: list[str] = []

    class _Sched:
        """`main` catches SystemExit from start(), so returning is how it stops."""

        def start(self):
            return None

    with patch("threading.Thread") as thread, patch(
        "pdx1.scheduler.build_scheduler", return_value=_Sched()
    ):
        thread.side_effect = lambda *a, **k: started.append("thread")
        from pdx1.scheduler import main as sched_main

        sched_main()

    assert started == [], "headless scheduler must not spawn an API thread"


def test_opted_in_scheduler_does_start_an_api_thread(monkeypatch):
    """The other half: the flag has to actually do something when set."""
    monkeypatch.setenv("PDX1_ENVIRONMENT", "production")
    monkeypatch.setenv("PDX1_LIVE", "false")
    monkeypatch.setenv("PDX1_SCHEDULER_EMBEDDED_API", "true")

    started: list[str] = []

    class _Sched:
        def start(self):
            return None

    with patch("threading.Thread") as thread, patch(
        "pdx1.scheduler.build_scheduler", return_value=_Sched()
    ):
        def _record(*_a, **_k):
            started.append("thread")

            class _T:
                def start(self):
                    return None

            return _T()

        thread.side_effect = _record
        from pdx1.scheduler import main as sched_main

        sched_main()

    assert started == ["thread"], "opting in must start the API thread"


# ── The undeclared environment ───────────────────────────────────────────────


def test_settings_still_defaults_environment_for_cli_and_tests():
    """Only the scheduler refuses; the CLI, API and suite must run without the var."""
    s = Settings.from_env()
    assert s.environment == "development"
    assert s.environment_declared is False


def test_declared_environment_is_recorded(monkeypatch):
    monkeypatch.setenv("PDX1_ENVIRONMENT", "development")
    assert Settings.from_env().environment_declared is True


def test_scheduler_refuses_an_undeclared_environment(monkeypatch, caplog):
    """
    An unset PDX1_ENVIRONMENT is indistinguishable from a unit file that lost it.

    Exit 2 so systemd records a failed start rather than a running service that
    quietly believes it is in development.
    """
    monkeypatch.setenv("PDX1_LIVE", "false")
    from pdx1.scheduler import main as sched_main

    with caplog.at_level("ERROR"):
        with pytest.raises(SystemExit) as exc:
            sched_main()

    assert exc.value.code == 2
    assert "PDX1_ENVIRONMENT is unset" in caplog.text


def test_scheduler_accepts_an_explicitly_declared_non_production_environment(monkeypatch):
    """Declared development is legitimate -- it just warns. Absence is the defect."""
    monkeypatch.setenv("PDX1_ENVIRONMENT", "development")
    monkeypatch.setenv("PDX1_LIVE", "false")

    class _Sched:
        def start(self):
            return None

    with patch("pdx1.scheduler.build_scheduler", return_value=_Sched()):
        from pdx1.scheduler import main as sched_main

        # Must return normally. The refusal path raises SystemExit(2); reaching the
        # end without raising is what "declared development is legitimate" means.
        sched_main()


# ── The bind address ─────────────────────────────────────────────────────────


def test_api_binds_loopback_by_default():
    """A VM with 0.0.0.0:8000 open is reachable by whatever finds the external IP."""
    assert Settings.from_env().api_host == "127.0.0.1"


def test_api_can_still_be_made_public_deliberately(monkeypatch):
    monkeypatch.setenv("PDX1_API_HOST", "0.0.0.0")
    assert Settings.from_env().api_host == "0.0.0.0"


def test_no_module_hardcodes_a_public_bind():
    """
    Guards the regression directly.

    Both entry points used to read PDX1_API_HOST with a 0.0.0.0 fallback of their own,
    so the default lived in two places and neither was the config.
    """
    import pathlib

    src = pathlib.Path("src/pdx1")
    offenders = [
        f"{path.relative_to(src)}:{n}"
        for path in src.rglob("*.py")
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if '"0.0.0.0"' in line and "_env(" not in line and "#" not in line.split('"0.0.0.0"')[0]
    ]
    assert not offenders, f"hardcoded public bind at {offenders}"


# ── Explicit schema init ─────────────────────────────────────────────────────


def test_init_store_creates_the_store_and_exits_zero(tmp_path, monkeypatch, capsys):
    """
    D2: schema creation on a clean host must be a documentable command.

    It was previously implicit in DualWriteStore.__init__, which works but gives a
    deploy document nothing to point at.
    """
    monkeypatch.setenv("PDX1_STORE_PATH", str(tmp_path / "s.jsonl"))
    monkeypatch.setenv("PDX1_DB_PATH", str(tmp_path / "s.db"))
    from pdx1.pipeline import main

    assert main(["--init-store"]) == 0
    assert (tmp_path / "s.db").exists(), "sqlite schema not created"
    out = capsys.readouterr().out
    assert "store initialised" in out
    # The resolved paths are printed so a deploy can confirm they land on the SSD.
    assert str(tmp_path / "s.db") in out


def test_init_store_is_idempotent(tmp_path, monkeypatch):
    """Re-running it on an existing store must not fail or destroy anything."""
    monkeypatch.setenv("PDX1_STORE_PATH", str(tmp_path / "s.jsonl"))
    monkeypatch.setenv("PDX1_DB_PATH", str(tmp_path / "s.db"))
    from pdx1.pipeline import main

    assert main(["--init-store"]) == 0
    (tmp_path / "s.jsonl").write_text('{"marker": 1}\n', encoding="utf-8")
    assert main(["--init-store"]) == 0
    assert '{"marker": 1}' in (tmp_path / "s.jsonl").read_text(encoding="utf-8")
