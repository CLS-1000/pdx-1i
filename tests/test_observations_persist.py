"""
Audit observations survive the write path.

An observation that only exists in a log line is not an audit trail. These pin that it
reaches both stores -- the append-only JSONL that is ground truth, and the SQLite
query layer -- and that an older database picks up the new columns without a rebuild.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from pdx1.models import (
    Brief,
    BriefSection,
    ConfidenceTier,
    GateResult,
    IntelligenceRecord,
    Observation,
    Outcome,
    Priority,
    SourceType,
)
from pdx1.publication.issue_builder import IssueBuilder
from pdx1.store import DualWriteStore


@pytest.fixture
def store(tmp_path) -> DualWriteStore:
    return DualWriteStore(tmp_path / "signals.jsonl", tmp_path / "pdx1.db")


def _record(pattern: str) -> IntelligenceRecord:
    return IntelligenceRecord(
        record_id="rec_obs_0001",
        run_id="pdx1_obs_run",
        source="PORTLAND_PRESS",
        source_type=SourceType.PORTLAND_PRESS,
        pattern=pattern,
        outcome=Outcome.ESCALATE,
        priority=Priority.ELEVATED,
        confidence=0.7,
        tier=ConfidenceTier.REPORTED,
        gates=GateResult(credibility=True, volume=True, velocity=True, novelty=True),
        entity_ids=["pge"],
        signal_id="sig_obs_0001",
        dedup_hash="hash_obs_0001",
        published_at=datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc),
    )


def _brief_with_observation() -> Brief:
    builder = IssueBuilder(run_id="pdx1_obs_run")
    # The live-run case: a press headline reporting a court outcome.
    brief = builder.build([_record("Jury found the contractor guilty of fraud.")])
    assert brief is not None, "the section must publish, not be withheld"
    return brief


# ── The section carries what was observed ─────────────────────────────────────


def test_a_press_headline_publishes_with_an_observation():
    brief = _brief_with_observation()
    observations = brief.sections[0].observations

    assert observations, "matched vocabulary must be recorded"
    assert observations[0].gate == "tone_gate"
    assert set(observations[0].matched_terms) >= {"fraud", "guilty"}


# ── JSONL: ground truth ───────────────────────────────────────────────────────


def test_observations_reach_the_jsonl(store):
    brief = _brief_with_observation()
    store.write_brief(brief)

    line = store.briefs_path.read_text(encoding="utf-8").strip()
    payload = json.loads(line)

    observations = payload["sections"][0]["observations"]
    assert observations
    assert observations[0]["gate"] == "tone_gate"
    assert observations[0]["rule"] == "observation_only"
    assert observations[0]["severity"] == "info"
    assert "fraud" in observations[0]["matched_terms"]


def test_a_brief_reads_back_with_its_observations(store):
    brief = _brief_with_observation()
    store.write_brief(brief)

    restored = store.latest_brief()
    assert restored is not None
    assert restored.sections[0].observations == brief.sections[0].observations


# ── SQLite: query layer ───────────────────────────────────────────────────────


def test_observations_are_queryable_as_their_own_column(store):
    store.write_brief(_brief_with_observation())

    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT observations, observation_count FROM briefs"
        ).fetchone()

    assert row["observation_count"] == 1
    stored = json.loads(row["observations"])
    assert stored[0]["gate"] == "tone_gate"
    assert stored[0]["section"], "each note names the section it came from"


def test_a_clean_brief_records_no_observations(store):
    builder = IssueBuilder(run_id="pdx1_obs_run")
    brief = builder.build([_record("Metro Councilor · D2 filed a statement of interest.")])
    assert brief is not None
    store.write_brief(brief)

    with sqlite3.connect(store.db_path) as conn:
        row = conn.execute("SELECT observations, observation_count FROM briefs").fetchone()

    assert row[1] == 0
    assert json.loads(row[0]) == []


# ── Migration ─────────────────────────────────────────────────────────────────


def test_a_database_predating_observations_is_migrated(tmp_path):
    """
    `CREATE TABLE IF NOT EXISTS` leaves an existing table alone, so the new columns
    have to be added by hand. A store opened against an older database must not raise.
    """
    db_path = tmp_path / "old.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE briefs (
                brief_id      TEXT PRIMARY KEY,
                run_id        TEXT NOT NULL,
                date          TEXT NOT NULL,
                headline      TEXT NOT NULL,
                confidence    REAL NOT NULL,
                section_count INTEGER NOT NULL,
                produced_at   TEXT NOT NULL,
                payload       TEXT NOT NULL
            )
            """
        )
        conn.commit()

    store = DualWriteStore(tmp_path / "signals.jsonl", db_path)

    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(briefs)")}
    assert {"observations", "observation_count"} <= columns

    store.write_brief(_brief_with_observation())
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT observation_count FROM briefs").fetchone()[0] == 1


def test_migration_is_idempotent(tmp_path):
    """Opening the same database twice must not try to add the columns again."""
    paths = (tmp_path / "signals.jsonl", tmp_path / "pdx1.db")
    DualWriteStore(*paths)
    store = DualWriteStore(*paths)
    store.write_brief(_brief_with_observation())

    with sqlite3.connect(paths[1]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM briefs").fetchone()[0] == 1


# ── Backwards compatibility of the model ──────────────────────────────────────


def test_a_section_serialised_before_observations_existed_still_loads():
    """The field is additive and defaulted, so old ground truth stays readable."""
    section = BriefSection.model_validate(
        {"title": "Elevated", "body": "text", "source_record_ids": ["rec_1"]}
    )
    assert section.observations == []


def test_observation_defaults_match_the_audit_contract():
    observation = Observation(gate="tone_gate")
    assert observation.rule == "observation_only"
    assert observation.severity == "info"
    assert observation.matched_terms == []
