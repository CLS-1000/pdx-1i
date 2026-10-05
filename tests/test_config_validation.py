"""Runtime settings must fail closed instead of silently changing behavior."""

from __future__ import annotations

import pytest

from pdx1.config import ConfigError, Settings


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("PDX1_GATE_MIN_WORDS", "fifty"),
        ("PDX1_CRON_HOUR", "6.5"),
        ("PDX1_TRIGGER_WEIGHT_THRESHOLD", "NaN"),
        ("PDX1_RETRY_BACKOFF_S", "inf"),
    ],
)
def test_malformed_numeric_settings_raise_actionable_errors(monkeypatch, key, value):
    monkeypatch.setenv(key, value)

    with pytest.raises(ConfigError, match=key):
        Settings.from_env()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("PDX1_GATE_MIN_CREDIBILITY", "1.01"),
        ("PDX1_GATE_MAX_AGE_HOURS", "0"),
        ("PDX1_RETRY_MAX_ATTEMPTS", "0"),
        ("PDX1_RETRY_BACKOFF_S", "-0.1"),
        ("PDX1_CRON_HOUR", "24"),
        ("PDX1_CRON_MINUTE", "60"),
        ("PDX1_API_PORT", "65536"),
    ],
)
def test_out_of_range_settings_raise_actionable_errors(monkeypatch, key, value):
    monkeypatch.setenv(key, value)

    with pytest.raises(ConfigError, match=key):
        Settings.from_env()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("PDX1_PUBLISH_ON_CHANGE", "sometimes"),
        ("PDX1_SCHEDULER_EMBEDDED_API", "enabled"),
        ("PDX1_TONE_GATE", "treu"),
    ],
)
def test_unrecognised_boolean_settings_raise_actionable_errors(monkeypatch, key, value):
    monkeypatch.setenv(key, value)

    with pytest.raises(ConfigError, match=key):
        Settings.from_env()


def test_unrecognised_anomaly_tier_does_not_fall_back(monkeypatch):
    monkeypatch.setenv("PDX1_PUBLISH_ANOMALY_TIER", "TIER_UNKNOWN")

    with pytest.raises(ConfigError, match="PDX1_PUBLISH_ANOMALY_TIER"):
        Settings.from_env()


def test_unrecognised_environment_does_not_disable_production_guards(monkeypatch):
    monkeypatch.setenv("PDX1_ENVIRONMENT", "prodution")
    monkeypatch.setenv("PDX1_LIVE", "false")

    with pytest.raises(ConfigError, match="PDX1_ENVIRONMENT"):
        Settings.from_env()


def test_environment_is_normalized(monkeypatch):
    monkeypatch.setenv("PDX1_ENVIRONMENT", " Production ")
    monkeypatch.setenv("PDX1_LIVE", "true")

    assert Settings.from_env().environment == "production"


def test_settings_accept_documented_numeric_boundaries(monkeypatch):
    monkeypatch.setenv("PDX1_GATE_MIN_CREDIBILITY", "1")
    monkeypatch.setenv("PDX1_GATE_MIN_WORDS", "0")
    monkeypatch.setenv("PDX1_RETRY_BUDGET_S", "0")
    monkeypatch.setenv("PDX1_CRON_HOUR", "23")
    monkeypatch.setenv("PDX1_CRON_MINUTE", "59")
    monkeypatch.setenv("PDX1_API_PORT", "65535")

    settings = Settings.from_env()

    assert settings.gates.min_credibility == 1
    assert settings.gates.min_words == 0
    assert settings.retry.budget_s == 0
    assert (settings.cron_hour, settings.cron_minute) == (23, 59)
    assert settings.api_port == 65535
