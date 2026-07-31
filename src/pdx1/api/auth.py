"""
API key authentication guard.

When PDX1_API_KEY is set (non-empty), every request must carry a matching
X-API-Key header. If the env var is absent or blank, auth is bypassed -- suitable
for local development but not for production.
"""

from __future__ import annotations

import os

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


def _configured_key() -> str:
    """Read the expected API key from the environment at call time."""
    return os.environ.get("PDX1_API_KEY", "").strip()


def require_api_key(api_key: str | None = Security(_API_KEY_HEADER)) -> None:
    """
    FastAPI dependency that enforces the API key when one is configured.

    Inject with `Depends(require_api_key)`.
    """
    expected = _configured_key()
    if not expected:
        # No key configured; auth is disabled (development mode).
        return
    import secrets
    if not api_key or not secrets.compare_digest(api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key header",
        )
