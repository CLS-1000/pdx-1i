"""
Pipeline orchestration -- the seven stages, end to end.

    01 Harvest      adapters pull raw payloads
    02 Parse        clean text, extract registry entities
    03 Score        four-gate filter + composite score
    04 Investigate  generate a hypothesis for surviving opportunities
    05 Verify       measure against the rolling baseline
    06 Analyze      assign outcome, priority and confidence tier
    07 Store        dual-write JSONL + SQLite, then assemble a brief if triggered

Each stage is independently fault-tolerant. A failing adapter is recorded and skipped;
the cycle continues and the write still happens.

Run it:

    python -m pdx1.pipeline
    python -m pdx1.pipeline --as-of 2026-05-28T12:00:00+00:00
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .anomaly import BaselineRegistry
from .config import Settings
from .gates import FourGateFilter, composite_score
from .graph import ALIASES, NODES
from .models import (
    AnomalyTier,
    Brief,
    ConfidenceTier,
    IntelligenceRecord,
    Investigation,
    Opportunity,
    Outcome,
    ParsedSignal,
    PipelineRunSummary,
    Priority,
    Signal,
    SourceType,
    content_hash,
    utcnow,
)
from .publication import IssueBuilder
from .resolver import EntityResolver
from .sources import (
    FetchResult,
    OlisAdapter,
    OrestarAdapter,
    PortlandPressAdapter,
    SeiAdapter,
    SourceAdapter,
    WaPdcAdapter,
)
from .store import DualWriteStore
from .trigger import TriggerState
from .watch import WATCH_TARGETS, WatchAdapter

logger = logging.getLogger(__name__)

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures"

#: Filed public records establish a fact directly; press establishes that something was
#: reported. That distinction is what ConfidenceTier encodes.
_TIER_BY_SOURCE: dict[SourceType, ConfidenceTier] = {
    SourceType.ORESTAR: ConfidenceTier.HARD_RECORD,
    SourceType.OLIS: ConfidenceTier.HARD_RECORD,
    SourceType.SEI: ConfidenceTier.HARD_RECORD,
    SourceType.WA_PDC: ConfidenceTier.HARD_RECORD,
    SourceType.PORTLAND_PRESS: ConfidenceTier.REPORTED,
    SourceType.WATCH: ConfidenceTier.REPORTED,
}

_WS = re.compile(r"\s+")


@dataclass
class CycleResult:
    """What one cycle did. Returned by `run_cycle` and summarised on the CLI."""

    run_id: str
    harvested: int = 0
    parsed: int = 0
    opportunities: int = 0
    records: list[IntelligenceRecord] = field(default_factory=list)
    written: int = 0
    brief: Brief | None = None
    dropped: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def stored(self) -> int:
        return self.written


def default_adapters(settings: Settings, fixture_dir: Path | None = None) -> list[SourceAdapter]:
    """
    The PDX-1i feeds.

    Fixture mode (default, `settings.live_fetch=False`): adapters read checked-in
    payloads so a cycle is reproducible and CI needs no network.

    Live mode (`PDX1_LIVE=true`): adapters fetch from their registered `feed_url` and
    the watch targets are included. Requires the `live` extra.
    """
    fx = fixture_dir or FIXTURE_DIR
    t = settings.timeouts

    if settings.live_fetch:
        # No fixture paths — adapters fetch from their registered feed_url.
        adapters: list[SourceAdapter] = [
            OrestarAdapter(timeout=t.orestar, live=True),
            OlisAdapter(timeout=t.olis, live=True),
            SeiAdapter(timeout=t.sei, live=True),
            WaPdcAdapter(timeout=t.wa_pdc, live=True),
            PortlandPressAdapter(timeout=30, live=True),
        ]
        for target in WATCH_TARGETS:
            adapters.append(WatchAdapter(target, timeout=60, live=True))
        return adapters

    return [
        OrestarAdapter(fixture_path=fx / "orestar.json", timeout=t.orestar),
        OlisAdapter(fixture_path=fx / "olis.json", timeout=t.olis),
        SeiAdapter(fixture_path=fx / "sei.json", timeout=t.sei),
        WaPdcAdapter(fixture_path=fx / "wa_pdc.json", timeout=t.wa_pdc),
        PortlandPressAdapter(fixture_path=fx / "portland_press.xml", timeout=30),
    ]


def make_run_id(now: datetime) -> str:
    return f"pdx1_{now:%Y_%m%d_%H%M%S}"


# ── Stage 01: Harvest ────────────────────────────────────────────────────────


def harvest(adapter: object) -> FetchResult:
    """
    Pull signals from one adapter, never raising.

    Accepts both adapter shapes: a `SourceAdapter` subclass, whose `safe_fetch` already
    converts failures into errors on the result, and any duck-typed object exposing
    `fetch() -> Sequence[Signal]`. The second form keeps lightweight adapters -- tests,
    one-off scripts -- usable without subclassing.
    """
    if isinstance(adapter, SourceAdapter):
        return adapter.safe_fetch()

    name = getattr(adapter, "name", type(adapter).__name__)
    try:
        return FetchResult(source=name, signals=list(adapter.fetch()))
    except Exception as exc:  # noqa: BLE001 - no adapter may halt a cycle
        logger.warning("adapter %s failed: %s", name, exc)
        return FetchResult(source=name, errors=[f"{type(exc).__name__}: {exc}"])


# ── Stage 02: Parse ──────────────────────────────────────────────────────────


def parse_signal(signal: Signal, resolver: EntityResolver) -> ParsedSignal:
    """Normalize whitespace and pull registry entities out of the text."""
    clean = _WS.sub(" ", signal.text).strip()
    keywords = [r.node_id for r in resolver.extract(clean)]
    return ParsedSignal(signal=signal, clean_text=clean, keywords=keywords)


# ── Stage 04: Investigate ────────────────────────────────────────────────────


def generate_investigation(opportunity: Opportunity) -> Investigation:
    """
    Build a follow-up for an opportunity.

    The hypothesis is phrased as a question about structure and timing. It is never an
    assertion, because at this stage the engine has one record and no corroboration.
    """
    entities = opportunity.parsed.keywords
    subject = ", ".join(entities) if entities else "no registry entity resolved"

    return Investigation(
        opportunity=opportunity,
        hypothesis=(
            f"Does the {opportunity.signal.source} record intersect with other "
            f"records touching {subject} within the same window?"
        ),
        queries=[
            f"records citing {e} within 90 days" for e in entities[:3]
        ]
        or ["records from the same source within 90 days"],
        analyst_leads=[
            f"Compare against prior {opportunity.signal.source} filings for the same body",
        ],
    )


# ── Stage 06: Analyze ────────────────────────────────────────────────────────


def classify_outcome(score: float, tier: AnomalyTier, entity_count: int) -> Outcome:
    """
    Assign a disposition.

    A TIER_1 baseline deviation escalates on its own. Otherwise the composite score
    decides, with a resolved entity nudging a record up from ARCHIVE -- a record tied to
    a tracked body is worth keeping in view even when it scores low.
    """
    if tier is AnomalyTier.TIER_1:
        return Outcome.ESCALATE
    if score >= 0.75:
        return Outcome.CORROBORATED if entity_count >= 2 else Outcome.INVESTIGATE
    if score >= 0.55:
        return Outcome.INVESTIGATE
    if score >= 0.35 or entity_count:
        return Outcome.MONITOR
    return Outcome.ARCHIVE


def assign_priority(outcome: Outcome) -> Priority:
    if outcome in (Outcome.ESCALATE, Outcome.CORROBORATED):
        return Priority.ELEVATED
    if outcome is Outcome.INVESTIGATE:
        return Priority.STANDARD
    return Priority.MONITOR


def summarize(parsed: ParsedSignal, limit: int = 240) -> str:
    """First sentence or so of the cleaned text, as the record's pattern line."""
    text = parsed.clean_text
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return f"{cut}..."


