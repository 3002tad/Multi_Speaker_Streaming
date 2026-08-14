"""Debounced, evidence-based auto-update for draft meeting minutes.

The first explicit ``minutes/analyze`` request enables this coordinator.  It
never consumes partial transcript events and it never writes a REVIEWING or
APPROVED document.  Meeting Service owns the snapshot; Meeting AI still only
receives an immutable evidence request.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

from meeting_service.app.application.minutes_analysis import (
    ANALYSIS_PENDING,
    ANALYSIS_RUNNING,
    MinutesAnalysisConflict,
    MinutesAnalysisService,
)
from meeting_service.app.domain.models import RuntimeStatus


class MinutesAutoUpdateCoordinator:
    def __init__(
        self,
        analysis: MinutesAnalysisService,
        content_store: Any,
        runtime_service: Any,
        *,
        debounce_seconds: float = 5.0,
    ) -> None:
        self.analysis = analysis
        self.content_store = content_store
        self.runtime_service = runtime_service
        self.debounce_seconds = max(0.2, float(debounce_seconds))
        self._tasks: dict[str, asyncio.Task] = {}

    def schedule(self, meeting_id: UUID) -> None:
        key = str(meeting_id)
        previous = self._tasks.get(key)
        if previous is not None and not previous.done():
            previous.cancel()
        task = asyncio.create_task(self._run(meeting_id))
        self._tasks[key] = task
        task.add_done_callback(lambda finished: self._tasks.pop(key, None) if self._tasks.get(key) is finished else None)

    async def disable(self, meeting_id: UUID) -> dict[str, Any] | None:
        key = str(meeting_id)
        task = self._tasks.pop(key, None)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return self.analysis.set_auto_update(meeting_id, False)

    def disable_now(self, meeting_id: UUID) -> dict[str, Any] | None:
        """Cancel pending debounce from a synchronous lifecycle route."""
        key = str(meeting_id)
        task = self._tasks.pop(key, None)
        if task is not None and not task.done():
            task.cancel()
        return self.analysis.set_auto_update(meeting_id, False)

    async def cancel(self, meeting_id: UUID) -> None:
        key = str(meeting_id)
        task = self._tasks.pop(key, None)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def _enabled_draft(self, meeting_id: UUID) -> bool:
        if not self.analysis.auto_update_status(meeting_id):
            return False
        minutes = self.content_store.minutes(meeting_id)
        return str(minutes.get("status") or "DRAFT") == "DRAFT"

    async def _run(self, meeting_id: UUID) -> None:
        try:
            await asyncio.sleep(self.debounce_seconds)
            # Coalescing happens at the timer boundary. New final segments
            # cancel this task and start one fresh quiet window.
            if not self._enabled_draft(meeting_id):
                return
            runtime = self.runtime_service.status(meeting_id)
            if runtime is None or runtime.status in {
                RuntimeStatus.COMPLETED,
                RuntimeStatus.FAILED,
                RuntimeStatus.STOPPING,
            }:
                return

            # Wait for the explicit/previous auto job to finish so its minutes
            # revision becomes the CAS base for the next delta.
            for _ in range(240):
                latest = self.analysis.repository.get(meeting_id)
                if not latest or not self.analysis.auto_update_status(meeting_id):
                    return
                if latest.get("status") not in {ANALYSIS_PENDING, ANALYSIS_RUNNING}:
                    break
                await asyncio.sleep(0.5)
            else:
                return

            if not self._enabled_draft(meeting_id):
                return
            runtime = self.runtime_service.status(meeting_id)
            if runtime is None or runtime.status in {
                RuntimeStatus.COMPLETED,
                RuntimeStatus.FAILED,
                RuntimeStatus.STOPPING,
            }:
                return
            snapshot = self.runtime_service.snapshot(meeting_id)
            transcript = self.content_store.transcript(meeting_id)
            minutes = self.content_store.minutes(meeting_id)
            if not transcript or str(minutes.get("status") or "DRAFT") != "DRAFT":
                return

            latest = self.analysis.repository.get(meeting_id)
            known_revision = int((latest or {}).get("base_transcript_revision") or 0)
            try:
                current_revision, _ = self.analysis.build_evidence(
                    meeting_id=meeting_id,
                    runtime_session_id=runtime.runtime_session_id,
                    meeting_snapshot=snapshot,
                    transcript=transcript,
                    previous_document=minutes.get("document"),
                    previous_revision=int(minutes.get("revision") or 0),
                )
            except MinutesAnalysisConflict:
                return
            if current_revision == known_revision:
                return

            await self.analysis.request(
                meeting_id=meeting_id,
                runtime_session_id=runtime.runtime_session_id,
                meeting_snapshot=snapshot,
                transcript=transcript,
                previous_document=minutes.get("document"),
                previous_revision=int(minutes.get("revision") or 0),
                idempotency_key=f"auto-minutes:{meeting_id}:{uuid4()}",
                auto_generated=True,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # A failed auto job must not affect transcript delivery. The next
            # final segment or an explicit retry can schedule it again.
            return


__all__ = ["MinutesAutoUpdateCoordinator"]
