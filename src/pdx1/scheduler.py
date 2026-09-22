"""
PDX-1i daily scheduler.

Runs the intelligence cycle on a cron schedule (default 06:00 PT) and optionally
starts the FastAPI surface alongside it.

The scheduler is only active when PDX1_ENVIRONMENT=production. In development,
cycles are run manually via ``pdx1`` or ``POST /cycle/run``.

Entry point::

    pdx1-scheduler              # starts scheduler + API server together

Schedule configuration (via .env or environment):

    PDX1_CRON_HOUR=6            # 0-23, local time
    PDX1_CRON_MINUTE=0          # 0-59
    PDX1_TIMEZONE=America/Los_Angeles

The scheduler uses APScheduler with the blocking scheduler backend when run
standalone, or the background scheduler when embedded alongside uvicorn.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def _is_production() -> bool:
    return os.environ.get("PDX1_ENVIRONMENT", "development").lower() == "production"


def build_scheduler(settings=None):
    """
    Build and return a configured APScheduler instance.

    Returns a `BlockingScheduler` with the cron job attached. Start it with
    `scheduler.start()`. Raises `ImportError` if apscheduler is not installed.
    """
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError as exc:
        raise ImportError(
            "apscheduler is required for the scheduler -- "
            "install it with: pip install 'pdx-1i[api]'"
        ) from exc

    from .config import Settings
    from .pipeline import run_cycle

    settings = settings or Settings.from_env()

    scheduler = BlockingScheduler(timezone=settings.timezone)

    trigger = CronTrigger(
        hour=settings.cron_hour,
        minute=settings.cron_minute,
        timezone=settings.timezone,
    )

    def _cycle_job() -> None:
        logger.info("scheduled cycle starting")
        try:
            # No explicit `now`: run_cycle anchors live runs to the real clock
            # itself (see `_anchor`). Passing one here would only duplicate that.
            result = run_cycle(settings=settings)
            logger.info(
                "scheduled cycle complete: run=%s harvested=%d written=%d",
                result.run_id,
                result.harvested,
                result.written,
            )
            if result.brief:
                logger.info("brief published: %s", result.brief.brief_id)
        except Exception:
            logger.exception("scheduled cycle failed")

    scheduler.add_job(_cycle_job, trigger=trigger, id="daily_cycle", replace_existing=True)
    logger.info(
        "scheduler configured: %02d:%02d %s",
        settings.cron_hour,
        settings.cron_minute,
        settings.timezone,
    )
    return scheduler


def main() -> None:
    """
    Console-script entry point: ``pdx1-scheduler``.

    Starts uvicorn (FastAPI) and the APScheduler cron job together. The API runs
    in a background thread; the scheduler blocks the main thread.
    """
    import threading

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s :: %(message)s")

    from .config import ConfigError, Settings

    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        # The scheduler is the process that runs unattended, so a misconfiguration here
        # is the one most likely to go unnoticed. Refuse loudly and exit non-zero so
        # systemd records a failed start rather than a running service quietly
        # replaying fixtures at 06:00 every morning.
        logger.error("refusing to start -- %s", exc)
        raise SystemExit(2) from exc

    # A warning here was not enough. The scheduler is the unattended process, so an
    # environment nobody declared is an environment nobody checked -- and an unset
    # variable is indistinguishable from a systemd unit that dropped it. Refuse.
    if not settings.environment_declared:
        logger.error(
            "refusing to start -- PDX1_ENVIRONMENT is unset. Set it explicitly: "
            "'production' on the VM, 'development' locally. A scheduler running with "
            "an undeclared environment is not visibly wrong, which is the problem."
        )
        raise SystemExit(2)

    if not _is_production():
        logger.warning(
            "PDX1_ENVIRONMENT=%s -- the scheduler is intended for production "
            "deployments. Running anyway because the environment was declared.",
            settings.environment,
        )

    # The embedded API is opt-in and off by default. On the VM `pdx1-api` owns port
    # 8000 as its own systemd unit; a scheduler that also bound 8000 would lose the
    # race and, under Restart=on-failure, crash-loop rather than fail visibly.
    if settings.scheduler_embedded_api:
        try:
            import uvicorn

            host = settings.api_host
            port = settings.api_port

            def _serve() -> None:
                uvicorn.run(
                    "pdx1.api.app:app", host=host, port=port, reload=False, log_level="info"
                )

            api_thread = threading.Thread(target=_serve, daemon=True, name="api-server")
            api_thread.start()
            logger.info("API server thread started on %s:%d", host, port)
        except ImportError:
            logger.warning("uvicorn not installed; starting scheduler without API server")
    else:
        logger.info(
            "running headless -- embedded API off (set PDX1_SCHEDULER_EMBEDDED_API=true "
            "to serve the API from this process instead of a separate pdx1-api unit)"
        )

    # Block on the scheduler.
    scheduler = build_scheduler(settings)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("scheduler stopped")


if __name__ == "__main__":
    main()
