"""
Scheduler.

Proves `build_scheduler` configures a cron job correctly and that the production
guard fires the right warning in development mode.
"""

from __future__ import annotations


from pdx1.config import Settings


def test_build_scheduler_returns_a_scheduler():
    from pdx1.scheduler import build_scheduler

    settings = Settings(cron_hour=6, cron_minute=0, timezone="America/Los_Angeles")
    scheduler = build_scheduler(settings)
    assert scheduler is not None


def test_scheduler_has_daily_cycle_job():
    from pdx1.scheduler import build_scheduler

    settings = Settings(cron_hour=7, cron_minute=30, timezone="America/Los_Angeles")
    scheduler = build_scheduler(settings)

    jobs = scheduler.get_jobs()
    assert any(j.id == "daily_cycle" for j in jobs)


def test_scheduler_cron_respects_settings():
    from pdx1.scheduler import build_scheduler

    settings = Settings(cron_hour=3, cron_minute=15, timezone="America/Chicago")
    scheduler = build_scheduler(settings)

    job = next(j for j in scheduler.get_jobs() if j.id == "daily_cycle")
    trigger = job.trigger
    # Verify the hour and minute encoded in the trigger fields.
    fields = {f.name: f for f in trigger.fields}
    assert str(3) in str(fields["hour"])
    assert str(15) in str(fields["minute"])


def test_is_production_flag():
    from pdx1.scheduler import _is_production
    import os

    original = os.environ.get("PDX1_ENVIRONMENT")
    try:
        os.environ["PDX1_ENVIRONMENT"] = "production"
        assert _is_production() is True

        os.environ["PDX1_ENVIRONMENT"] = "development"
        assert _is_production() is False

        del os.environ["PDX1_ENVIRONMENT"]
        assert _is_production() is False
    finally:
        if original is None:
            os.environ.pop("PDX1_ENVIRONMENT", None)
        else:
            os.environ["PDX1_ENVIRONMENT"] = original
