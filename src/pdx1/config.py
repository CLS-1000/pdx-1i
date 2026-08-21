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
from enum import Enum
from pathlib import Path

from dotenv import load_dotenv

from .models import AnomalyTier

load_dotenv()


class ConfigError(RuntimeError):
    """
    Configuration is missing or self-contradictory, and the process must not run.

    Raised rather than defaulted. Every use of this exception guards a choice where a
    silent default would change what the engine publishes -- above all the choice
    between replaying fixtures and reading the real sources.
    """


class SourceMode(str, Enum):
    """
    Where adapters read their payloads from. There is no default.

    This is the single value that selects fixture replay or live fetch. It must be
    declared explicitly, because the two modes produce different records from the same
    code: a fixture replay publishes a brief about a frozen May 2026 snapshot, and it
    does so without a single network call to say otherwise. An operator who believes
    they are running live and is not gets a plausible brief built from replayed data,
    which is the failure this enum exists to make impossible.
    """

    LIVE = "live"
    FIXTURE = "fixture"


#: The environment key that selects the mode.
SOURCE_MODE_KEY = "PDX1_SOURCE_MODE"

#: Removed in favour of SOURCE_MODE_KEY. Still recognised -- to refuse. A stale
#: `PDX1_LIVE=true` that was quietly ignored would be a live deployment silently
#: serving fixtures, which is exactly the failure this rewrite removes.
_LEGACY_LIVE_KEY = "PDX1_LIVE"


def _resolve_source_mode(env: dict[str, str] | None = None) -> SourceMode:
    """
    Read the source mode, refusing anything that is not one explicit valid value.

    Missing, blank, unrecognised, or accompanied by the legacy key -- all refuse. The
    only outcomes are SourceMode.LIVE, SourceMode.FIXTURE, and ConfigError.
    """
    env = os.environ if env is None else env
    valid = ", ".join(m.value for m in SourceMode)

    if env.get(_LEGACY_LIVE_KEY) is not None:
        raise ConfigError(
            f"{_LEGACY_LIVE_KEY} is no longer read; it was replaced by "
            f"{SOURCE_MODE_KEY} ({valid}). Remove {_LEGACY_LIVE_KEY} from the "
            "environment and from .env, then set the new key. Refusing to run "
            "rather than guess which one you meant."
        )

    raw = env.get(SOURCE_MODE_KEY, "").strip()
    if not raw:
        raise ConfigError(
            f"{SOURCE_MODE_KEY} is not set. It has no default: set it to one of "
            f"({valid}). `fixture` replays the checked-in payloads and reaches no "
            "network; `live` reads the real sources."
        )
    try:
        return SourceMode(raw.lower())
    except ValueError:
        raise ConfigError(
            f"{SOURCE_MODE_KEY}={raw!r} is not a source mode. Valid values: {valid}."
        ) from None


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
class SourceUrls:
    """
    Per-source endpoint overrides.

    Empty means "use the adapter's registered default". These exist because feed URLs
    rot: a live run on 2026-08-06 found most of the defaults returning 404, and a
    publisher moving an endpoint should cost an .env line rather than a release. Run
    `pdx1 --check-endpoints` to see which are answering before changing one.
    """

    orestar: str = ""
    olis: str = ""
    sei: str = ""
    wa_pdc: str = ""
    portland_press: str = ""


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
    #: Last-good payload cache for live adapters. A feed outage falls back to the
    #: newest body here rather than dropping the source from the cycle.
    cache_dir: Path = Path("cache/pdx1")
    environment: str = "development"
    log_level: str = "INFO"

    gates: GateConfig = field(default_factory=GateConfig)
    timeouts: SourceTimeouts = field(default_factory=SourceTimeouts)
    urls: SourceUrls = field(default_factory=SourceUrls)

    baseline_window_days: int = 90
    publish_on_change: bool = False
    publish_anomaly_tier: AnomalyTier = AnomalyTier.TIER_1
    trigger_weight_threshold: float = 3.0
    trigger_floor_days: int = 7

    timezone: str = "America/Los_Angeles"
    cron_hour: int = 6
    cron_minute: int = 0

    #: Fixture replay or live fetch. None means "not declared", which is not a
    #: runnable state: `default_adapters` refuses it rather than picking one. There is
    #: deliberately no default here -- see SourceMode.
    source_mode: SourceMode | None = None
    #: When False the vocabulary gates -- tone and hedging -- are bypassed; source
    #: language is published as-is while citation discipline (attribution gate)
    #: remains enforced.
    tone_gate: bool = True

    @classmethod
    def from_env(cls) -> Settings:
        """
        Build settings from the current environment.

        Raises `ConfigError` when PDX1_SOURCE_MODE is missing or unrecognised. That is
        the point: a process that cannot say which sources it is reading must not
        start, because both answers produce a publishable brief.
        """
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
            cache_dir=Path(_env("PDX1_CACHE_DIR", "cache/pdx1")),
            environment=_env("PDX1_ENVIRONMENT", "development"),
            log_level=_env("PDX1_LOG_LEVEL", "INFO").upper(),
            gates=GateConfig(
                min_credibility=_env_float("PDX1_GATE_MIN_CREDIBILITY", 0.5),
                min_words=_env_int("PDX1_GATE_MIN_WORDS", 50),
                max_age_hours=_env_int("PDX1_GATE_MAX_AGE_HOURS", 48),
            ),
            urls=SourceUrls(
                orestar=_env("PDX1_ORESTAR_URL", ""),
                olis=_env("PDX1_OLIS_URL", ""),
                sei=_env("PDX1_SEI_URL", ""),
                wa_pdc=_env("PDX1_WA_PDC_URL", ""),
                portland_press=_env("PDX1_PORTLAND_PRESS_URL", ""),
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
            source_mode=_resolve_source_mode(),
            tone_gate=_env_bool("PDX1_TONE_GATE", True),
        )
