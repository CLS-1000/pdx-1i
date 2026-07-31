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
