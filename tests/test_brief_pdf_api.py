"""
GET /brief/pdf and /brief/{id}/pdf — the brief served in SPEC-1 WorldStateBrief form.

The renderer and its format are pinned in `test_brief_format.py`. What is pinned here
is the *endpoint*: that it serves the brief the store holds rather than assembling a
new one, that it resolves the diagram's entity ids through the store (which is the
whole reason the route exists rather than the caller rendering for itself), and that
it fails honestly when the optional dependency is absent.

One assertion here guards a routing accident rather than a behaviour: `/brief/pdf`
must be matched before `/brief/{brief_id}`. Declared the other way round the literal
path is swallowed as an id and the route 404s with a message about a brief called
"pdf" — which reads like missing data, not a misdeclared route.

Nothing touches the network; every store lands in tmp_path.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from pdx1.api.app import create_app
from pdx1.models import (
    Brief,
    BriefSection,
    ConfidenceTier,
    GateResult,
    IntelligenceRecord,
    Outcome,
    Priority,
    SourceType,
)

pytest.importorskip("reportlab", reason="PDF rendering needs the pdf extra")


def _record(record_id: str, entity_ids: list[str]) -> IntelligenceRecord:
    return IntelligenceRecord(
        record_id=record_id,
        run_id="pdx1_2026_0922_060000",
        source="OLIS",
        source_type=SourceType.OLIS,
        pattern=f"Pattern for {record_id}",
        outcome=Outcome.INVESTIGATE,
        priority=Priority.STANDARD,
        confidence=0.7,
        tier=ConfidenceTier.HARD_RECORD,
        gates=GateResult(credibility=True, volume=True, velocity=True, novelty=True),
        entity_ids=entity_ids,
        signal_id=f"sig_{record_id}",
        dedup_hash=f"hash_{record_id}",
        published_at=datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc),
    )


def _brief(**over) -> Brief:
    fields = dict(
        brief_id="brief_api_001",
        run_id="pdx1_2026_0922_060000",
        date="2026-09-22",
        headline="Two bodies filed this cycle",
        summary="Routine filings cleared the four-gate filter.",
        sections=[
            BriefSection(
                title="Under review",
                body="- [INVESTIGATE] rec_one",
                source_record_ids=["rec_one"],
            ),
            BriefSection(
                title="Watch list",
                body="- [MONITOR] rec_two",
                source_record_ids=["rec_two"],
            ),
        ],
        confidence=0.6,
        sources=["OLIS"],
        produced_at=datetime(2026, 9, 22, 6, 0, 0, tzinfo=timezone.utc),
    )
    fields.update(over)
    return Brief(**fields)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PDX1_STORE_PATH", str(tmp_path / "s.jsonl"))
    monkeypatch.setenv("PDX1_DB_PATH", str(tmp_path / "d.db"))
    monkeypatch.delenv("PDX1_API_KEY", raising=False)
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def populated(client):
    """A client whose store holds one brief and the two records behind it."""
    store = client.app.state.store
    # `mcp` and `metro` are tied in the registry, so the diagram has something to draw.
    store.write([_record("rec_one", ["mcp"]), _record("rec_two", ["metro"])])
    store.write_brief(_brief())
    return client


def _text(body: bytes, tmp_path) -> str:
    reader = pytest.importorskip("pypdf", reason="text extraction needs pypdf")
    path = tmp_path / "got.pdf"
    path.write_bytes(body)
    return "\n".join(p.extract_text() for p in reader.PdfReader(str(path)).pages)


# ── It serves a PDF ──────────────────────────────────────────────────────────


def test_latest_brief_renders_as_a_pdf(populated):
    r = populated.get("/brief/pdf")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/pdf")
    assert r.content.startswith(b"%PDF-")


def test_archived_brief_renders_by_id(populated):
    r = populated.get("/brief/brief_api_001/pdf")
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF-")


def test_the_literal_pdf_path_is_not_read_as_a_brief_id(populated):
    """
    The routing accident this file exists to catch.

    Were `/{brief_id}` declared first, `/brief/pdf` would match it, the store would
    find no brief called "pdf", and the response would be a 404 that looks like
    absent data rather than a misdeclared route.
    """
    r = populated.get("/brief/pdf")
    assert r.status_code == 200
    assert "No brief with id" not in r.text


def test_the_pdf_is_the_stored_brief_not_a_freshly_assembled_one(populated, tmp_path):
    """
    Both representations must be the same document.

    The renderer writes no prose of its own, so anything in the PDF that is not in
    the stored brief would mean the endpoint invented it.
    """
    stored = populated.get("/brief").json()
    text = _text(populated.get("/brief/pdf").content, tmp_path)
    assert stored["headline"] in text
    assert stored["date"] in text
    assert stored["run_id"][:8] in text


def test_the_filename_carries_the_brief_id(populated):
    disposition = populated.get("/brief/brief_api_001/pdf").headers[
        "content-disposition"
    ]
    assert "brief_api_001.pdf" in disposition


def test_an_awkward_brief_id_cannot_shape_the_header(client):
    """
    The id is store-controlled, but it is interpolated into a response header.

    A quote or a newline in it would end the filename early or start another header
    line, so everything outside the safe set is replaced rather than trusted.
    """
    store = client.app.state.store
    store.write_brief(_brief(brief_id='b"\r\nX-Injected: yes'))
    r = client.get("/brief/pdf")
    assert r.status_code == 200
    assert "x-injected" not in {k.lower() for k in r.headers}
    assert r.headers["content-disposition"] == 'inline; filename="b___X-Injected__yes.pdf"'


# ── The diagram, which is why the route holds the store ──────────────────────


def test_the_diagram_is_drawn_from_the_records_behind_the_brief(populated, tmp_path):
    """
    The endpoint resolves `source_record_ids` to entity ids and passes them down.

    `Brief` carries record ids only, so without that resolution `render_brief_pdf`
    gets no `entity_ids` and the diagram is silently absent from every served PDF.
    """
    from pdx1.publication.network_diagram import CAPTION

    text = _text(populated.get("/brief/pdf").content, tmp_path)
    assert CAPTION.split(".")[0] in text


def test_no_diagram_when_the_cited_bodies_share_no_tie(client, tmp_path):
    """
    A brief whose bodies are unconnected gets a document, not an empty frame.

    An empty frame would read as "these bodies are unrelated" — a claim the registry
    does not make. `build_network_drawing` refuses it; the endpoint must not paper
    over the refusal.
    """
    from pdx1.publication.network_diagram import CAPTION

    store = client.app.state.store
    store.write([_record("rec_one", ["pge"])])
    store.write_brief(
        _brief(
            sections=[
                BriefSection(
                    title="Under review",
                    body="- [INVESTIGATE] rec_one",
                    source_record_ids=["rec_one"],
                )
            ]
        )
    )
    r = client.get("/brief/pdf")
    assert r.status_code == 200
    assert CAPTION.split(".")[0] not in _text(r.content, tmp_path)


# ── Absence and failure ──────────────────────────────────────────────────────


def test_404_before_any_cycle_has_published(client):
    assert client.get("/brief/pdf").status_code == 404


def test_404_for_an_unknown_brief_id(populated):
    r = populated.get("/brief/brief_nope/pdf")
    assert r.status_code == 404
    assert "brief_nope" in r.json()["detail"]


def test_missing_reportlab_reports_unavailable_not_server_error(populated, monkeypatch):
    """
    reportlab is an optional extra. Without it the feature is unconfigured, not
    broken, and a 500 would send a reader looking for a bug that is not there.
    """
    import builtins

    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if "pdf_renderer" in name:
            raise ImportError("no reportlab")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    r = populated.get("/brief/pdf")
    assert r.status_code == 503
    assert "reportlab" in r.json()["detail"]


def test_both_pdf_routes_require_the_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("PDX1_STORE_PATH", str(tmp_path / "s.jsonl"))
    monkeypatch.setenv("PDX1_DB_PATH", str(tmp_path / "d.db"))
    monkeypatch.setenv("PDX1_API_KEY", "test-secret-key")
    with TestClient(create_app()) as c:
        assert c.get("/brief/pdf").status_code == 401
        assert c.get("/brief/brief_api_001/pdf").status_code == 401
