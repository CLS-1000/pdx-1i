"""Pins the sign-up page's honesty constraints and its place in the Pages build."""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "ui" / "signup.html"


@pytest.fixture(scope="module")
def source() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_submit_is_handled_in_script_not_posted_by_the_browser(source):
    assert "e.preventDefault();" in source
    assert "<form id=\"form\" novalidate>" in source
    assert "mailto:" not in source


def test_unconfigured_endpoint_refuses_rather_than_pretending(source):
    # Until a real endpoint is set, a sign-up must not reach the success panel.
    assert "const ENDPOINT_READY = !/REPLACE_ME/.test(FORM_ENDPOINT);" in source
    guard = source.index("if (!ENDPOINT_READY)")
    assert guard < source.index("done.hidden = false;")


def test_success_panel_makes_no_email_delivery_claim(source):
    # The free form tier sends no autoresponse, so the page must not promise one.
    done = source[source.index('id="done"'):]
    assert "inbox" not in done.lower()


def test_honeypot_present_and_hidden(source):
    assert 'name="_gotcha"' in source
    assert 'class="hp" aria-hidden="true"' in source


def test_pages_build_publishes_signup_and_landing_links_to_it():
    workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
    assert "cp ui/signup.html             _site/signup.html" in workflow
    landing = (ROOT / "ui" / "citizen-cognisance.html").read_text(encoding="utf-8")
    assert 'href="signup.html"' in landing
