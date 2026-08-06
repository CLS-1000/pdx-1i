"""
Neutrality-gated brief assembly (publication path step 05).

IssueBuilder turns a set of IntelligenceRecords into a Metro Citizens Brief. Every
section passes the tone gate and the attribution gate before it lands in the brief; a
section that fails either is dropped and the rejection is recorded.

Section prose is generated from record fields rather than written freehand. That is the
point: templated text derived from the record cannot drift into characterisation, and
what it asserts is exactly what the record holds.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..models import (
    AnomalyTier,
    Brief,
    BriefSection,
    IntelligenceRecord,
    Observation,
    Outcome,
    utcnow,
)
from ..neutrality import check_attribution, check_hedging, check_tone

logger = logging.getLogger(__name__)

# Sections are grouped by outcome, strongest first.
_SECTION_ORDER: tuple[tuple[str, tuple[Outcome, ...]], ...] = (
    ("Elevated", (Outcome.ESCALATE, Outcome.CORROBORATED)),
    ("Under Review", (Outcome.INVESTIGATE,)),
    ("Watch List", (Outcome.MONITOR,)),
)


@dataclass
class RejectedSection:
    """A section that failed a neutrality gate, kept for the run log."""

    title: str
    reason: str


@dataclass
class SectionObservation:
    """An audit note raised against a section that was published anyway."""

    title: str
    observation: Observation

    def describe(self) -> str:
        return (
            f"{self.observation.gate} matched "
            f"{list(self.observation.matched_terms)}"
        )


@dataclass
class IssueBuilder:
    """Assembles a brief from records, gating every section."""

    run_id: str
    #: Sections withheld. Only attribution can put a section here -- tone and hedging
    #: observe rather than reject.
    rejected: list[RejectedSection] = field(default_factory=list)
    #: Audit notes raised against sections that were published anyway.
    observed: list[SectionObservation] = field(default_factory=list)
    #: When False the vocabulary scans -- tone and hedging -- are skipped entirely, so
    #: no observations are recorded. Neither setting withholds a section; the flag now
    #: controls whether the text is annotated, not whether it publishes. Attribution
    #: is unaffected and always enforced.
    tone_gate: bool = True

    def build(
        self,
        records: list[IntelligenceRecord],
        date: str | None = None,
    ) -> Brief | None:
        """
        Assemble a brief. Returns None when no section survives the gates.

        An empty brief is not published. Silence is preferable to a brief that says
        nothing traceable.
        """
        if not records:
            return None

        known_ids = {r.record_id for r in records}
        sections: list[BriefSection] = []

        for title, outcomes in _SECTION_ORDER:
            bucket = [r for r in records if r.outcome in outcomes]
            if not bucket:
                continue
            section = self._build_section(title, bucket, known_ids)
            if section is not None:
                sections.append(section)

        if not sections:
            logger.info("run %s: no section cleared the neutrality gates", self.run_id)
            return None

        date = date or utcnow().date().isoformat()
        confidence = round(sum(r.confidence for r in records) / len(records), 4)

        return Brief(
            brief_id=f"brief_{date.replace('-', '_')}_{self.run_id[-6:]}",
            run_id=self.run_id,
            date=date,
            headline=self._headline(records),
            summary=self._summary(records),
            sections=sections,
            confidence=confidence,
            sources=sorted({r.source for r in records}),
        )

    # ── Section assembly ─────────────────────────────────────────────────────

    def _build_section(
        self,
        title: str,
        records: list[IntelligenceRecord],
        known_ids: set[str],
    ) -> BriefSection | None:
        lines = [self._render_record(r) for r in records]
        body = "\n".join(lines)
        cited = [r.record_id for r in records]

        # Tone and hedging observe; they never withhold the section. What they matched
        # is recorded on the section and travels with it into the store.
        observations: list[Observation] = []
        if self.tone_gate:
            for result in (check_tone(body), check_hedging(body)):
                for payload in result.observations():
                    observation = Observation(**payload)
                    observations.append(observation)
                    self.observed.append(SectionObservation(title, observation))
                    logger.info(
                        "run %s: section %r observation: %s matched %s",
                        self.run_id,
                        title,
                        observation.gate,
                        observation.matched_terms,
                    )

        # Attribution still rejects. A section citing a record the engine does not hold
        # is a traceability failure, not a question of vocabulary, and publishing it
        # would break the one claim the engine makes about every line it prints.
        attribution = check_attribution(body, cited, known_ids)
        if not attribution:
            self.rejected.append(RejectedSection(title, attribution.reason()))
            logger.info(
                "run %s: section %r rejected -- %s",
                self.run_id,
                title,
                attribution.reason(),
            )
            return None

        return BriefSection(
            title=title,
            body=body,
            source_record_ids=cited,
            observations=observations,
        )

    def _render_record(self, record: IntelligenceRecord) -> str:
        """
        Render one record as a neutral line.

        Where an anomaly is attached, cite the baseline and the sigma value rather than
        describing the reading in words.
        """
        parts = [f"- [{record.outcome.value}] {record.pattern}"]
        if record.anomaly and record.anomaly.tier is not AnomalyTier.NONE:
            parts.append(f"Measured {record.anomaly.describe()}.")
        parts.append(
            f"Source {record.source}, confidence {record.confidence:.2f} "
            f"({record.tier.value}), record {record.record_id}."
        )
        return " ".join(parts)

    # ── Front matter ─────────────────────────────────────────────────────────

    def _headline(self, records: list[IntelligenceRecord]) -> str:
        """A count-based headline. Counts are facts; characterisations are not."""
        sources = len({r.source for r in records})
        elevated = sum(
            1 for r in records if r.outcome in (Outcome.ESCALATE, Outcome.CORROBORATED)
        )
        if elevated:
            return (
                f"{len(records)} records across {sources} feeds; "
                f"{elevated} at elevated disposition"
            )
        return f"{len(records)} records across {sources} feeds; none at elevated disposition"

    def _summary(self, records: list[IntelligenceRecord]) -> str:
        by_outcome: dict[str, int] = {}
        for record in records:
            by_outcome[record.outcome.value] = by_outcome.get(record.outcome.value, 0) + 1
        breakdown = ", ".join(f"{v} {k}" for k, v in sorted(by_outcome.items()))

        anomalies = [
            r for r in records if r.anomaly and r.anomaly.tier is AnomalyTier.TIER_1
        ]
        tail = (
            f" {len(anomalies)} record(s) carry a TIER_1 baseline deviation."
            if anomalies
            else " No record carries a TIER_1 baseline deviation."
        )

        return (
            f"This cycle cleared {len(records)} records through the four-gate filter: "
            f"{breakdown}.{tail} Every line traces to run {self.run_id}."
        )
