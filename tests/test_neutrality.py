"""
Neutrality checks.

Attribution enforces: a section citing a record the engine does not hold does not
publish. Tone and hedging observe: they record what vocabulary they matched and never
withhold anything.

The tone tests below assert both halves of that -- the scan still detects what it
always detected, and the result always passes anyway. Detection without enforcement is
easy to break silently in either direction, so both are pinned.
"""

from __future__ import annotations

import pytest

from pdx1.neutrality import check_attribution, check_tone

NEUTRAL = (
    "ORESTAR transaction ORE-2026-4417392 records a contribution of $12,500.00 to "
    "committee PAC-19442, filed 2026-05-27. The contributor is listed as a political "
    "committee registered in Oregon."
)


# ── Tone ─────────────────────────────────────────────────────────────────────


def test_neutral_record_language_passes():
    result = check_tone(NEUTRAL)
    assert result
    assert result.clean
    assert result.observations() == []


def test_measurement_language_passes():
    result = check_tone(
        "Measured 11.00 against a 90-day baseline of 5.00 (sd 2.00, n=8) -- 3.0 sigma."
    )
    assert result
    assert result.clean


def test_tone_always_passes_even_when_it_matches():
    """
    The core of observation mode. Detection is unchanged; enforcement is gone.

    If this ever fails, sections are being withheld again and the live-run problem it
    was changed to solve -- press headlines tripping the scan -- is back.
    """
    result = check_tone("The filing shows corruption and a bribery scheme.")
    assert result.passed is True
    assert bool(result) is True
    assert not result.clean


@pytest.mark.parametrize(
    "word", ["corrupt", "bribery", "kickback", "scheme", "collusion", "wrongdoing"]
)
def test_prosecutorial_words_are_observed(word):
    result = check_tone(f"The filing shows {word} in the contribution record.")
    assert result, "observation only -- the passage still passes"
    assert word in result.prosecutorial
    assert word in result.matched_terms


@pytest.mark.parametrize(
    "phrase",
    [
        "deliberately",
        "knowingly",
        "in exchange for",
        "in order to",
        "to conceal",
        "designed to",
    ],
)
def test_motive_attribution_is_observed(phrase):
    result = check_tone(f"The committee filed the report {phrase} meet the deadline.")
    assert result
    assert phrase in result.motive
    assert phrase in result.matched_terms


@pytest.mark.parametrize(
    "word", ["suspicious", "troubling", "cozy", "brazen", "questionable"]
)
def test_loaded_framing_is_observed(word):
    result = check_tone(f"A {word} pattern appears in the filings.")
    assert result
    assert word in result.loaded
    assert word in result.matched_terms


def test_observation_payload_has_the_audit_shape():
    result = check_tone("A suspicious and corrupt arrangement.")
    payloads = result.observations()
    assert len(payloads) == 1

    payload = payloads[0]
    assert payload["gate"] == "tone_gate"
    assert payload["rule"] == "observation_only"
    assert payload["severity"] == "info"
    assert payload["matched_terms"] == ["corrupt", "suspicious"]
    assert payload["note"]


def test_matched_terms_are_sorted_and_deduplicated():
    result = check_tone("Corrupt, corrupt, and suspicious dealings.")
    assert result.matched_terms == ("corrupt", "suspicious")


def test_reason_names_what_matched():
    result = check_tone("A suspicious and corrupt arrangement.")
    reason = result.reason()
    assert "prosecutorial" in reason
    assert "loaded" in reason


def test_clean_reason_is_stated():
    assert check_tone(NEUTRAL).reason() == "tone gate: pass"


def test_tone_check_is_case_insensitive():
    result = check_tone("CORRUPT dealings")
    assert not result.clean
    assert "corrupt" in result.matched_terms


def test_vocabulary_is_matched_without_context_awareness():
    """
    'scheme' is in the vocabulary, so a colour scheme matches it.

    Documenting that the scan is a flat vocabulary lookup. Under the old behaviour this
    cost a dropped section; now it costs an observation, which is the trade the change
    was made for.
    """
    result = check_tone("The color scheme of the chart.")
    assert result, "no longer withheld"
    assert "scheme" in result.matched_terms


# ── Attribution ──────────────────────────────────────────────────────────────


def test_cited_known_record_passes():
    result = check_attribution(NEUTRAL, ["rec_abc"], {"rec_abc", "rec_def"})
    assert result
    assert result.cited == ("rec_abc",)


def test_uncited_section_is_rejected():
    result = check_attribution(NEUTRAL, [], {"rec_abc"})
    assert not result
    assert result.missing_citation


def test_unknown_record_id_is_rejected():
    """Citing a record the engine does not hold is worse than citing nothing."""
    result = check_attribution(NEUTRAL, ["rec_ghost"], {"rec_abc"})
    assert not result
    assert result.unknown == ("rec_ghost",)


@pytest.mark.parametrize(
    "phrase",
    ["sources say", "reportedly", "allegedly", "it is understood", "critics say"],
)
def test_vague_attribution_is_rejected(phrase):
    result = check_attribution(f"The committee {phrase} filed late.", ["rec_a"], {"rec_a"})
    assert not result
    assert phrase in result.vague


def test_partial_unknown_ids_still_reject():
    result = check_attribution(NEUTRAL, ["rec_a", "rec_ghost"], {"rec_a"})
    assert not result
    assert result.unknown == ("rec_ghost",)


def test_reason_reports_the_citation_count():
    result = check_attribution(NEUTRAL, ["rec_a", "rec_b"], {"rec_a", "rec_b"})
    assert "2 record(s) cited" in result.reason()


def test_reason_names_the_failure():
    result = check_attribution("Sources say the filing was late.", [], set())
    reason = result.reason()
    assert "no source record cited" in reason
    assert "vague attribution" in reason
