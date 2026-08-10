from __future__ import annotations

import hmac

from fastapi import Header, HTTPException

from meeting_service.app.config import settings


def require_service_key(
    x_service_key: str | None = Header(default=None, alias="X-Service-Key"),
) -> None:
    """Authenticate service-to-service calls.

    Internal endpoints are never public.  A missing deployment key is treated
    as a configuration error instead of silently disabling authentication.
    """
    expected = settings.service_key.strip()
    if not expected:
        raise HTTPException(status_code=503, detail="Meeting Service key is not configured")
    if not x_service_key or not hmac.compare_digest(x_service_key, expected):
        raise HTTPException(status_code=401, detail="Invalid service key")

