"""Structural checks for the SPEC-1 daily brief views in ui/index.html."""

from __future__ import annotations

import re
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "ui" / "index.html"


def page() -> str:
    return SOURCE.read_text(encoding="utf-8")


def spec_styles(source: str) -> str:
    match = re.search(
        r'<style id="spec1-daily-views">(.*?)</style>',
        source,
        flags=re.DOTALL,
    )
    assert match, "SPEC-1 view styles are missing"
    return match.group(1)


def test_daily_views_use_existing_endpoints_and_keep_brief_palette_separate():
    source = page()
    for view in ("district-map", "signal-feed", "statistics"):
        assert f'data-view="{view}"' in source
        assert f'id="view-{view}"' in source
    assert 'apiFetch("/graph/districts")' in source
    assert 'apiFetch("/intel?"' in source
    assert 'apiFetch("/graph")' in source
    assert '<!-- SPEC-1 daily brief panels; the existing Brief palette remains unchanged. -->' in source


def test_signal_feed_renders_expandable_four_gate_record_details():
    source = page()
    assert "<details>" in source
    assert "<summary>Four-gate detail</summary>" in source
    gate_renderer = source[source.index("function gateRows"):source.index("function anomalyText")]
    for gate in ("credibility", "volume", "velocity", "novelty"):
        assert f'"{gate}"' in source
    assert "GATES.map(name =>" in gate_renderer
    assert "record.gates" in gate_renderer
    assert "gates.detail" in gate_renderer
    assert "record.run_id" in source


def test_district_map_requests_and_projects_real_gis_boundaries():
    source = page()
    assert "BoundaryDataWebMerc/MapServer/2/query" in source
    assert 'outSR: "3857"' in source
    assert "spatialReference" in source
    assert "6378137" in source
    assert "No substitute geometry is drawn." in source


def test_statistics_are_derived_from_existing_record_and_graph_shapes():
    source = page()
    assert "function loadAllIntel()" in source
    assert "/intel?limit=${limit}&offset=${offset}" in source
    assert "record.gates?.[gate] === true" in source
    assert "graph.node_count" in source
    assert "graph.tie_count" in source
    assert "jurisdiction.seats" in source


def test_daily_view_palette_is_spec1_and_status_hues_are_custom_properties():
    styles = spec_styles(page())
    colors = {value.lower() for value in re.findall(r"#[0-9a-fA-F]{3,6}\b", styles)}
    allowed = {"#000", "#fff", "#ccc", "#999", "#666", "#00ff00", "#ff0000"}
    assert colors <= allowed
    assert "--spec-pass: #00ff00;" in styles
    assert "--spec-alert: #ff0000;" in styles
    assert "rgba(255,0,0" not in styles
    assert ".spec-status.online { color: var(--spec-pass); }" in styles
    assert ".spec-status.offline { color: var(--spec-alert); }" in styles
