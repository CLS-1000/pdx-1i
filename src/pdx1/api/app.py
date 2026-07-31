"""
PDX-1i FastAPI application.

Entry point::

    pdx1-api                    # reads PDX1_API_HOST / PDX1_API_PORT from .env
    uvicorn pdx1.api.app:app    # direct uvicorn invocation

Routes
------
GET  /signals           raw harvested signals (paginated)
GET  /intel             analyzed IntelligenceRecords (paginated, filterable)
GET  /leads             INVESTIGATE / ESCALATE / CORROBORATED records
GET  /brief             latest assembled Metro Citizens Brief
POST /cycle/run         trigger one full intelligence cycle

Auth
----
If PDX1_API_KEY is set, every request must carry a matching X-API-Key header.
If the key is absent or blank, auth is disabled (development mode).
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..config import Settings
from ..store import DualWriteStore
from .routes import brief, cycle, intel, leads, signals

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Load settings and open the store on startup; nothing to tear down."""
    settings = Settings.from_env()
    store = DualWriteStore(settings.store_path, settings.db_path)
    app.state.settings = settings
    app.state.store = store
    app.state.last_brief = None
    logger.info(
        "PDX-1i API started — store=%s db=%s live=%s",
        settings.store_path,
        settings.db_path,
        settings.live_fetch,
    )
    yield


def _cors_origins() -> list[str]:
    raw = os.environ.get("PDX1_CORS_ORIGINS", "")
    return [o.strip() for o in raw.split(",") if o.strip()]


def create_app() -> FastAPI:
    """Build and return the FastAPI application instance."""
    app = FastAPI(
        title="PDX-1i — Portland Metro Intelligence",
        description=(
            "Open-source intelligence engine for Portland-area politics and civic "
            "infrastructure. Signals, records, leads, and briefs over HTTP."
        ),
        version="0.1.0",
        lifespan=_lifespan,
    )

    origins = _cors_origins()
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["X-API-Key"],
        )

    app.include_router(signals.router)
    app.include_router(intel.router)
    app.include_router(leads.router)
    app.include_router(brief.router)
    app.include_router(cycle.router)

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        """Liveness probe."""
        return {"status": "ok"}

    return app


app = create_app()


def main() -> None:
    """Console-script entry point: ``pdx1-api``."""
    import uvicorn

    host = os.environ.get("PDX1_API_HOST", "0.0.0.0")  # nosec B104
    port = int(os.environ.get("PDX1_API_PORT", "8000"))
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s :: %(message)s")
    uvicorn.run("pdx1.api.app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
