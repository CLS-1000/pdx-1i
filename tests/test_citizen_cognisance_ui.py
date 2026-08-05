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
    assert "if (h < 0) return null;" in source


def test_signal_filter_uses_pressed_state_buttons(source):
    # Scope the absence checks to the signal-filter markup and buildControls snippet.
    sig_chips_idx = source.index('id="sig-chips"')
    build_controls_idx = source.index('function buildControls()')
    sig_filter_markup = source[sig_chips_idx:sig_chips_idx + 200]
    build_controls_snippet = source[build_controls_idx:build_controls_idx + 1200]
    assert 'role="radio"' not in sig_filter_markup
    assert 'role="radiogroup"' not in sig_filter_markup
    assert 'aria-checked="' not in build_controls_snippet
    assert 'aria-pressed="${s === \'all\'}"' in build_controls_snippet
    assert "x.setAttribute('aria-pressed', String(x.dataset.sig === sigFilter))" in build_controls_snippet
