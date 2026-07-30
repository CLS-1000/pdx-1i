"""
Public API surface: Pipeline, BriefPublisher, WatchTarget.

Covers the top-level contract other code imports from `pdx1` -- the object-oriented
facade over the pipeline, the brief renderer, and the watch-target record.

Signals here are built the way the real model requires: `source_type`, `text`, and a
timezone-aware `published_at`. Those fields are load-bearing -- the volume gate counts
words in `text` and the velocity gate ages `published_at` -- so a Signal without them
could not be scored.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pdx1 import ConfidenceTier, Pipeline, PipelineRunSummary, Signal, SourceType
from pdx1.config import Settings
from pdx1.publication import BriefPublisher
from pdx1.store import DualWriteStore
from pdx1.watch import WatchTarget

PUBLISHED = datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc)


def make_signal(signal_id: str, title: str) -> Signal:
    return Signal(
        signal_id=signal_id,
        source="dummy",
        source_type=SourceType.ORESTAR,
        title=title,
        text=f"{title}. Body text for the {title} record.",
        published_at=PUBLISHED,
    )


class DummyAdapter:
    """A duck-typed adapter -- no subclassing, just `name` and `fetch`."""

    name = "dummy"

    def fetch(self) -> list[Signal]:
        return [make_signal("sig-1", "Signal One")]


class BrokenAdapter:
    name = "broken"

    def fetch(self) -> list[Signal]:
        raise RuntimeError("upstream is down")


# ── Pipeline facade ──────────────────────────────────────────────────────────


def test_pipeline_collect_and_run_summary(tmp_path):
    settings = Settings(store_path=tmp_path / "s.jsonl", db_path=tmp_path / "p.db")
    pipeline = Pipeline([DummyAdapter()], settings=settings)

    signals = pipeline.collect()
    summary = pipeline.run(store=DualWriteStore(settings.store_path, settings.db_path))

    assert len(signals) == 1
    assert signals[0].title == "Signal One"
    assert summary.sources_processed == 1
    assert summary.signals_collected == 1
    assert summary.finished_at >= summary.started_at
    assert isinstance(summary, PipelineRunSummary)


def test_pipeline_accepts_adapters_after_construction():
    pipeline = Pipeline()
    assert pipeline.collect() == []

    pipeline.add_adapter(DummyAdapter())
    assert len(pipeline.collect()) == 1


def test_a_broken_duck_typed_adapter_does_not_halt_collection():
    """Fault tolerance covers duck-typed adapters too, not just the base class."""
    pipeline = Pipeline([BrokenAdapter(), DummyAdapter()])
    signals = pipeline.collect()

    assert len(signals) == 1
    assert signals[0].signal_id == "sig-1"


def test_run_summary_reports_duration():
    summary = PipelineRunSummary(
        started_at=PUBLISHED,
        finished_at=PUBLISHED.replace(minute=1),
        sources_processed=2,
        signals_collected=7,
    )
    assert summary.duration_seconds == 60.0


# ── Publication ──────────────────────────────────────────────────────────────


def test_publication_and_watch_scaffold():
    publisher = BriefPublisher()
    output = publisher.render([make_signal("sig-2", "Signal Two")])
    target = WatchTarget(name="Portland Water Bureau", endpoint="https://example.com/feed")

    assert "Metro Citizens Brief" in output
    assert "Signal Two" in output
    assert target.name == "Portland Water Bureau"


def test_render_counts_signals():
    output = BriefPublisher().render(
        [make_signal("sig-3", "Three"), make_signal("sig-4", "Four")]
    )
    assert "(2 signals)" in output
    assert output.count("\n- ") == 2


def test_render_of_an_empty_set():
    assert BriefPublisher().render([]) == "# Metro Citizens Brief (0 signals)"


def test_untitled_signal_falls_back_to_its_text():
    """Adapters without a natural headline still render legibly."""
    untitled = Signal(
        source="dummy",
        source_type=SourceType.OLIS,
        text="A committee action was recorded on the measure.",
        published_at=PUBLISHED,
    )
    assert untitled.title is None
    assert "A committee action" in BriefPublisher().render([untitled])


def test_long_untitled_text_is_truncated_on_a_word_boundary():
    long_signal = Signal(
        source="dummy",
        source_type=SourceType.OLIS,
        text=" ".join(["word"] * 200),
        published_at=PUBLISHED,
    )
    assert long_signal.display_title.endswith("...")
    assert len(long_signal.display_title) <= 84


# ── Confidence tier ──────────────────────────────────────────────────────────


def test_confidence_tier_is_importable_from_the_package_root():
    assert ConfidenceTier.HARD_RECORD.value == "HARD_RECORD"
    assert {t.value for t in ConfidenceTier} == {"HARD_RECORD", "REPORTED", "INFERRED"}