# ── The cycle ────────────────────────────────────────────────────────────────


def run_cycle(
    settings: Settings | None = None,
    adapters: list[SourceAdapter] | None = None,
    now: datetime | None = None,
    store: DualWriteStore | None = None,
    trigger: TriggerState | None = None,
) -> CycleResult:
    """
    Run one full intelligence cycle.

    `now` anchors the velocity gate. It defaults to the newest harvested signal rather
    than wall-clock time, because the adapters replay fixed fixtures: anchoring a replay
    to real time would drop every record on velocity as the fixtures age. Live adapters
    should pass the real clock.
    """
    settings = settings or Settings.from_env()
    adapters = adapters if adapters is not None else default_adapters(settings)
    store = store or DualWriteStore(
        settings.store_path, settings.db_path, settings.briefs_path
    )

    resolver = EntityResolver(NODES, ALIASES)
    baselines = BaselineRegistry(settings.baseline_window_days)
    trigger = trigger or TriggerState(
        weight_threshold=settings.trigger_weight_threshold,
        floor_days=settings.trigger_floor_days,
        publish_tier=settings.publish_anomaly_tier,
    )

    # ── 01 Harvest ──
    signals: list[Signal] = []
    errors: list[str] = []
    for adapter in adapters:
        result = harvest(adapter)
        signals.extend(result.signals)
        errors.extend(f"{result.source}: {e}" for e in result.errors)
        logger.info(
            "harvest %s: %d signal(s), ok=%s", result.source, len(result), result.ok
        )

    if now is None:
        now = max((s.published_at for s in signals), default=datetime.now(timezone.utc))

    run_id = make_run_id(now)
    result = CycleResult(run_id=run_id, harvested=len(signals), errors=errors)

    # ── 02 Parse ──
    parsed_signals = [parse_signal(s, resolver) for s in signals]
    result.parsed = len(parsed_signals)

    # ── 03 Score ──
    gate_filter = FourGateFilter(settings.gates, seen_hashes=store.known_signal_hashes())
    opportunities: list[Opportunity] = []
    dropped: dict[str, int] = {}

    for parsed in parsed_signals:
        gates = gate_filter.evaluate(parsed, now)
        gate_filter.register(parsed)
        if not gates.passed:
            for gate in gates.failed_gates:
                dropped[gate] = dropped.get(gate, 0) + 1
            continue
        opportunities.append(
            Opportunity(
                parsed=parsed,
                gates=gates,
                score=composite_score(parsed, gates, settings.gates, now),
            )
        )

    result.opportunities = len(opportunities)
    result.dropped = dropped

    # ── 04 Investigate / 05 Verify / 06 Analyze ──
    records: list[IntelligenceRecord] = []
    for opportunity in opportunities:
        generate_investigation(opportunity)

        # Verify: measure the opportunity's score against its source's baseline.
        reading = baselines.observe(
            opportunity.signal.source, opportunity.score, opportunity.signal.published_at
        )

        entity_ids = opportunity.parsed.keywords
        outcome = classify_outcome(opportunity.score, reading.tier, len(entity_ids))
        pattern = summarize(opportunity.parsed)

        record = IntelligenceRecord(
            record_id=f"rec_{content_hash(run_id, opportunity.signal.signal_id)}",
            run_id=run_id,
            source=opportunity.signal.source,
            source_type=opportunity.signal.source_type,
            pattern=pattern,
            outcome=outcome,
            priority=assign_priority(outcome),
            confidence=opportunity.score,
            tier=_TIER_BY_SOURCE.get(
                opportunity.signal.source_type, ConfidenceTier.INFERRED
            ),
            gates=opportunity.gates,
            anomaly=reading if reading.tier is not AnomalyTier.NONE else None,
            entity_ids=entity_ids,
            signal_id=opportunity.signal.signal_id,
            dedup_hash=opportunity.signal.dedup_hash,
            url=opportunity.signal.url,
            published_at=opportunity.signal.published_at,
        )
        records.append(record)

        trigger.add_weight(opportunity.score)
        trigger.note_anomaly(reading.tier, record.record_id)

    result.records = records

    # ── 07 Store ──
    result.written = store.write(records)

    decision = trigger.evaluate(now)
    if decision.should_publish and records:
        builder = IssueBuilder(run_id=run_id, tone_gate=settings.tone_gate)
        result.brief = builder.build(records, date=now.date().isoformat())
        if result.brief is not None:
            # Persist before marking published. A brief that was assembled but not
            # stored would be unrecoverable -- the novelty gate drops these signals on
            # the next cycle, so nothing would regenerate it.
            store.write_brief(result.brief)
            trigger.mark_published(now)
        for rejection in builder.rejected:
            errors.append(f"section {rejection.title!r} dropped: {rejection.reason}")

    return result


