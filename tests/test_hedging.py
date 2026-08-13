"""
Hedging check -- observation only.

The check detects what tone and attribution structurally cannot: prose that implies a
finding while asserting nothing. Because no claim is made, the attribution gate has
nothing to check.

It no longer withholds anything. These tests pin both halves: the detection is
unchanged, and the result always passes. What that gives up is stated in
test_insinuation_is_detected_but_no_longer_withheld.
"""

from __future__ import annotations

import pytest

from pdx1.neutrality import check_attribution, check_hedging, check_tone
from pdx1.neutrality.hedging import _CONCLUSION_STEALING, _INSINUATION
from pdx1.neutrality.tone import _LOADED, _MOTIVE, _PROSECUTORIAL

# A section written the way the engine is supposed to write: measurement, then source.
NEUTRAL = (
    "- [ESCALATE] Metro Councilor · D2 filed a statement of economic interest naming "
    "Portland General Electric. Measured 3.0 sigma against a 90-day baseline of 5.00 "
    "(sd 2.00, n=8). Source ORESTAR, confidence 0.91 (HARD_RECORD), record rec_00c1a2."
)


def test_neutral_measurement_passes():
    result = check_hedging(NEUTRAL)
    assert result
    assert result.clean
    assert result.observations() == []


def test_empty_text_passes():
    result = check_hedging("")
    assert result
    assert result.clean


def test_hedging_always_passes_even_when_it_matches():
    """The core of observation mode: detection intact, enforcement gone."""
    result = check_hedging("The record clearly appears to show a pattern.")
    assert result.passed is True
    assert bool(result) is True
    assert not result.clean


# ── Conclusion-stealing ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "phrase",
    ["clearly", "obviously", "undoubtedly", "of course", "tellingly", "predictably"],
)
def test_conclusion_stealing_is_observed(phrase):
    result = check_hedging(f"The filing {phrase} shows a pattern of contributions.")
    assert result, "observation only -- the passage still passes"
    assert phrase in result.conclusion_stealing
    assert phrase in result.matched_terms
    assert "conclusion stated for the reader" in result.reason()


def test_single_tokens_match_on_word_boundaries():
    """`certainly` must not fire inside `uncertainly`."""
    assert check_hedging("The timeline is uncertainly documented in the record.").clean


# ── Insinuation ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "phrase",
    [
        "appears to",
        "seems to",
        "raises questions",
        "casts doubt",
        "no coincidence",
        "stops short of",
        "declined to say",
        "remains unclear why",
    ],
)
def test_insinuation_is_observed(phrase):
    result = check_hedging(f"The record {phrase} the councilor's vote on the measure.")
    assert result, "observation only -- the passage still passes"
    assert phrase in result.insinuation
    assert phrase in result.matched_terms
    assert "implication without a claim" in result.reason()


def test_insinuation_is_detected_but_no_longer_withheld():
    """
    What observation mode gives up, stated as a test rather than left in a docstring.

    An insinuating sentence asserts nothing, so it carries no prosecutorial vocabulary
    for the tone scan and makes no claim for attribution to trace. This check is still
    the only one that sees it -- but seeing is now all any of them do, so the sentence
    reaches a reader with an audit note attached rather than being held back.

    If the project later decides that is the wrong trade, this test is where the
    decision is written down.
    """
    insinuation = (
        "The timing of the contribution raises questions about the vote that followed. "
        "Source ORESTAR, record rec_00c1a2."
    )

    assert check_tone(insinuation).clean, "tone scan sees no prosecutorial vocabulary"
    assert check_attribution(insinuation, ["rec_00c1a2"], {"rec_00c1a2"}), (
        "attribution gate sees a cited, known record"
    )

    hedging = check_hedging(insinuation)
    assert not hedging.clean, "the hedging scan is still the only one that detects it"
    assert hedging, "and it no longer withholds the passage"
    assert hedging.observations()[0]["matched_terms"] == ["raises questions"]


# ── Reporting ─────────────────────────────────────────────────────────────────


def test_both_families_are_reported_separately():
    result = check_hedging("Clearly the record appears to show a pattern.")
    assert result
    assert not result.clean
    assert result.conclusion_stealing == ("clearly",)
    assert result.insinuation == ("appears to",)
    assert "conclusion stated for the reader" in result.reason()
    assert "implication without a claim" in result.reason()


