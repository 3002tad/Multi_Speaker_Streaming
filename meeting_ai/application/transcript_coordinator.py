"""Application boundary for realtime transcript delivery.

The coordinator owns transport-neutral partial/final delivery policy. It does
not decode audio, identify speakers, or persist data; the Agent/Meeting Service
remain responsible for callback persistence and event ordering.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from typing import Any


SendText = Callable[[str], Awaitable[None]]


class TranscriptCoordinator:
    """Deduplicate and throttle one source's realtime transcript messages."""

    def __init__(
        self,
        send_text: SendText,
        *,
        partial_interval_seconds: float = 0.25,
    ) -> None:
        self._send_text = send_text
        self._partial_interval_seconds = max(0.0, partial_interval_seconds)
        self._last_partial_text = ""
        self._last_partial_sent_at = 0.0
        self._final_keys: set[tuple[str, str, str, str]] = set()

    def begin_turn(self) -> None:
        """Reset partial suppression at a new VAD/global turn."""
        self._last_partial_text = ""
        self._last_partial_sent_at = 0.0

    async def publish_partial(
        self,
        text: str,
        *,
        identity: str,
        speaker: str,
    ) -> bool:
        value = str(text or "").strip()
        now = time.monotonic()
        if (
            not value
            or value == self._last_partial_text
            or now - self._last_partial_sent_at < self._partial_interval_seconds
        ):
            return False
        payload = {
            "partial": value,
            "identity": identity,
            "speaker": speaker,
            "identity_method": "mic_fallback",
            "speaker_confidence": None,
            "ts": time.time(),
        }
        try:
            await self._send_text(json.dumps(payload, ensure_ascii=False))
        except Exception:
            return False
        self._last_partial_text = value
        self._last_partial_sent_at = now
        return True

    async def publish_final(self, payload: dict[str, Any]) -> bool:
        """Send one final evidence payload, suppressing exact local repeats."""
        key = (
            str(payload.get("global_turn_id") or ""),
            str(payload.get("raw_text") or payload.get("final_asr_text") or ""),
            str(payload.get("start_time") or ""),
            str(payload.get("end_time") or ""),
        )
        if key[0] and key in self._final_keys:
            return False
        try:
            await self._send_text(json.dumps(payload, ensure_ascii=False))
        except Exception:
            return False
        if key[0]:
            self._final_keys.add(key)
        return True
