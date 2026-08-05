from __future__ import annotations

from typing import Any


class RuntimeTokenVerifier:
    """Claim adapter placeholder; production will verify eCabinet signature."""

    def verify(self, token: Any) -> dict[str, Any]:
        if isinstance(token, dict) and token.get("sub") and token.get("meeting_id"):
            return dict(token)
        raise ValueError("runtime token verification is not configured")
