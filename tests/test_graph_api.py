"""
GET /graph — the political web over HTTP.

This is the endpoint the force-directed web map needs. The taxonomies have to survive
the wire intact, because they are what the drawing encodes: node shape by `group`, line
style by `kind`, dashed for `disclosure`.

Two invariants get more attention than the rest, because breaking either would turn a
structural description into a claim about people:

  - officials are role-based seats, never named individuals;
  - `record_count` is a count, exposed without adjective or ranking language.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pdx1.api.app import create_app
from pdx1.graph import NODES, TIES
from pdx1.models import NodeGroup, TieKind


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PDX1_STORE_PATH", str(tmp_path / "s.jsonl"))
    monkeypatch.setenv("PDX1_DB_PATH", str(tmp_path / "d.db"))
    monkeypatch.delenv("PDX1_API_KEY", raising=False)
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def populated(client):
    """A client whose store has been through one cycle."""
    assert client.post("/cycle/run").status_code == 200
    return client


# ── The whole graph ──────────────────────────────────────────────────────────


def test_graph_returns_the_full_registry(client):
    body = client.get("/graph").json()

    assert body["node_count"] == len(NODES) == 31
    assert body["tie_count"] == len(TIES) == 40
    assert len(body["nodes"]) == 31
    assert len(body["ties"]) == 40


def test_every_node_id_is_unique_and_complete(client):
    ids = [n["id"] for n in client.get("/graph").json()["nodes"]]
    assert len(ids) == len(set(ids))
    assert set(ids) == {n.id for n in NODES}


def test_node_groups_survive_the_wire(client):
    """Shape is drawn from `group`, so all three must arrive."""
    groups = {n["group"] for n in client.get("/graph").json()["nodes"]}
    assert groups == {g.value for g in NodeGroup}


def test_every_tie_kind_survives_the_wire(client):
    """Line style is drawn from `kind`, so all five must arrive."""
    kinds = {t["kind"] for t in client.get("/graph").json()["ties"]}
    assert kinds == {k.value for k in TieKind}


def test_flagged_disclosures_are_marked(client):
    ties = client.get("/graph").json()["ties"]
    disclosures = [t for t in ties if t["kind"] == "disclosure"]
    assert disclosures
    assert any(t["flagged"] for t in disclosures)


def test_the_vacant_seat_is_flagged(client):
    nodes = client.get("/graph").json()["nodes"]
    flagged = [n for n in nodes if n["flag"]]
    assert [n["id"] for n in flagged] == ["mcp"]
    assert flagged[0]["flag"] == "VACANT"


def test_every_tie_endpoint_is_a_node_in_the_same_response(client):
    """A renderer must never be handed an edge pointing at nothing."""
    body = client.get("/graph").json()
    ids = {n["id"] for n in body["nodes"]}
    for tie in body["ties"]:
        assert tie["source"] in ids
        assert tie["target"] in ids


def test_officials_are_role_based_not_named(client):
    """
    The constraint the whole module rests on.

    A named individual here would turn a structural description into a
    characterisation of a person.
    """
    officials = [n for n in client.get("/graph").json()["nodes"] if n["group"] == "O"]
    assert officials
    for official in officials:
        assert any(
            token in official["label"]
            for token in ("Councilor", "Mayor", "Chair", "Commissioner", "President")
        ), official["label"]


# ── Record activity ──────────────────────────────────────────────────────────


def test_counts_are_zero_before_any_cycle(client):
    assert all(n["record_count"] == 0 for n in client.get("/graph").json()["nodes"])


def test_counts_reflect_the_stored_records(populated):
    nodes = populated.get("/graph").json()["nodes"]
    active = {n["id"]: n["record_count"] for n in nodes if n["record_count"]}

    assert active, "a cycle over the fixtures should touch some registry entities"
    assert active["metro"] >= 1
    assert all(v > 0 for v in active.values())


def test_counts_match_the_records_endpoint(populated):
    """The count and the list must not disagree."""
    nodes = populated.get("/graph").json()["nodes"]
    for node in nodes:
        if node["record_count"]:
            detail = populated.get(f"/graph/{node['id']}").json()
            assert len(detail["records"]) == node["record_count"]


def test_counts_do_not_double_count_a_rerun(populated):
    """A barren second cycle writes nothing, so counts must not move."""
    before = {n["id"]: n["record_count"] for n in populated.get("/graph").json()["nodes"]}
    populated.post("/cycle/run")
    after = {n["id"]: n["record_count"] for n in populated.get("/graph").json()["nodes"]}
    assert before == after


def test_entity_ids_match_exactly_not_by_substring(populated):
    """`pge` must not match a record that only mentions some other id."""
    detail = populated.get("/graph/pge").json()
    for record in detail["records"]:
        assert "pge" in record["entity_ids"]


# ── Node detail ──────────────────────────────────────────────────────────────


def test_node_detail_shape(populated):
    body = populated.get("/graph/pge").json()

    assert body["node"]["id"] == "pge"
    assert body["node"]["group"] == "E"
    assert body["ties"]
    assert body["neighbors"]


def test_node_detail_ties_all_touch_the_node(populated):
    body = populated.get("/graph/portland").json()
    for tie in body["ties"]:
        assert "portland" in (tie["source"], tie["target"])


def test_neighbors_are_deduplicated_and_exclude_self(populated):
    body = populated.get("/graph/metro").json()
    ids = [n["id"] for n in body["neighbors"]]
    assert len(ids) == len(set(ids))
    assert "metro" not in ids


def test_node_detail_for_an_official_lists_its_seat(populated):
    body = populated.get("/graph/mcp").json()
    seats = [t for t in body["ties"] if t["kind"] == "seat"]
    assert len(seats) == 1
    assert seats[0]["target"] == "metro"


def test_unknown_node_is_404(client):
    assert client.get("/graph/no_such_node").status_code == 404


def test_node_detail_record_limit_is_honoured(populated):
    body = populated.get("/graph/metro?limit=1").json()
    assert len(body["records"]) <= 1


# ── Districts ────────────────────────────────────────────────────────────────


def test_districts_cover_the_bistate_metro(client):
    names = {j["name"] for j in client.get("/graph/districts").json()}
    assert {
        "Clark County",
        "Washington County",
        "City of Portland",
        "Multnomah County",
        "Clackamas County",
        "Metro Council",
    } <= names


def test_districts_span_both_states(client):
    states = {j["state"] for j in client.get("/graph/districts").json()}
    assert states == {"OR", "WA", "REGIONAL"}


def test_metro_president_seat_is_vacant(client):
    metro = next(
        j for j in client.get("/graph/districts").json() if j["name"] == "Metro Council"
    )
    president = next(s for s in metro["seats"] if s["district"] == "Pres")
    assert president["status"] == "vacant"


def test_every_jurisdiction_has_seats(client):
    assert all(j["seats"] for j in client.get("/graph/districts").json())


# ── Auth ─────────────────────────────────────────────────────────────────────


def test_graph_routes_require_the_api_key_when_set(tmp_path, monkeypatch):
    monkeypatch.setenv("PDX1_STORE_PATH", str(tmp_path / "s.jsonl"))
    monkeypatch.setenv("PDX1_DB_PATH", str(tmp_path / "d.db"))
    monkeypatch.setenv("PDX1_API_KEY", "test-secret-key")

    with TestClient(create_app()) as c:
        for path in ("/graph", "/graph/districts", "/graph/pge"):
            assert c.get(path).status_code == 401, path
            ok = c.get(path, headers={"X-API-Key": "test-secret-key"})
            assert ok.status_code == 200, path


# ── Neutrality ───────────────────────────────────────────────────────────────


def test_the_response_carries_no_characterising_language(populated):
    """
    The graph shows structure. It must not editorialise about it.

    Guards against a future contributor adding a "risk" or "suspicion" field alongside
    the counts -- the graph is entitled to say how often a body appears and nothing more.
    """
    raw = populated.get("/graph").text.lower()
    for word in (
        "suspicious",
        "corrupt",
        "risk",
        "alarming",
        "questionable",
        "troubling",
        "wrongdoing",
    ):
        assert word not in raw
