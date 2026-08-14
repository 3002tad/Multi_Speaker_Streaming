"""Meeting AI minutes composition and callback boundary.

This module owns only the AI-side work.  It receives an immutable evidence
snapshot, calls the configured Ollama composer, and publishes one
``minutes.updated`` event.  It deliberately has no database or Meeting
Service imports.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

import httpx

from meeting_ai.application.minutes_composer import (
    MinutesCompositionError,
    OllamaMinutesComposer,
)
from meeting_ai.config import Settings


class MinutesWorkerError(RuntimeError):
    """Raised when composition or callback delivery cannot complete."""


def _composer_segments(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """Adapt the service evidence contract to the composer input contract."""
    result: list[dict[str, Any]] = []
    for index, item in enumerate(evidence.get("segments") or []):
        if not isinstance(item, dict):
            continue
        text = str(item.get("content_text") or "").strip()
        segment_id = str(item.get("segment_id") or "").strip()
        if not text or not segment_id:
            continue
        result.append(
            {
                "segment_id": segment_id,
                "speaker": str(item.get("speaker_label") or "Không xác định"),
                "speaker_user_id": item.get("speaker_user_id"),
                # The composer only needs a stable ordering for prompt/context
                # budgeting. The original ISO timestamps remain in evidence
                # and are not rewritten into the visible document.
                "start_time": float(index),
                "end_time": float(index + 1),
                "text": text,
                "raw_text": text,
            }
        )
    return result


def _callback_event(
    evidence: dict[str, Any],
    document: dict[str, Any],
    generator_meta: dict[str, Any],
    sequence: int,
) -> dict[str, Any]:
    callback_meta = {
        **generator_meta,
        "auto_update": bool(evidence.get("auto_generated")),
    }
    return {
        "schema_version": 1,
        "event_id": str(uuid4()),
        "type": "minutes.updated",
        "meeting_id": str(evidence["meeting_id"]),
        "runtime_session_id": str(evidence["runtime_session_id"]),
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "sequence": int(sequence),
        "payload": {
            "analysis_id": str(evidence["analysis_id"]),
            "generation_id": str(evidence["generation_id"]),
            "base_transcript_revision": int(evidence["base_transcript_revision"]),
            "base_minutes_revision": int(evidence.get("base_minutes_revision") or 0),
            "document": document,
            "generator_meta": callback_meta,
        },
    }


class MinutesWorker:
    """Compose a snapshot and deliver its result without persistence."""

    def __init__(
        self,
        settings: Settings,
        *,
        http_client_factory: Callable[..., Any] = httpx.AsyncClient,
    ) -> None:
        self.settings = settings
        self._http_client_factory = http_client_factory

    async def compose(self, evidence: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        if not self.settings.minutes_composer_enabled:
            raise MinutesWorkerError("minutes composer is disabled")
        if self.settings.minutes_composer_mode != "llm":
            raise MinutesWorkerError(
                "MINUTES_COMPOSER_MODE must be 'llm' for P1-02 composition"
            )
        segments = _composer_segments(evidence)
        if not segments:
            raise MinutesWorkerError("minutes evidence has no usable final segments")
        try:
            return await OllamaMinutesComposer(self.settings).compose(
                meeting_title=str(
                    (evidence.get("meeting") or {}).get("title")
                    or "Biên bản cuộc họp"
                ),
                existing_document=evidence.get("previous_document"),
                segments=segments,
                started_at=(evidence.get("meeting") or {}).get("started_at"),
            )
        except MinutesCompositionError as exc:
            raise MinutesWorkerError(str(exc)) from exc

    async def publish(
        self,
        evidence: dict[str, Any],
        document: dict[str, Any],
        generator_meta: dict[str, Any],
        *,
        callback_url: str,
        sequence: int,
    ) -> dict[str, Any]:
        url = str(callback_url or "").strip()
        if not url:
            raise MinutesWorkerError("minutes callback URL is missing")
        event = _callback_event(evidence, document, generator_meta, sequence)
        headers = {"X-Service-Key": self.settings.meeting_service_key}
        last_error: Exception | None = None
        async with self._http_client_factory(timeout=5.0) as client:
            for attempt in range(3):
                try:
                    response = await client.post(url, json=event, headers=headers)
                    if 400 <= response.status_code < 500:
                        response.raise_for_status()
                    result = response.json()
                    if result.get("status") not in {"accepted", "duplicate", "stale"}:
                        raise MinutesWorkerError(
                            f"Meeting Service rejected minutes callback: {result.get('status')}"
                        )
                    return event
                except (httpx.HTTPError, ValueError, MinutesWorkerError) as exc:
                    last_error = exc
                    if attempt < 2:
                        await asyncio.sleep(0.1 * (attempt + 1))
        raise MinutesWorkerError("minutes callback unavailable") from last_error

    async def run(
        self,
        evidence: dict[str, Any],
        *,
        callback_url: str,
        sequence: int,
    ) -> dict[str, Any]:
        document, metadata = await self.compose(evidence)
        return await self.publish(
            evidence,
            document,
            metadata,
            callback_url=callback_url,
            sequence=sequence,
        )


__all__ = [
    "MinutesWorker",
    "MinutesWorkerError",
    "_callback_event",
    "_composer_segments",
]
