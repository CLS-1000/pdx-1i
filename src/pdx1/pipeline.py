from __future__ import annotations

from datetime import UTC, datetime

from pdx1.models import PipelineRunSummary, Signal
from pdx1.sources import SourceAdapter


class Pipeline:
    def __init__(self, adapters: list[SourceAdapter] | None = None):
        self.adapters = adapters or []

    def add_adapter(self, adapter: SourceAdapter) -> None:
        self.adapters.append(adapter)

    def collect(self) -> list[Signal]:
        signals: list[Signal] = []
        for adapter in self.adapters:
            signals.extend(adapter.fetch())
        return signals

    def run(self) -> PipelineRunSummary:
        started_at = datetime.now(UTC)
        signals = self.collect()
        finished_at = datetime.now(UTC)
        return PipelineRunSummary(
            started_at=started_at,
            finished_at=finished_at,
            sources_processed=len(self.adapters),
            signals_collected=len(signals),
        )
