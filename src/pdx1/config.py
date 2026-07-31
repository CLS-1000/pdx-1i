"""
Runtime configuration.

Every key here corresponds to a variable documented in .env.example. Values are read
from the process environment, with a .env file loaded first if one is present.

Gate thresholds live here rather than as constants in gates.py so an operator can tune
the filter without editing code -- but the defaults are the SPEC-1 published values and
should be changed deliberately.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from .models import AnomalyTier

load_dotenv()


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class GateConfig:
    """Thresholds for the four-gate filter."""

    min_credibility: float = 0.5
    min_words: int = 50
    max_age_hours: int = 48


@dataclass(frozen=True)
class SourceTimeouts:
    """Per-adapter HTTP timeouts, in seconds."""

    orestar: int = 30
    olis: int = 30
    sei: int = 30
    wa_pdc: int = 30
    pdx911: int = 60


@dataclass(frozen=True)
class Settings:
    """Resolved configuration for one process."""

    store_path: Path = Path("pdx1_signals.jsonl")
    db_path: Path = Path("pdx1.db")
    #: Ground truth for assembled briefs. None derives it from `store_path`.
    briefs_path: Path | None = None
    environment: str = "development"
    log_level: str = "INFO"

    gates: GateConfig = field(default_factory=GateConfig)
    timeouts: SourceTimeouts = field(default_factory=SourceTimeouts)

    baseline_window_days: int = 90
    publish_on_change: bool = False
    publish_anomaly_tier: AnomalyTier = AnomalyTier.TIER_1
    trigger_weight_threshold: float = 3.0
    trigger_floor_days: int = 7

    timezone: str = "America/Los_Angeles"
    cron_hour: int = 6
    cron_minute: int = 0

    live_fetch: bool = False
    #: When False the tone-vocabulary gate is bypassed; source language is
    #: published as-is while citation discipline (attribution gate) remains enforced.
    tone_gate: bool = True

    @classmethod
    def from_env(cls) -> Settings:
        """Build settings from the current environment."""
        tier_raw = _env("PDX1_PUBLISH_ANOMALY_TIER", AnomalyTier.TIER_1.value).upper()
        try:
            tier = AnomalyTier(tier_raw)
        except ValueError:
            tier = AnomalyTier.TIER_1

        return cls(
            store_path=Path(_env("PDX1_STORE_PATH", "pdx1_signals.jsonl")),
            db_path=Path(_env("PDX1_DB_PATH", "pdx1.db")),
            briefs_path=(
                Path(os.environ["PDX1_BRIEFS_PATH"])
                if os.environ.get("PDX1_BRIEFS_PATH", "").strip()
                else None
            ),
            environment=_env("PDX1_ENVIRONMENT", "development"),
            log_level=_env("PDX1_LOG_LEVEL", "INFO").upper(),
            gates=GateConfig(
                min_credibility=_env_float("PDX1_GATE_MIN_CREDIBILITY", 0.5),
                min_words=_env_int("PDX1_GATE_MIN_WORDS", 50),
                max_age_hours=_env_int("PDX1_GATE_MAX_AGE_HOURS", 48),
            ),
            timeouts=SourceTimeouts(
                orestar=_env_int("ORESTAR_TIMEOUT", 30),
                olis=_env_int("OLIS_TIMEOUT", 30),
                sei=_env_int("SEI_TIMEOUT", 30),
                wa_pdc=_env_int("WA_PDC_TIMEOUT", 30),
                pdx911=_env_int("PDX911_TIMEOUT", 60),
            ),
            baseline_window_days=_env_int("PDX1_BASELINE_WINDOW_DAYS", 90),
            publish_on_change=_env_bool("PDX1_PUBLISH_ON_CHANGE", False),
            publish_anomaly_tier=tier,
            trigger_weight_threshold=_env_float("PDX1_TRIGGER_WEIGHT_THRESHOLD", 3.0),
            trigger_floor_days=_env_int("PDX1_TRIGGER_FLOOR_DAYS", 7),
            timezone=_env("PDX1_TIMEZONE", "America/Los_Angeles"),
            cron_hour=_env_int("PDX1_CRON_HOUR", 6),
            cron_minute=_env_int("PDX1_CRON_MINUTE", 0),
            live_fetch=_env_bool("PDX1_LIVE", False),
            tone_gate=_env_bool("PDX1_TONE_GATE", True),
        )
