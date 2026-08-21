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


#: Spellings accepted for a boolean environment variable. Anything else is a typo,
#: and a typo must not resolve to a silent default -- see `_env_bool_strict`.
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class ConfigError(RuntimeError):
    """
    Configuration is missing or ambiguous and the process must not start.

    Raised rather than defaulted because the values guarded this way decide what the
    engine reads and publishes. A run that silently fell back would still write records
    and still assemble a brief -- it would just be describing the wrong world.
    """


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in _TRUE


def _env_bool_strict(key: str) -> bool | None:
    """
    Read a boolean that is not allowed to be guessed at.

    Returns None when unset or blank, so the caller decides whether absence is legal
    in its environment. Raises `ConfigError` on a value that is neither true nor false
    -- `PDX1_LIVE=ture` is not a request for fixture mode, it is a mistake, and
    resolving it to False would run the whole cycle against checked-in payloads while
    reporting success.
    """
    raw = os.environ.get(key)
    if raw is None or not raw.strip():
        return None
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ConfigError(
        f"{key}={raw!r} is not a recognised boolean. "
        f"Use one of {sorted(_TRUE)} or {sorted(_FALSE)}."
    )


def _resolve_live_fetch(environment: str) -> bool:
    """
    Decide fixture replay vs live HTTP -- the single switch, with no production default.

    Fixture mode is the right default for development and CI, where a reproducible
    cycle matters more than a fresh one. It is never a defensible default in
    production: an unset `PDX1_LIVE` on the VM would produce a brief every morning,
    on schedule, entirely from checked-in payloads, and nothing downstream would
    report anything wrong. So production must say which it wants.
    """
    explicit = _env_bool_strict("PDX1_LIVE")
    if explicit is not None:
        return explicit
    if environment.strip().lower() == "production":
        raise ConfigError(
            "PDX1_LIVE is unset and PDX1_ENVIRONMENT=production. "
            "Production must choose explicitly: set PDX1_LIVE=true to fetch from the "
            "real sources, or PDX1_LIVE=false to acknowledge a fixture replay. "
            "There is no default here because a silent fixture run publishes a brief "
            "that looks correct and describes nothing current."
        )
    return False


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
class RetryPolicy:
    """
    Bounded retry for live fetches.

    `max_attempts` counts the first try, so 3 means two retries. It is deliberately
    small: the cycle runs five adapters in series under a cron schedule, and a generous
    retry budget multiplied across them turns a partial outage into a run that is still
    going when the next one is due.
    """

    max_attempts: int = 3
    backoff_s: float = 2.0


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
    retry: RetryPolicy = field(default_factory=RetryPolicy)

    baseline_window_days: int = 90
    publish_on_change: bool = False
    publish_anomaly_tier: AnomalyTier = AnomalyTier.TIER_1
    trigger_weight_threshold: float = 3.0
    trigger_floor_days: int = 7

    timezone: str = "America/Los_Angeles"
    cron_hour: int = 6
    cron_minute: int = 0

    live_fetch: bool = False
    #: When False the vocabulary gates -- tone and hedging -- are bypassed; source
    #: language is published as-is while citation discipline (attribution gate)
    #: remains enforced.
    tone_gate: bool = True

    @classmethod
    def from_env(cls) -> Settings:
        """Build settings from the current environment."""
        tier_raw = _env("PDX1_PUBLISH_ANOMALY_TIER", AnomalyTier.TIER_1.value).upper()
        try:
            tier = AnomalyTier(tier_raw)
        except ValueError:
            tier = AnomalyTier.TIER_1

        environment = _env("PDX1_ENVIRONMENT", "development")

        return cls(
            store_path=Path(_env("PDX1_STORE_PATH", "pdx1_signals.jsonl")),
            db_path=Path(_env("PDX1_DB_PATH", "pdx1.db")),
            briefs_path=(
                Path(os.environ["PDX1_BRIEFS_PATH"])
                if os.environ.get("PDX1_BRIEFS_PATH", "").strip()
                else None
            ),
            cache_dir=Path(_env("PDX1_CACHE_DIR", "cache/pdx1")),
            environment=environment,
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
            retry=RetryPolicy(
                max_attempts=max(1, _env_int("PDX1_RETRY_MAX_ATTEMPTS", 3)),
                backoff_s=max(0.0, _env_float("PDX1_RETRY_BACKOFF_S", 2.0)),
            ),
            baseline_window_days=_env_int("PDX1_BASELINE_WINDOW_DAYS", 90),
            publish_on_change=_env_bool("PDX1_PUBLISH_ON_CHANGE", False),
            publish_anomaly_tier=tier,
            trigger_weight_threshold=_env_float("PDX1_TRIGGER_WEIGHT_THRESHOLD", 3.0),
            trigger_floor_days=_env_int("PDX1_TRIGGER_FLOOR_DAYS", 7),
            timezone=_env("PDX1_TIMEZONE", "America/Los_Angeles"),
            cron_hour=_env_int("PDX1_CRON_HOUR", 6),
            cron_minute=_env_int("PDX1_CRON_MINUTE", 0),
            live_fetch=_resolve_live_fetch(environment),
            tone_gate=_env_bool("PDX1_TONE_GATE", True),
        )