# ── Object-oriented facade ───────────────────────────────────────────────────


class Pipeline:
    """
    Adapter-holding facade over `run_cycle`.

    `collect` stops after harvest, for callers that only want raw signals. `run` drives
    a full cycle and reports a coarse summary; use `run_cycle` directly when you need
    the records, the gate drops, or the assembled brief.
    """

    def __init__(
        self,
        adapters: list[SourceAdapter] | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.adapters: list[SourceAdapter] = list(adapters or [])
        self.settings = settings or Settings.from_env()

    def add_adapter(self, adapter: SourceAdapter) -> None:
        self.adapters.append(adapter)

    def collect(self) -> list[Signal]:
        """Harvest every adapter. A failing adapter is skipped, not raised."""
        signals: list[Signal] = []
        for adapter in self.adapters:
            signals.extend(harvest(adapter).signals)
        return signals

    def run(self, store: DualWriteStore | None = None) -> PipelineRunSummary:
        """Run a full cycle and summarise it."""
        started_at = utcnow()
        result = run_cycle(
            settings=self.settings, adapters=self.adapters, store=store
        )
        return PipelineRunSummary(
            started_at=started_at,
            finished_at=utcnow(),
            sources_processed=len(self.adapters),
            signals_collected=result.harvested,
        )


# ── CLI ──────────────────────────────────────────────────────────────────────


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m pdx1.pipeline",
        description="Run one PDX-1i intelligence cycle.",
    )
    parser.add_argument(
        "--as-of",
        dest="as_of",
        default=None,
        help=(
            "ISO timestamp anchoring the velocity gate. Defaults to the newest "
            "harvested signal, which is what makes a fixture replay deterministic."
        ),
    )
    parser.add_argument(
        "--fixtures",
        dest="fixtures",
        default=None,
        help="Directory of source fixtures (default: tests/fixtures).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = Settings.from_env()

    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(levelname)s %(name)s :: %(message)s",
    )

    now = None
    if args.as_of:
        now = datetime.fromisoformat(args.as_of)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

    adapters = default_adapters(
        settings, Path(args.fixtures) if args.fixtures else None
    )
    result = run_cycle(settings=settings, adapters=adapters, now=now)

    print(f"run {result.run_id}")
    print(f"  harvested     {result.harvested}")
    print(f"  parsed        {result.parsed}")
    print(f"  opportunities {result.opportunities}")
    if result.dropped:
        detail = ", ".join(f"{k}={v}" for k, v in sorted(result.dropped.items()))
        print(f"  dropped by    {detail}")
    print(f"  written       {result.written}")
    print(f"  store         {settings.store_path} + {settings.db_path}")

    if result.brief:
        print(f"  brief         {result.brief.brief_id} ({len(result.brief.sections)} sections)")
        print(f"                {result.brief.headline}")
    else:
        print("  brief         not triggered")

    for err in result.errors:
        print(f"  [warn] {err}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
