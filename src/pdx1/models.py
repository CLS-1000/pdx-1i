"""
Core data models for the PDX-1i pipeline.

The chain follows the SPEC-1 seven-stage pipeline:

    Signal -> ParsedSignal -> Opportunity -> Investigation -> IntelligenceRecord

A Signal is raw input from a source adapter. Parsing cleans it and extracts keywords.
Scoring runs the four gates; only signals that clear all four become Opportunities.
Analysis attaches an outcome, a confidence tier and any anomaly reading, producing the
IntelligenceRecord that gets written to the store.

Every record carries the run_id of the cycle that produced it. That traceability is a
publication requirement, not a convenience: no claim reaches a reader without a path back
to the run and the source record that generated it.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ── Enumerations ─────────────────────────────────────────────────────────────


class SourceType(str, Enum):
    """Origin feed for a signal."""

    ORESTAR = "ORESTAR"
    OLIS = "OLIS"
    SEI = "SEI"
    WA_PDC = "WA_PDC"
    PORTLAND_PRESS = "PORTLAND_PRESS"


class ConfidenceTier(str, Enum):
    """
    How directly a record is grounded in evidence.

    HARD_RECORD  a filed public record states it (a contribution, a bill, an SEI entry)
    REPORTED     a published source reports it
    INFERRED     the engine derived it by correlating records
    """

    HARD_RECORD = "HARD_RECORD"
    REPORTED = "REPORTED"
    INFERRED = "INFERRED"


class Outcome(str, Enum):
    """Disposition assigned during analysis."""

    CORROBORATED = "CORROBORATED"
    ESCALATE = "ESCALATE"
    INVESTIGATE = "INVESTIGATE"
    MONITOR = "MONITOR"
    ARCHIVE = "ARCHIVE"


class Priority(str, Enum):
    """Analyst-facing priority band."""

    ELEVATED = "ELEVATED"
    STANDARD = "STANDARD"
    MONITOR = "MONITOR"


class AnomalyTier(str, Enum):
    """
    Sigma band of an observation against its rolling baseline.

    TIER_1 (>= 3 sigma) is the publish-eligible band.
    """

    TIER_1 = "TIER_1"
    TIER_2 = "TIER_2"
    TIER_3 = "TIER_3"
    NONE = "NONE"


class NodeGroup(str, Enum):
    """Political-web node taxonomy."""

    JURISDICTION = "J"
    OFFICIAL = "O"
    ENTITY = "E"


class TieKind(str, Enum):
    """Political-web edge taxonomy."""

    SEAT = "seat"
    TIE = "tie"
    REGULATES = "regulates"
    OPERATES = "operates"
    DISCLOSURE = "disclosure"


# ── Helpers ──────────────────────────────────────────────────────────────────


def utcnow() -> datetime:
    """Timezone-aware UTC now. Used as a default factory so tests can freeze it."""
    return datetime.now(timezone.utc)


def content_hash(*parts: str) -> str:
    """
    Stable content digest used for signal IDs and novelty dedup.

    Not a security primitive -- it identifies duplicate source content, so a fast
    digest truncated to 16 hex chars is sufficient and keeps IDs readable.
    """
    joined = "\x1f".join(p.strip().lower() for p in parts if p)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


# ── Pipeline models ──────────────────────────────────────────────────────────


class Signal(BaseModel):
    """Raw item emitted by a source adapter, before parsing or scoring."""

    model_config = ConfigDict(frozen=True)

    signal_id: str = ""
    source: str
    source_type: SourceType
    text: str
    #: Optional headline. Adapters that have a natural title (press items) set it;
    #: publication falls back to the opening of `text` when it is absent.
    title: str | None = None
    url: str | None = None
    author: str | None = None
    published_at: datetime
    credibility: float = Field(default=0.5, ge=0.0, le=1.0)

    @property
    def display_title(self) -> str:
        """Headline for rendering -- the title if set, otherwise the opening of the text."""
        if self.title:
            return self.title
        head = self.text.strip()
        return head if len(head) <= 80 else head[:80].rsplit(" ", 1)[0] + "..."

    @field_validator("published_at")
    @classmethod
    def _require_tzaware(cls, v: datetime) -> datetime:
        """Naive datetimes silently break velocity math, so reject them at the door."""
        if v.tzinfo is None:
            raise ValueError("published_at must be timezone-aware")
        return v

    def model_post_init(self, _context: object) -> None:
        if not self.signal_id:
            digest = content_hash(self.source, self.url or "", self.text)
            object.__setattr__(self, "signal_id", f"sig_{digest}")

    @property
    def dedup_hash(self) -> str:
        """Digest the novelty gate compares against. Text-only, so a re-post of the
        same content from a different URL is still recognised as a duplicate."""
        return content_hash(self.text)


class ParsedSignal(BaseModel):
    """A Signal with cleaned text and extracted keywords."""

    signal: Signal
    clean_text: str
    keywords: list[str] = Field(default_factory=list)

    @property
    def word_count(self) -> int:
        return len(self.clean_text.split())


class GateResult(BaseModel):
    """
    Outcome of the four-gate filter.

    Each gate is recorded individually because the analyst surface expands a record to
    show which gates it cleared -- a signal that fails only novelty reads very differently
    from one that fails only credibility.
    """

    credibility: bool
    volume: bool
    velocity: bool
    novelty: bool
    detail: dict[str, str] = Field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """All four or nothing. There is no partial credit."""
        return self.credibility and self.volume and self.velocity and self.novelty

    @property
    def failed_gates(self) -> list[str]:
        return [
            name
            for name in ("credibility", "volume", "velocity", "novelty")
            if not getattr(self, name)
        ]


class Opportunity(BaseModel):
    """A parsed signal that cleared all four gates."""

    parsed: ParsedSignal
    gates: GateResult
    score: float = Field(ge=0.0, le=1.0)

    @property
    def signal(self) -> Signal:
        return self.parsed.signal


class Investigation(BaseModel):
    """
    Generated follow-up for an opportunity.

    The hypothesis is phrased as a question about structure and timing. It is not an
    allegation, and the verifier may reject it.
    """

    opportunity: Opportunity
    hypothesis: str
    queries: list[str] = Field(default_factory=list)
    analyst_leads: list[str] = Field(default_factory=list)


class AnomalyReading(BaseModel):
    """
    An observation measured against its rolling baseline.

    Carries the baseline it was measured against, not just a verdict: published copy
    cites the baseline and the sigma deviation rather than an adjective.
    """

    observed: float
    baseline_mean: float
    baseline_stddev: float
    sigma: float
    tier: AnomalyTier
    window_days: int
    sample_size: int

    def describe(self) -> str:
        """Neutral one-line rendering for publication."""
        return (
            f"{self.observed:.2f} against a {self.window_days}-day baseline of "
            f"{self.baseline_mean:.2f} (sd {self.baseline_stddev:.2f}, n={self.sample_size}) "
            f"-- {self.sigma:.1f} sigma"
        )


class IntelligenceRecord(BaseModel):
    """Final analyzed record. This is what gets written to JSONL and SQLite."""

    record_id: str
    run_id: str
    source: str
    source_type: SourceType
    pattern: str
    outcome: Outcome
    priority: Priority
    confidence: float = Field(ge=0.0, le=1.0)
    tier: ConfidenceTier
    gates: GateResult
    anomaly: AnomalyReading | None = None
    entity_ids: list[str] = Field(default_factory=list)
    signal_id: str
    #: Content hash of the originating signal's text. Persisted so the novelty gate can
    #: be seeded from the store on the next cycle -- `pattern` is a truncated summary
    #: and hashing it would not match what the gate compares against.
    dedup_hash: str = ""
    url: str | None = None
    published_at: datetime
    created_at: datetime = Field(default_factory=utcnow)


# ── Political web ────────────────────────────────────────────────────────────


class Node(BaseModel):
    """
    A jurisdiction, an official seat, or a monitored entity.

    Officials are role-based seats ("Metro Councilor - D2"), never named individuals.
    The module exists to make structure legible, not to characterise people.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    label: str
    group: NodeGroup
    weight: float = Field(default=0.5, ge=0.0, le=1.0)
    flag: str | None = None


