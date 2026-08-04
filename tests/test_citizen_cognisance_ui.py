from __future__ import annotations

from pathlib import Path

import pytest


PAGE = Path(__file__).resolve().parents[1] / "ui" / "citizen-cognisance.html"


@pytest.fixture(scope="module")
def source() -> str:
    assert PAGE.exists(), f"{PAGE} is missing"
    return PAGE.read_text(encoding="utf-8")


def test_skip_link_matches_controls_destination(source):
    assert '<a class="skip" href="#controls">Skip to the filters</a>' in source


def test_api_base_uses_same_origin(source):
    assert "new URL('/api/v1/nodes', window.location.href).href.replace(/\\/$/, '')" in source
    assert "http://localhost:8000/api/v1/nodes" not in source


def test_hours_since_clamps_future_timestamps(source):
    assert "return Math.max(0, (Date.now() - t) / 3.6e6);" in source


def test_signal_filter_uses_pressed_state_buttons(source):
    assert 'role="radio"' not in source
    assert 'role="radiogroup"' not in source
    assert 'aria-checked="' not in source
    assert 'aria-pressed="${s === \'all\'}"' in source
    assert "x.setAttribute('aria-pressed', String(x.dataset.sig === sigFilter))" in source
