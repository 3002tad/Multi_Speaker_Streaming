from __future__ import annotations

from typing import Any

import jwt

from meeting_service.app.config import settings


class RuntimeTokenVerifier:
    """Verify the short-lived actor token issued by eCabinet BFF."""

    def verify(self, token: Any) -> dict[str, Any]:
        if not isinstance(token, str) or not token.strip():
            raise ValueError("missing runtime token")
        try:
            claims = jwt.decode(
                token,
                settings.runtime_token_secret,
                algorithms=[settings.runtime_token_algorithm],
                issuer=settings.runtime_token_issuer,
                audience=settings.runtime_token_audience,
                options={"require": [
                    "sub", "meeting_id", "runtime_session_id", "permissions",
                    "iss", "aud", "iat", "exp", "jti",
                ]},
            )
        except jwt.PyJWTError as exc:
            raise ValueError("invalid runtime token") from exc
        if (
            not claims.get("sub")
            or not claims.get("meeting_id")
            or not claims.get("runtime_session_id")
            or not isinstance(claims.get("permissions"), list)
            or not claims.get("jti")
        ):
            raise ValueError("invalid runtime claims")
        return claims
