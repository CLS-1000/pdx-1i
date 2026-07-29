from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field


class ConfidenceTier(str, Enum):
    HARD_RECORD = "HARD_RECORD"
    REPORTED = "REPORTED"
    INFERRED = "INFERRED"


class Signal(BaseModel):
    signal_id: str
    source: str
    title: str
    confidence: ConfidenceTier = ConfidenceTier.REPORTED
    url: str | None = None
    published_at: datetime | None = None
    collected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    content: str | None = None


class PipelineRunSummary(BaseModel):
    started_at: datetime
    finished_at: datetime
    sources_processed: int
    signals_collected: int
