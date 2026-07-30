from __future__ import annotations

from pdx1 import ConfidenceTier, Pipeline, Signal
from pdx1.publication import BriefPublisher
from pdx1.watch import WatchTarget


class DummyAdapter:
    name = "dummy"

    def fetch(self) -> list[Signal]:
        return [
            Signal(
                signal_id="sig-1",
                source="dummy",
                title="Signal One",
                confidence=ConfidenceTier.HARD_RECORD,
            )
        ]


def test_pipeline_collect_and_run_summary():
    pipeline = Pipeline([DummyAdapter()])

    signals = pipeline.collect()
    summary = pipeline.run()

    assert len(signals) == 1
    assert signals[0].title == "Signal One"
    assert summary.sources_processed == 1
    assert summary.signals_collected == 1
    assert summary.finished_at >= summary.started_at


def test_publication_and_watch_scaffold():
    publisher = BriefPublisher()
    output = publisher.render([Signal(signal_id="sig-2", source="dummy", title="Signal Two")])
    target = WatchTarget(name="Portland Water Bureau", endpoint="https://example.com/feed")

    assert "Metro Citizens Brief" in output
    assert "Signal Two" in output
    assert target.name == "Portland Water Bureau"
