"""
Hedging gate.

The gate exists to catch what tone and attribution structurally cannot: prose that
implies a finding while asserting nothing. Because no claim is made, the attribution
gate has nothing to check -- so without this gate, insinuation is the one way to
characterise a subject and still publish.
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
    assert check_hedging(NEUTRAL)


def test_empty_text_passes():
    assert check_hedging("")


# ── Conclusion-stealing ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "phrase",
    ["clearly", "obviously", "undoubtedly", "of course", "tellingly", "predictably"],
)
def test_conclusion_stealing_is_rejected(phrase):
    result = check_hedging(f"The filing {phrase} shows a pattern of contributions.")
    assert not result
    assert phrase in result.conclusion_stealing
    assert "conclusion stated for the reader" in result.reason()


def test_single_tokens_match_on_word_boundaries():
    """`certainly` must not fire inside `uncertainly`."""
    assert check_hedging("The timeline is uncertainly documented in the record.")


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
def test_insinuation_is_rejected(phrase):
    result = check_hedging(f"The record {phrase} the councilor's vote on the measure.")
    assert not result
    assert phrase in result.insinuation
    assert "implication without a claim" in result.reason()


def test_insinuation_survives_the_other_two_gates():
    """
    The reason this gate exists, stated as a test.

    An insinuating sentence asserts nothing, so it carries no prosecutorial vocabulary
    for the tone gate and makes no claim for the attribution gate to trace. Both pass
    it. Only the hedging gate stops it.
    """
    insinuation = (
        "The timing of the contribution raises questions about the vote that followed. "
        "Source ORESTAR, record rec_00c1a2."
    )

    assert check_tone(insinuation), "tone gate sees no prosecutorial vocabulary"
    assert check_attribution(insinuation, ["rec_00c1a2"], {"rec_00c1a2"}), (
        "attribution gate sees a cited, known record"
    )
    assert not check_hedging(insinuation), "hedging gate is the only one that catches it"


# ── Reporting ─────────────────────────────────────────────────────────────────


def test_both_families_are_reported_separately():
    result = check_hedging("Clearly the record appears to show a pattern.")
    assert not result
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


def test_issue_builder_rejects_an_insinuating_section():
    """A section that only insinuates must not reach a brief."""
    brief, builder = _build_with_pattern(
        "The timing of the contribution raises questions about the vote."
    )
    assert brief is None
    assert builder.rejected
    assert "hedging gate" in builder.rejected[0].reason


def test_issue_builder_publishes_a_neutral_section():
    """The control: the same wiring passes when the prose states a measurement."""
    brief, builder = _build_with_pattern(
        "Metro Councilor · D2 filed a statement of economic interest naming PGE."
    )
    assert brief is not None
    assert not builder.rejected


def test_tone_gate_flag_also_bypasses_hedging():
    """
    PDX1_TONE_GATE=false publishes source language as-is, and hedging is a vocabulary
    gate like tone. Citation discipline stays enforced either way.
    """
    brief, builder = _build_with_pattern(
        "The timing of the contribution raises questions about the vote.",
        tone_gate=False,
    )
    assert brief is not None, "vocabulary gates are bypassed together"
    assert not builder.rejected
