"""
OLIS action-text -> procedural state mapping.

`Measures` gives one row per bill with a `CurrentLocation` field, and that field is
misleading: a vetoed bill reads "Senate - Tabled." Procedural state has to be derived
from the action history instead, which is what this module does.

**This is a copy.** The rules live in and are validated by `proc_track`
(`replay_harness.py`); this file is the extracted mapping only -- no CLI, no fetching,
no reporting. Stdlib imports only, so it can never pull the adapter into a dependency
it does not already have.

`RULES_VERSION` digests the rule table. `tests/test_olis_actions_parity.py` recomputes
it and asserts the match, so editing the copy without bumping the constant fails the
suite. That is a drift *detector*, not a drift *preventer*: it cannot tell you the
upstream table moved. When proc_track's rules are patched, re-copy and re-digest.

Replay provenance (2026-09-07, 11 sessions pulled live from the OData service):
12,641 of 12,642 measures replay to completion across 2019R1, 2021R1, 2023R1, 2025R1,
2026R1, 2024R1, 2022R1, 2020S1, 2020S2, 2020S3 and 2025S1. The one halt is SB579 in
2023R1 -- "Rescission of the subsequent referral denied by Order of the President",
which no rule claims because `[Rr]escind` does not match "Rescission". That is an
upstream gap and is deliberately not patched here; patching the copy would fork the
rules from the only place they are validated.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Optional

@dataclass(frozen=True)
class Emit:
    state: Optional[str]
    payload: dict
    rule_id: str
    span: tuple[int, int]          # character offsets in the source row
    text: str                       # exact matched substring


# (rule_id, pattern, state_or_None, payload)
RULES: list[tuple[str, str, Optional[str], dict]] = [
    # --- introduction -----------------------------------------------------
    ("introduction_first_reading", r"Introduction and first reading",   "introduced", {}),
    ("measure_introduced",         r"measure introduced",               "introduced", {}),
    ("first_reading",              r"First reading",                    "introduced", {}),

    # --- referral ---------------------------------------------------------
    ("referred_to",                r"[Rr]eferred to ([^.]+)",           "committee", {"committee": r"\1"}),
    ("assigned_subcommittee",      r"Assigned to ([^.]+)",              "committee", {"subcommittee": r"\1"}),
    ("returned_full_committee",    r"Returned to Full Committee",       "committee", {"subcommittee": None}),
    ("returned_presidents_desk",   r"returned to President's desk",     "committee", {}),

    # --- committee events -------------------------------------------------
    # combined form is 34x more common than you would guess; must outrank the singles
    ("hearing_and_work_session",   r"Public Hearing and Work Session held", "work_session", {}),
    ("public_hearing_held",        r"Public Hearing held",              "public_hearing", {}),
    ("work_session_held",          r"Work Session held",                "work_session", {}),
    ("meeting_cancelled",          r"(Public Hearing|Work Session|Informational Meeting)[^.]*[Cc]ancelled",
                                                                        None, {"cancelled": True}),
    ("informational_meeting",      r"Informational Meeting held",       None, {}),
    ("event_not_held",              r"(Public Hearing|Work Session|Possible [^.]*) not held", None, {}),
    ("event_scheduled",             r"(Public Hearing|Work Session|Informational Meeting) [Ss]cheduled", None, {}),
    ("event_not_held",              r"(Public Hearing|Work Session|Possible [^.]*) not held", None, {}),
    ("event_scheduled",             r"(Public Hearing|Work Session|Informational Meeting) [Ss]cheduled", None, {}),
    ("event_not_held",              r"(Public Hearing|Work Session|Possible [^.]*) not held", None, {}),
    ("event_scheduled",             r"(Public Hearing|Work Session|Informational Meeting) [Ss]cheduled", None, {}),
    ("event_not_held",              r"(Public Hearing|Work Session|Possible [^.]*) not held", None, {}),
    ("event_scheduled",             r"(Public Hearing|Work Session|Informational Meeting) [Ss]cheduled", None, {}),
    ("in_committee_adjournment",   r"In committee upon adjournment",    "committee", {}),
    ("in_conference_adjournment",   r"In conference committee upon adjournment", "committee", {"conference": True}),
    ("in_conference_adjournment",   r"In conference committee upon adjournment", "committee", {"conference": True}),
    ("in_conference_adjournment",   r"In conference committee upon adjournment", "committee", {"conference": True}),
    ("in_conference_adjournment",   r"In conference committee upon adjournment", "committee", {"conference": True}),

    # --- recommendations --------------------------------------------------
    ("rec_do_pass",                r"Recomme[nd]dation:\s+Do pass",          "committee", {"recommendation": "do_pass"}),
    ("rec_do_adopt",               r"Recomme[nd]dation:\s+Do adopt",         "committee", {"recommendation": "do_adopt"}),
    ("rec_be_adopted",             r"Recomme[nd]dation:\s+Be adopted",       "committee", {"recommendation": "be_adopted"}),
    ("rec_none",                   r"Without recommendation",
                                                                        "committee", {"recommendation": "none"}),
    ("with_amendments",            r"with amendments",                  None, {"amended": True}),
    ("printed_engrossed",          r"printed ([A-Z])-Engrossed",        None, {"engrossment": r"\1"}),
    ("printed_eng_paren",          r"\(Printed ([A-Z])-Eng\.\)",        None, {"engrossment": r"\1"}),

    # --- floor ------------------------------------------------------------
    ("second_reading",             r"Second reading",                   "second_reading", {}),
    ("third_reading",              r"Third reading",                    "third_reading", {}),
    ("read",                       r"\bRead\b(?! as)",                  "third_reading", {}),
    ("final_reading",              r"Final reading",                    "third_reading", {}),
    ("special_order",              r"(Made a?|Motion to make a?) Special Order of Business( on [^.]*)?",
                                                                        None, {"special_order": True}),
    ("taken_from_calendar",        r"Taken from [^.]*Calendar[^.]*",    None, {"recalendared": True}),
    ("placed_on_calendar",         r"[Pp]laced on [^.]*Calendar[^.]*",  None, {"recalendared": True}),
    ("read_special_order",         r"Read as Special Order of Business", "third_reading", {}),
    ("passed",                     r"\bPassed\b",                       "passed", {}),
    ("adopted_cc_report",          r"(Senate|House) adopted Conference Committee Report[^.]*", "passed", {"conference": True}),
    ("repassed",                   r"[Rr]epassed( bill)?",              "passed", {"repassed": True}),
    ("not_concurring_paren",       r"\(Not concurring:[^)]+\)", None, {}),
    ("concurred",                  r"concurred in ([A-Za-z]+) amendments", None, {"concurred": True}),
    ("motion_failed",              r"Motion to [^.]*failed",            None, {"motion_failed": True}),
    ("motion_carried_refer",       r"Motion to r?e?refer to ([^.]+) carried[^.]*", "committee", {"committee": r"\1"}),
    ("motion_postpone",            r"Motion to pos[tp]one[^.]*",         None, {"postponed": True}),
    ("motion_postpone",            r"Motion to pos[tp]one[^.]*",         None, {"postponed": True}),
    ("motion_postpone",            r"Motion to pos[tp]one[^.]*",         None, {"postponed": True}),
    ("motion_postpone",            r"Motion to pos[tp]one[^.]*",         None, {"postponed": True}),
    ("motion_carried_generic",     r"Motion to [^.]*carried[^.]*",      None, {"motion_carried": True}),
    ("failed",                     r"\bFailed\b",                       "failed", {}),
    ("refused_to_concur",          r"(House|Senate) refused to concur[^.]*", "committee", {"concurrence": "refused"}),
    ("adopted",                    r"\bAdopted\b",                      "adopted", {}),
    ("rules_suspended",            r"Rules suspended",                  None, {"rules_suspended": True}),
    ("carried_by",                 r"Carried by ([^.]+)",               None, {"carried_by": r"\1"}),
    ("carried_over",               r"Carried [Oo]ver to [^.]+",         None, {"carried_over": True}),
    ("vote_explanation",           r"Vote explanation[^.]*",  None, {}),
    ("motion_to_table",            r"Motion to lay bill.*on the table", "tabled", {}),
    ("withdrawn_committee",        r"[Ww]ithdrawn from committee",      "committee", {}),

    # --- executive / post-passage ----------------------------------------
    ("president_signed",           r"President signed",                 "signed_by_presiding", {"signer": "president"}),
    ("speaker_signed",             r"Speaker signed",                   "signed_by_presiding", {"signer": "speaker"}),
    ("governor_signed",            r"Governor signed",                  "enacted", {}),
    ("art_v_time_allowed",         r"The time allowed by Article V[^.]*", None, {}),
    ("art_v_time_allowed",         r"The time allowed by Article V[^.]*", None, {}),
    ("line_item_veto",             r"Governor purported to sign with line-item veto", "vetoed", {"veto_type": "line_item"}),
    ("governor_vetoed",            r"Governor vetoed",                  "vetoed", {}),
    ("veto_sustained",             r"Veto sustained[^.]*",              "veto_sustained", {}),
    ("veto_overridden",            r"Veto overridden[^.]*",             "veto_overridden", {}),
    ("repass_notwithstanding",     r"[Mm]otion to repass bill notwithstanding[^.]*veto carried[^.]*",
                                                                        "veto_overridden", {"override": True}),
    ("filed_sos",                  r"Filed with Secretary of State",    None, {"filed_sos": True}),
    ("chapter_number",             r"Chapter (\d+)[^.]*Laws",            None, {"chapter": r"\1"}),
    ("filed_without_signature", r"Filed without Governor.s signature", "enacted", {}),
    ("filed_without_signature", r"Filed without Governor.s signature", "enacted", {}),
    ("filed_without_signature", r"Filed without Governor.s signature", "enacted", {}),
    ("filed_without_signature", r"Filed without Governor.s signature", "enacted", {}),
    ("effective_date",             r"Effective date[^.]*",              None, {}),
    ("effective_91st_day",         r"[Ee]ffective on the \d+\w* day[^.]*", None, {}),
    ("subsequent_referral_denied", r"[Rr]escind[^.]*subsequent referral[^.]*denied[^.]*", None, {}),
    ("referral_rescinded_reref",    r"[Rr]eferral rescinded by order[^.]*", "committee", {}),
    ("subseq_referral_resc_denied", r"[Ss]ubsequent referral rescission denied[^.]*", None, {}),
    ("subsequent_referral_denied", r"[Rr]escind[^.]*subsequent referral[^.]*denied[^.]*", None, {}),
    ("referral_rescinded_reref",    r"[Rr]eferral rescinded by order[^.]*", "committee", {}),
    ("subseq_referral_resc_denied", r"[Ss]ubsequent referral rescission denied[^.]*", None, {}),
    ("subsequent_referral_denied", r"[Rr]escind[^.]*subsequent referral[^.]*denied[^.]*", None, {}),
    ("referral_rescinded_reref",    r"[Rr]eferral rescinded by order[^.]*", "committee", {}),
    ("subseq_referral_resc_denied", r"[Ss]ubsequent referral rescission denied[^.]*", None, {}),
    ("subsequent_referral_denied", r"[Rr]escind[^.]*subsequent referral[^.]*denied[^.]*", None, {}),
    ("referral_rescinded_reref",    r"[Rr]eferral rescinded by order[^.]*", "committee", {}),
    ("subseq_referral_resc_denied", r"[Ss]ubsequent referral rescission denied[^.]*", None, {}),
    ("subsequent_referral_resc",   r"[Ss]ubsequent referral[^.]*rescinded[^.]*", None, {"subsequent_referral": "rescinded"}),
    ("amendments_distributed",     r"\(Amendments distributed\.?\)",     None, {}),
    ("at_desk_adjournment",        r"At ((President's|Speaker's) desk|Desk) upon adjournment", None, {"location": "desk"}),
    ("governors_message_read",     r"Governor's message read[^.]*",     None, {}),
    ("conferees_appointed",        r"[^.]*(appointed|discharged) (as )?(House|Senate) conferee[s]?[^.]*", None, {"conference": True}),
    ("conference_recommendation",  r"Conference Committee Recommendation:[^.]*", "committee", {"conference": True}),
    ("conference_report_dist",     r"Conference Committee Report distributed[^.]*", None, {"conference": True}),
    ("vote_reconsideration",       r"Vote reconsideration (carried|failed)",      None, {"reconsidered": True}),
    ("rereferred_bare",            r"\bRereferred\b",                   "committee", {}),
    ("notice_reconsideration",     r"[^.]*reconsideration[^.]*", None, {}),
    ("consent_change_vote",        r"[^.]*granted unanimous consent to change vote[^.]*", None, {}),
    ("tabled_simple",              r"\bTabled\b",                       "tabled", {}),
    ("veto_message_journal",       r"Governor's veto message entered into Journal", None, {}),
    ("conflict_declared",          r"[^.]*conflict[^.]*of interest[^.]*", None, {}),
    ("conflict_declared_alt",      r"[^.]*declared potential conflict of interest", None, {}),
    ("excused_vote",               r"[^.]*(excused|absent)[^.]*granted unanim[oa]+us consent to[^.]*", None, {}),
    ("vote_changed",               r"[^.]*changed from (aye|nay) to (aye|nay)[^.]*", None, {}),
    ("eng_base_ref",               r"(to )?the ([A-Z])-Eng\.? (bill|resolution|measure)", None, {}),
    ("referred_bare",              r"\bReferred\b(?! to)",              "committee", {}),
    ("special_order_bare",         r"(as )?a? ?Special Order of Business,?", None, {"special_order": True}),
    ("minority_report",            r"Minority Report[^.]*",              None, {"minority_report": True}),
    ("art_v_citation",             r"Art\. V, sec\. \d+\w*, Oregon Constitution", None, {}),
    ("and_be",                     r"\band be\b",                       None, {}),

    # --- organizational boilerplate (no procedural meaning) ---------------
    ("permanent_org_report",       r"Under the provisions of the Report[^.]*", None, {}),
    ("special_rules_report",       r"In com[np]liance with the Report[^.]*",   None, {}),
]


COMPILED = [(rid, re.compile(pat, re.IGNORECASE), st, pl) for rid, pat, st, pl in RULES]


# noqa placement is deliberate: `parse_row` is copied verbatim and trips the C901
# complexity gate at 14. Refactoring it here would fork the matching logic from the only
# place it is validated, which costs more than the lint rule buys. Simplify it upstream
# in proc_track and re-copy if it needs to come down.
def parse_row(action_text: str) -> tuple[list[Emit], list[str]]:  # noqa: C901
    """Span-based, no sentence splitting.

    Legal citations ("Art. V, sec. 15b") and legislator initials ("Smith G.")
    both contain periods, so splitting on them destroys the exact rows that
    matter. Instead: find every match over the FULL string, resolve overlaps by
    longest-match-wins, emit in offset order. Ordering falls out for free and
    each emit carries its character span for provenance.

    Returns (emits, uncovered_text_fragments).
    """
    hits = []
    for rid, rx, state, payload in COMPILED:
        for m in rx.finditer(action_text):
            hits.append((m.start(), m.end(), rid, state, payload, m))

    hits.sort(key=lambda h: (h[0], -(h[1] - h[0])))
    chosen, occupied = [], []
    for h in hits:
        if any(not (h[1] <= s or h[0] >= e) for s, e in occupied):
            continue
        occupied.append((h[0], h[1]))
        chosen.append(h)
    chosen.sort(key=lambda h: h[0])

    emits = []
    for start, end, rid, state, payload, m in chosen:
        resolved = {}
        for k, v in payload.items():
            if isinstance(v, str) and v.startswith("\\1") and m.groups():
                resolved[k] = m.group(1).strip()
            else:
                resolved[k] = v
        emits.append(Emit(state, resolved, rid, (start, end), m.group(0)))

    # whatever no rule claimed, minus punctuation/whitespace
    covered = bytearray(len(action_text))
    for s, e in occupied:
        for i in range(s, e):
            covered[i] = 1
    gaps, cur = [], ""
    for i, ch in enumerate(action_text):
        if covered[i]:
            if cur.strip(" .,;"):
                gaps.append(cur.strip(" .,;"))
            cur = ""
        else:
            cur += ch
    if cur.strip(" .,;"):
        gaps.append(cur.strip(" .,;"))
    return emits, gaps

# --------------------------------------------------------------------------
# schema — chamber-aware WITH a crossover edge (the piece that was missing)
# --------------------------------------------------------------------------

CHAMBER_FLOW = {
    None: {"introduced", "adopted", "third_reading"},
    "introduced": {"committee", "second_reading", "third_reading", "adopted"},
    "committee": {"committee", "public_hearing", "work_session", "second_reading", "third_reading",
                  "passed", "adopted", "failed", "tabled", "signed_by_presiding"},
    "public_hearing": {"committee", "public_hearing", "work_session",
                       "second_reading", "third_reading"},
    "work_session": {"committee", "work_session", "public_hearing",
                     "second_reading", "third_reading"},
    "second_reading": {"third_reading", "committee", "failed", "passed", "signed_by_presiding"},
    "third_reading": {"passed", "adopted", "failed", "committee", "third_reading", "second_reading", "signed_by_presiding"},
    "passed": {"signed_by_presiding", "passed", "committee", "third_reading", "tabled", "second_reading", "failed",
               "adopted", "veto_sustained", "veto_overridden"},
    "adopted": {"signed_by_presiding", "committee", "adopted", "third_reading", "passed"},
    "failed": {"committee", "failed", "second_reading", "third_reading", "passed", "adopted"},
    "signed_by_presiding": {"signed_by_presiding", "enacted", "vetoed", "committee"},
    # proc_track repeats this key four times with an identical value, a copy-paste
    # artifact. Collapsed to one here because ruff F601 is a hard CI gate; the built
    # dict is identical either way, and `test_olis_actions_parity` asserts that.
    "enacted": {"vetoed", "veto_sustained"},
    "vetoed": {"tabled", "veto_sustained", "veto_overridden", "committee", "passed"},
    "tabled": {"veto_sustained", "veto_overridden"},
}
TERMINAL = {"veto_sustained", "veto_overridden"}


class Halt(Exception):
    pass


@dataclass
class Item:
    id: str
    chamber: Optional[str] = None
    # per-chamber position; a measure has a state in EACH chamber, not one global state
    states: dict = field(default_factory=dict)
    payload: dict = field(default_factory=dict)
    history: list = field(default_factory=list)

    @property
    def state(self):
        return self.states.get(self.chamber)


def step(item: Item, to_state: str, chamber: str, occurred_at: str, rule_id: str, payload: dict):
    frm = item.states.get(chamber)
    # crossover: origin chamber passed -> second chamber introduction is legal
    if frm is None and to_state == "introduced":
        pass
    elif frm not in CHAMBER_FLOW:
        raise Halt(f"unknown from_state {frm!r} in {chamber}")
    elif to_state not in CHAMBER_FLOW[frm]:
        raise Halt(f"{chamber}: {frm} -> {to_state} not allowed")
    if frm in TERMINAL:
        raise Halt(f"{chamber}: cannot leave terminal {frm}")
    item.states[chamber] = to_state
    item.chamber = chamber
    item.payload.update(payload)
    item.history.append((occurred_at, chamber, frm, to_state, rule_id))


def replay(measure_id: str, rows: list[dict]):
    item = Item(measure_id)
    uncovered = []
    for r in rows:
        emits, gaps = parse_row(r["ActionText"])
        uncovered.extend(gaps)
        if not emits:
            return item, f"NO RULE MATCHED: {r['ActionText'][:70]}", uncovered
        for e in emits:
            if e.state is None:
                item.payload.update(e.payload)
                continue
            try:
                step(item, e.state, r["Chamber"], r["ActionDate"], e.rule_id, e.payload)
            except Halt as h:
                return item, f"{h}  [rule={e.rule_id} row={r['ActionText'][:60]!r}]", uncovered
    return item, None, uncovered



# ══════════════════════════════════════════════════════════════════════════════
# PDX-1i additions. Everything above this line is copied from proc_track and must
# stay byte-identical to it; everything below is this repo's own and is not part
# of the RULES digest.
# ══════════════════════════════════════════════════════════════════════════════

#: sha256 of `repr(RULES)`, first 12 hex characters. Hardcoded rather than computed so
#: `tests/test_olis_actions_parity.py` has something to compare against -- a digest
#: computed at import would match itself no matter what the table said.
#:
#: Bump this deliberately when re-copying from proc_track. A failure here on a
#: deliberate sync is expected and is the whole point: the copy cannot drift quietly.
RULES_VERSION = "5d6c1b8bf766"

#: States worth a Signal. These are the procedural facts a reader would call news:
#: a measure moved, or it stopped moving.
#:
#: Committee churn is excluded on purpose -- `committee`, `public_hearing` and
#: `work_session` fire many times per measure per session and would drown the gates
#: in referral traffic.
#:
#: `carried_over` is deliberately absent. It parses (rule `carried_over`) but maps to
#: no state, and the 2026R1 pull shows why keeping it that way is right: 94 of its
#: rows match, and most are routine floor-calendar churn ("Carried over to 2-13 by
#: unanimous consent") rather than the end-of-session outcome. The rule cannot tell
#: the two apart, so emitting it would emit calendar churn under a terminal-sounding
#: name. If a rule is ever added that isolates "by virtue of adjournment", it can be
#: added here.
EMITTABLE: frozenset[str] = frozenset(
    {
        "introduced",
        "passed",
        "adopted",
        "failed",
        "enacted",
        "vetoed",
        "veto_sustained",
        "veto_overridden",
        "signed_by_presiding",
        "tabled",
    }
)


@dataclass(frozen=True)
class Transition:
    """One state change, tied to the action row that caused it."""

    action_id: int
    chamber: str
    from_state: Optional[str]
    to_state: str
    action_text: str
    action_date: str
    rule_id: str


def transitions(rows: list[dict]) -> tuple[Item, list[Transition]]:
    """
    Replay one measure's history, returning every state change with its action row.

    This is `replay()` with the row identity kept. `replay()` records transitions in
    `Item.history` but drops the `MeasureHistoryId`, and without it a transition
    cannot be recorded as emitted -- `ActionDate` is not unique within a measure, and
    the ids do not sort in date order, so neither alone identifies a row.

    Unlike `replay()`, this raises `Halt` rather than returning it, so one anomalous
    measure is caught by the caller's `except Halt` and skipped without taking the
    cycle down. `rows` must already be sorted by `(ActionDate, MeasureHistoryId)`.
    """
    item = Item(rows[0]["MeasurePrefix"] + str(rows[0]["MeasureNumber"]) if rows else "")
    found: list[Transition] = []

    for r in rows:
        emits, _gaps = parse_row(r["ActionText"])
        if not emits:
            raise Halt(f"NO RULE MATCHED: {r['ActionText'][:70]}")
        for e in emits:
            if e.state is None:
                item.payload.update(e.payload)
                continue
            before = item.states.get(r["Chamber"])
            # step() raises Halt on an illegal edge; let it reach the caller.
            step(item, e.state, r["Chamber"], r["ActionDate"], e.rule_id, e.payload)
            found.append(
                Transition(
                    action_id=int(r["MeasureHistoryId"]),
                    chamber=r["Chamber"],
                    from_state=before,
                    to_state=e.state,
                    action_text=r["ActionText"],
                    action_date=r["ActionDate"],
                    rule_id=e.rule_id,
                )
            )
    return item, found


def rules_digest() -> str:
    """Recompute the digest of the live rule table. Used by the parity test."""
    return hashlib.sha256(repr(RULES).encode()).hexdigest()[:12]
