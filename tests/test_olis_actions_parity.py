"""
Drift detector for the copied rule table.

`src/pdx1/sources/olis_actions.py` is a copy of proc_track's mapping, and proc_track is
the only place those rules are validated. This test cannot stop the copy from drifting
-- nothing here can see the upstream table -- but it does stop it drifting *quietly*:
edit RULES without bumping RULES_VERSION and the suite goes red.

Failing this on a deliberate re-sync is the expected outcome. Recompute the digest,
update the constant, and say so in the commit.
"""

from __future__ import annotations

import hashlib

from pdx1.sources import olis_actions


def test_rules_digest_matches_the_recorded_version():
    digest = hashlib.sha256(repr(olis_actions.RULES).encode()).hexdigest()[:12]

    assert digest == olis_actions.RULES_VERSION, (
        "The rule table changed but RULES_VERSION did not. If this was a deliberate "
        f"re-sync from proc_track, set RULES_VERSION = {digest!r}."
    )


def test_helper_agrees_with_the_constant():
    assert olis_actions.rules_digest() == olis_actions.RULES_VERSION


def test_rules_version_is_a_short_hex_digest():
    assert len(olis_actions.RULES_VERSION) == 12
    assert all(c in "0123456789abcdef" for c in olis_actions.RULES_VERSION)


def test_every_rule_compiles_and_is_shaped_right():
    """COMPILED is derived from RULES, so a malformed row fails at import; assert the
    shape anyway so the failure names the rule instead of a traceback in a listcomp."""
    assert len(olis_actions.COMPILED) == len(olis_actions.RULES)
    for rule_id, pattern, state, payload in olis_actions.RULES:
        assert isinstance(rule_id, str) and rule_id
        assert isinstance(pattern, str) and pattern
        assert state is None or isinstance(state, str)
        assert isinstance(payload, dict)


def test_every_emittable_state_is_reachable_from_some_rule():
    """A state in EMITTABLE that no rule can produce is dead configuration."""
    produced = {state for _rid, _pat, state, _pl in olis_actions.RULES if state}

    assert olis_actions.EMITTABLE <= produced, olis_actions.EMITTABLE - produced


def test_chamber_flow_matches_the_upstream_table():
    """
    proc_track repeats four CHAMBER_FLOW keys with identical values; the copy collapses
    them so ruff's F601 gate passes. Deduplication must not have changed the table --
    a dict literal keeps the last value for a repeated key, so this only holds while
    the repeats really are identical.
    """
    assert olis_actions.CHAMBER_FLOW["enacted"] == {"vetoed", "veto_sustained"}
    # Every state a rule can produce must be reachable, or replay halts on it.
    produced = {state for _rid, _pat, state, _pl in olis_actions.RULES if state}
    known = set(olis_actions.CHAMBER_FLOW) | {
        target for targets in olis_actions.CHAMBER_FLOW.values() for target in targets
    }
    assert produced <= known, produced - known