class Tie(BaseModel):
    """A relationship between two nodes."""

    model_config = ConfigDict(frozen=True)

    source: str
    target: str
    kind: TieKind
    flagged: bool = False


class Seat(BaseModel):
    """An elected seat within a jurisdiction, for the district roster."""

    model_config = ConfigDict(frozen=True)

    district: str
    role: str
    status: str = "seated"


class Jurisdiction(BaseModel):
    """A governing body and its seats."""

    model_config = ConfigDict(frozen=True)

    name: str
    state: str
    zone: str
    seats: tuple[Seat, ...] = ()


# ── Publication ──────────────────────────────────────────────────────────────


class BriefSection(BaseModel):
    """One section of an assembled brief, with the records backing it."""

    title: str
    body: str
    source_record_ids: list[str] = Field(default_factory=list)


class Brief(BaseModel):
    """
    An assembled Metro Citizens Brief.

    Every section has cleared the tone and attribution gates before it lands here.
    """

    brief_id: str
    run_id: str
    date: str
    headline: str
    summary: str
    sections: list[BriefSection] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    sources: list[str] = Field(default_factory=list)
    produced_at: datetime = Field(default_factory=utcnow)


# ── Run reporting ────────────────────────────────────────────────────────────


class PipelineRunSummary(BaseModel):
    """
    Coarse summary of one pipeline run.

    Reports what a run touched without carrying the records themselves -- suitable for
    a status endpoint or a log line. `CycleResult` in pipeline.py is the detailed
    counterpart used inside the engine.
    """

    started_at: datetime
    finished_at: datetime
    sources_processed: int
    signals_collected: int

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()
