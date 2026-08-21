"""
Stage-by-stage walkthrough of one cycle, printed as it happens.

Where `pdx1.pipeline` prints a summary, this shows the work: what each adapter returned,
which gate dropped what, how entities resolved, and what the neutrality gates did to the
brief. Useful for seeing why a given record did or did not survive.

    python -m pdx1.demos.walkthrough
"""

from __future__ import annotations

import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from ..config import Settings, SourceMode
from ..gates import FourGateFilter, composite_score
from ..graph import ALIASES, NODES
from ..pipeline import FIXTURE_DIR, default_adapters, parse_signal, run_cycle
from ..resolver import EntityResolver
from ..store import DualWriteStore


def rule(title: str) -> None:
    print(f"\n{title}")
    print("-" * len(title))


def main() -> int:
    # The walkthrough is a fixture replay by construction -- it narrates a known
    # cycle -- so it declares that mode itself rather than inheriting whatever the
    # environment is set to. Everything else (gate thresholds especially) still comes
    # from the environment, so the walkthrough shows your tuning.
    settings = replace(Settings.from_env(), source_mode=SourceMode.FIXTURE)
    adapters = default_adapters(settings, FIXTURE_DIR)
    resolver = EntityResolver(NODES, ALIASES)

    rule("01 HARVEST")
    signals = []
    for adapter in adapters:
        result = adapter.safe_fetch()
        signals.extend(result.signals)
        status = "ok" if result.ok else f"FAILED {result.errors}"
        print(f"  {adapter.name:16} {len(result):>2} signal(s)  {status}")

    if not signals:
        print("no signals harvested")
        return 1

    now = max(s.published_at for s in signals)
    print(f"\n  anchoring the cycle at {now.isoformat()} (newest signal)")

    rule("02 PARSE")
    parsed_signals = [parse_signal(s, resolver) for s in signals]
    for parsed in parsed_signals[:4]:
        entities = ", ".join(parsed.keywords) or "-"
        print(f"  {parsed.signal.signal_id}  {parsed.word_count:>3}w  entities: {entities}")
    print(f"  ... {len(parsed_signals)} parsed")

    rule("03 SCORE -- four gates")
    gate_filter = FourGateFilter(settings.gates)
    survivors = 0
    for parsed in parsed_signals:
        gates = gate_filter.evaluate(parsed, now)
        gate_filter.register(parsed)
        marks = "".join(
            "+" if getattr(gates, g) else "-"
            for g in ("credibility", "volume", "velocity", "novelty")
        )
        if gates.passed:
            survivors += 1
            score = composite_score(parsed, gates, settings.gates, now)
            print(f"  {marks}  PASS  score={score:.3f}  {parsed.signal.source}")
        else:
            why = ", ".join(gates.detail[g] for g in gates.failed_gates)
            print(f"  {marks}  DROP  {parsed.signal.source}: {why}")
    print(f"\n  {survivors} of {len(parsed_signals)} cleared all four gates")

    rule("04-07 INVESTIGATE / VERIFY / ANALYZE / STORE")
    with tempfile.TemporaryDirectory() as tmp:
        store = DualWriteStore(Path(tmp) / "demo.jsonl", Path(tmp) / "demo.db")
        result = run_cycle(settings=settings, adapters=adapters, now=now, store=store)

        for record in result.records:
            anomaly = f"  {record.anomaly.tier.value}" if record.anomaly else ""
            print(
                f"  {record.record_id}  {record.outcome.value:12} "
                f"{record.priority.value:9} conf={record.confidence:.3f} "
                f"{record.tier.value}{anomaly}"
            )

        print(f"\n  written: {result.written}")
        print(f"  jsonl {store.jsonl_count()} lines / sqlite {store.count()} rows")

        rule("PUBLICATION")
        if result.brief is None:
            print("  no brief this cycle")
        else:
            brief = result.brief
            print(f"  {brief.brief_id}  confidence {brief.confidence:.2f}")
            print(f"  {brief.headline}\n")
            print(f"  {brief.summary}\n")
            for section in brief.sections:
                print(f"  [{section.title}] {len(section.source_record_ids)} record(s) cited")
        for err in result.errors:
            print(f"  [warn] {err}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
