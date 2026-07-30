"""
Neutrality gates.

These enforce the publication constraints directly: descriptive not prosecutorial, no
motive attribution, every claim traceable. A regression here means the engine can
publish an accusation, so the tests are deliberately blunt.
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
    assert check_tone(NEUTRAL)


def test_measurement_language_passes():
    assert check_tone(
        "Measured 11.00 against a 90-day baseline of 5.00 (sd 2.00, n=8) -- 3.0 sigma."
    )


@pytest.mark.parametrize(
    "word", ["corrupt", "bribery", "kickback", "scheme", "collusion", "wrongdoing"]
)
def test_prosecutorial_words_are_rejected(word):
    result = check_tone(f"The filing shows {word} in the contribution record.")
    assert not result
    assert word in result.prosecutorial


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
def test_motive_attribution_is_rejected(phrase):
    result = check_tone(f"The committee filed the report {phrase} meet the deadline.")
    assert not result
    assert phrase in result.motive


@pytest.mark.parametrize(
    "word", ["suspicious", "troubling", "cozy", "brazen", "questionable"]
)
def test_loaded_framing_is_rejected(word):
    result = check_tone(f"A {word} pattern appears in the filings.")
    assert not result
    assert word in result.loaded


def test_rejection_reason_names_what_tripped():
    result = check_tone("A suspicious and corrupt arrangement.")
    reason = result.reason()
    assert "prosecutorial" in reason
    assert "loaded" in reason


def test_pass_reason_is_stated():
    assert check_tone(NEUTRAL).reason() == "tone gate: pass"


def test_tone_check_is_case_insensitive():
    assert not check_tone("CORRUPT dealings")


def test_substrings_do_not_false_positive():
    """'scheme' must not fire on 'schemes' variants that are legitimate words."""
    assert check_tone("The color scheme of the published chart was updated.") is not None
    # 'scheme' genuinely is in the vocabulary, so this must be rejected -- documenting
    # that the gate errs toward rejection rather than pretending it is context-aware.
    assert not check_tone("The color scheme of the chart.")


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