def test_passing_result_is_truthy_and_explains_itself():
    result = check_hedging(NEUTRAL)
    assert bool(result) is True
    assert result.reason() == "hedging gate: pass"


def test_findings_are_sorted_and_deduplicated():
    result = check_hedging("Obviously, clearly, obviously the filing is late.")
    assert result.conclusion_stealing == ("clearly", "obviously")


# ── Vocabulary hygiene ────────────────────────────────────────────────────────


def test_hedging_vocabulary_is_disjoint_from_the_tone_gate():
    """
    A passage tripping both gates should report two distinct reasons, not one word
    twice. Overlap would make the rejection log misleading about what is wrong.
    """
    hedging_terms = _CONCLUSION_STEALING | _INSINUATION
    tone_terms = _PROSECUTORIAL | _MOTIVE | _LOADED
    assert not (hedging_terms & tone_terms), "term appears in both gates' vocabularies"


def test_hedging_families_are_disjoint_from_each_other():
    assert not (_CONCLUSION_STEALING & _INSINUATION)


def test_no_term_is_a_substring_of_another_in_the_same_family():
    """
    Overlapping phrases double-report: "worth noting" and "it is worth noting" would
    both fire on the same text and list the finding twice.
    """
    for family in (_CONCLUSION_STEALING, _INSINUATION):
        for term in family:
            others = family - {term}
            assert not any(term in other for other in others), (
                f"{term!r} is contained in another term of the same family"
            )


# ── Wiring into publication ───────────────────────────────────────────────────


def _build_with_pattern(pattern: str, *, tone_gate: bool = True):
    """Drive IssueBuilder over one record whose rendered line carries `pattern`."""
    from datetime import datetime, timezone

    from pdx1.models import (
        ConfidenceTier,
        GateResult,
        IntelligenceRecord,
        Outcome,
        Priority,
        SourceType,
    )
    from pdx1.publication.issue_builder import IssueBuilder

    record = IntelligenceRecord(
        record_id="rec_hedging_0001",
        run_id="pdx1_test_run",
        source="ORESTAR",
        source_type=SourceType.ORESTAR,
        pattern=pattern,
        outcome=Outcome.ESCALATE,
        priority=Priority.ELEVATED,
        confidence=0.91,
        tier=ConfidenceTier.HARD_RECORD,
        gates=GateResult(credibility=True, volume=True, velocity=True, novelty=True),
        entity_ids=["pge"],
        signal_id="sig_hedging_0001",
        dedup_hash="hash_hedging_0001",
        published_at=datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc),
    )
    builder = IssueBuilder(run_id="pdx1_test_run", tone_gate=tone_gate)
    return builder.build([record]), builder


def test_issue_builder_publishes_an_insinuating_section_with_an_observation():
    """A section that insinuates now publishes, carrying an audit note."""
    brief, builder = _build_with_pattern(
        "The timing of the contribution raises questions about the vote."
    )
    assert brief is not None, "observation mode -- the section is no longer withheld"
    assert not builder.rejected

    section = brief.sections[0]
    assert len(section.observations) == 1
    observation = section.observations[0]
    assert observation.gate == "hedging_gate"
    assert observation.rule == "observation_only"
    assert observation.severity == "info"
    assert observation.matched_terms == ["raises questions"]

    assert builder.observed and builder.observed[0].title == section.title


def test_issue_builder_publishes_a_neutral_section_with_no_observations():
    """The control: neutral prose publishes and records nothing."""
    brief, builder = _build_with_pattern(
        "Metro Councilor · D2 filed a statement of economic interest naming PGE."
    )
    assert brief is not None
    assert not builder.rejected
    assert brief.sections[0].observations == []
    assert not builder.observed


def test_a_section_can_carry_observations_from_both_scans():
    brief, _ = _build_with_pattern(
        "Clearly the corrupt arrangement raises questions about the vote."
    )
    gates = {o.gate for o in brief.sections[0].observations}
    assert gates == {"tone_gate", "hedging_gate"}


def test_tone_gate_flag_turns_the_scans_off_entirely():
    """
    PDX1_TONE_GATE=false now means "do not annotate", not "do not withhold" -- nothing
    is withheld either way. Citation discipline stays enforced regardless.
    """
    brief, builder = _build_with_pattern(
        "The timing of the contribution raises questions about the vote.",
        tone_gate=False,
    )
    assert brief is not None
    assert not builder.rejected
    assert brief.sections[0].observations == [], "scans skipped, so nothing recorded"
    assert not builder.observed
