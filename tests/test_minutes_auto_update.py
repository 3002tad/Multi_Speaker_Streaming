from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from uuid import uuid4

from meeting_service.app.application.minutes_auto_update import MinutesAutoUpdateCoordinator
from meeting_service.app.domain.models import RuntimeStatus


class _Analysis:
    def __init__(self, meeting_id):
        self.meeting_id = meeting_id
        self.calls = 0
        self.repository = SimpleNamespace(
            get=lambda _meeting_id: {
                "status": "SUCCEEDED",
                "base_transcript_revision": 1,
                "evidence": {"auto_update_enabled": True},
            }
        )

    def auto_update_status(self, _meeting_id):
        return True

    @staticmethod
    def set_auto_update(_meeting_id, _enabled):
        return {"status": "SUCCEEDED"}

    @staticmethod
    def build_evidence(**_kwargs):
        return 2, {}

    async def request(self, **_kwargs):
        self.calls += 1
        return {"status": "RUNNING"}


class _Content:
    def minutes(self, _meeting_id):
        return {"status": "DRAFT", "revision": 1, "document": {}}

    def transcript(self, _meeting_id):
        return [{"segment_id": "seg-2", "content_text": "Nội dung mới"}]


class _Runtime:
    status_value = RuntimeStatus.READY

    def status(self, meeting_id):
        return SimpleNamespace(
            meeting_id=meeting_id,
            runtime_session_id=uuid4(),
            status=self.status_value,
        )

    @staticmethod
    def snapshot(_meeting_id):
        return {"meeting": {"title": "Test"}}


class MinutesAutoUpdateTests(unittest.IsolatedAsyncioTestCase):
    async def test_schedule_coalesces_and_requests_after_quiet_window(self):
        meeting_id = uuid4()
        analysis = _Analysis(meeting_id)
        coordinator = MinutesAutoUpdateCoordinator(
            analysis, _Content(), _Runtime(), debounce_seconds=0.01
        )
        coordinator.schedule(meeting_id)
        coordinator.schedule(meeting_id)
        await asyncio.sleep(0.35)
        self.assertEqual(analysis.calls, 1)

    async def test_disable_cancels_pending_update(self):
        meeting_id = uuid4()
        analysis = _Analysis(meeting_id)
        coordinator = MinutesAutoUpdateCoordinator(
            analysis, _Content(), _Runtime(), debounce_seconds=0.2
        )
        coordinator.schedule(meeting_id)
        await coordinator.disable(meeting_id)
        await asyncio.sleep(0.25)
        self.assertEqual(analysis.calls, 0)
