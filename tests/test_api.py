"""
FastAPI surface.

Exercises each route with the test client, both with and without an API key.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pdx1.api.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Test client with an isolated store and no API key."""
    monkeypatch.setenv("PDX1_STORE_PATH", str(tmp_path / "s.jsonl"))
    monkeypatch.setenv("PDX1_DB_PATH", str(tmp_path / "d.db"))
    monkeypatch.delenv("PDX1_API_KEY", raising=False)
    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client_with_key(tmp_path, monkeypatch):
    """Test client with API key enforcement active."""
    monkeypatch.setenv("PDX1_STORE_PATH", str(tmp_path / "s.jsonl"))
    monkeypatch.setenv("PDX1_DB_PATH", str(tmp_path / "d.db"))
    monkeypatch.setenv("PDX1_API_KEY", "test-secret-key")
    app = create_app()
    with TestClient(app) as c:
        yield c


# ── Health ────────────────────────────────────────────────────────────────────


def test_health_endpoint(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ── Auth ──────────────────────────────────────────────────────────────────────


def test_no_auth_required_when_key_not_configured(client):
    """When PDX1_API_KEY is blank, every request passes without a header."""
    assert client.get("/signals").status_code == 200


def test_auth_required_when_key_configured(client_with_key):
    r = client_with_key.get("/signals")
    assert r.status_code == 401


def test_correct_key_grants_access(client_with_key):
    r = client_with_key.get("/signals", headers={"X-API-Key": "test-secret-key"})
    assert r.status_code == 200


def test_wrong_key_is_rejected(client_with_key):
    r = client_with_key.get("/signals", headers={"X-API-Key": "wrong"})
    assert r.status_code == 401


# ── /signals ──────────────────────────────────────────────────────────────────


def test_signals_returns_empty_page_on_empty_store(client):
    r = client.get("/signals")
    assert r.status_code == 200
    data = r.json()
    assert data["items"] == []
    assert data["total"] == 0


def test_signals_pagination_fields_present(client):
    r = client.get("/signals?limit=10&offset=0")
    assert r.status_code == 200
    data = r.json()
    assert "limit" in data
    assert "offset" in data
    assert "total" in data


# ── /intel ────────────────────────────────────────────────────────────────────


def test_intel_returns_empty_page_on_empty_store(client):
    r = client.get("/intel")
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_intel_outcome_filter_is_accepted(client):
    r = client.get("/intel?outcome=ESCALATE")
    assert r.status_code == 200


# ── /leads ────────────────────────────────────────────────────────────────────


def test_leads_returns_empty_on_empty_store(client):
    r = client.get("/leads")
    assert r.status_code == 200
    assert r.json()["items"] == []


# ── /brief ────────────────────────────────────────────────────────────────────


def test_brief_returns_404_before_any_cycle(client):
    r = client.get("/brief")
    assert r.status_code == 404


# ── /cycle/run ────────────────────────────────────────────────────────────────


def test_cycle_run_returns_summary(client, fixture_dir):
    """A cycle over fixtures writes records and returns a valid summary."""
    r = client.post("/cycle/run")
    assert r.status_code == 200
    data = r.json()
    assert "run_id" in data
    assert data["harvested"] > 0
    assert data["written"] > 0


def test_cycle_run_populates_intel(client):
    """After a cycle, /intel returns records."""
    client.post("/cycle/run")
    r = client.get("/intel?limit=100")
    assert r.status_code == 200
    assert len(r.json()["items"]) > 0


def test_cycle_run_populates_brief(client):
    """After a cycle that publishes, /brief returns the assembled brief."""
    result = client.post("/cycle/run")
    data = result.json()
    if data.get("brief_id"):
        r = client.get("/brief")
        assert r.status_code == 200
        assert r.json()["brief_id"] == data["brief_id"]


# ── Brief persistence across processes ────────────────────────────────────────
#
# GET /brief reads the store, not process memory. Before that, a brief assembled by
# the CLI or the scheduler was invisible here and a restart lost it — and because the
# novelty gate drops already-stored signals, no later cycle could rebuild it.


@pytest.fixture
def api_env(tmp_path, monkeypatch):
    """Env pointing at one isolated store, so several app instances can share it."""
    monkeypatch.setenv("PDX1_STORE_PATH", str(tmp_path / "s.jsonl"))
    monkeypatch.setenv("PDX1_DB_PATH", str(tmp_path / "d.db"))
    monkeypatch.delenv("PDX1_API_KEY", raising=False)
    return tmp_path


def test_brief_survives_an_api_restart(api_env):
    """The exact reported failure: records survived a restart, the brief did not."""
    with TestClient(create_app()) as first:
        assert first.post("/cycle/run").status_code == 200
        original = first.get("/brief")
        assert original.status_code == 200
        brief_id = original.json()["brief_id"]

    # A brand new app instance over the same store — i.e. a restarted server.
    with TestClient(create_app()) as second:
        assert second.get("/intel").json()["total"] == 10
        again = second.get("/brief")
        assert again.status_code == 200
        assert again.json()["brief_id"] == brief_id


def test_brief_persists_after_a_barren_second_cycle(api_env):
    """A re-run yields no records, and must not erase the brief already published."""
    with TestClient(create_app()) as c:
        c.post("/cycle/run")
        brief_id = c.get("/brief").json()["brief_id"]

        second = c.post("/cycle/run").json()
        assert second["opportunities"] == 0
        assert second["brief_id"] is None

        assert c.get("/brief").json()["brief_id"] == brief_id


def test_a_cli_assembled_brief_is_visible_to_the_api(api_env):
    """The scheduler case — a different process assembles, the API serves."""
    from pdx1.config import Settings
    from pdx1.pipeline import default_adapters, run_cycle
    from pdx1.store import DualWriteStore

    settings = Settings.from_env()
    store = DualWriteStore(settings.store_path, settings.db_path, settings.briefs_path)
    result = run_cycle(
        settings=settings, adapters=default_adapters(settings), store=store
    )
    assert result.brief is not None

    with TestClient(create_app()) as c:
        assert c.get("/brief").json()["brief_id"] == result.brief.brief_id


def test_brief_404s_only_when_none_was_ever_published(client):
    assert client.get("/brief").status_code == 404


def test_brief_archive_and_lookup_by_id(api_env):
    with TestClient(create_app()) as c:
        c.post("/cycle/run")
        brief_id = c.get("/brief").json()["brief_id"]

        archive = c.get("/brief/archive")
        assert archive.status_code == 200
        assert [b["brief_id"] for b in archive.json()] == [brief_id]

        assert c.get(f"/brief/{brief_id}").json()["brief_id"] == brief_id
        assert c.get("/brief/brief_does_not_exist").status_code == 404
