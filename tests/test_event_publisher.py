from __future__ import annotations

import asyncio
import unittest
from uuid import uuid4

from agent import EventPublisher


class _Response:
    def raise_for_status(self) -> None:
        return None


class _FlakyClient:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    async def post(self, *args, **kwargs) -> _Response:
        self.calls += 1
        if self.calls <= self.failures:
            raise OSError("callback unavailable")
        return _Response()


class EventPublisherTests(unittest.IsolatedAsyncioTestCase):
    def _publisher(self) -> EventPublisher:
        return EventPublisher(
            {
                "meeting_id": str(uuid4()),
                "runtime_session_id": str(uuid4()),
                "callback": {"url": "http://meeting-service/internal/v1/ai-events"},
            }
        )

    async def test_callback_failure_is_retried_and_flushed(self) -> None:
        publisher = self._publisher()
        client = _FlakyClient(failures=2)
        await publisher.publish(
            client,
            {
                "type": "transcript.partial",
                "segment_id": "spool-1",
                "source_id": "mic-a",
                "speaker": "Dat",
                "text": "bản nháp",
            },
        )
        await publisher.close(timeout=3.0)
        self.assertGreaterEqual(client.calls, 3)
        self.assertEqual(publisher.dropped, 0)

    async def test_spool_is_bounded_and_partial_payload_is_contract_safe(self) -> None:
        publisher = self._publisher()
        publisher._max_spool = 2
        publisher._enqueue({"id": 1})
        publisher._enqueue({"id": 2})
        publisher._enqueue({"id": 3})
        self.assertEqual(publisher.dropped, 1)
        partial = publisher._canonical_payload(
            "transcript.partial",
            {
                "segment_id": "partial-1",
                "source_id": "mic-a",
                "speaker": "Dat",
                "text": "bản nháp",
                "revision": 1,
                "signal_rms": 0.2,
                "refinement": {"decision": "ignored"},
            },
        )
        self.assertNotIn("quality", partial)
        self.assertNotIn("pipeline_meta", partial)
        self.assertEqual(partial["content_text"], "bản nháp")
        self.assertNotIn("user_id", publisher._speaker("Dat", {"speaker_id": "Dat"}))

    async def test_refinement_payload_uses_updated_event_and_monotonic_revision(self) -> None:
        publisher = self._publisher()
        client = _FlakyClient(failures=0)
        await publisher.publish(
            client,
            {
                "type": "transcript.final",
                "segment_id": "revision-1",
                "source_id": "mic-a",
                "speaker": "Dat",
                "text": "raw",
                "raw_text": "raw",
                "start_time": 1,
                "end_time": 2,
            },
        )
        await publisher.publish(
            client,
            {
                "type": "transcript.final",
                "segment_id": "revision-1",
                "speaker": "Dat",
                "text": "refined",
                "raw_text": "raw",
                "start_time": 1,
                "end_time": 2,
                "is_refinement_update": True,
            },
        )
        await publisher.close()
        self.assertEqual(client.calls, 2)
        self.assertEqual(publisher.sequence, 2)
        self.assertEqual(publisher._segment_revisions["revision-1"], 2)


if __name__ == "__main__":
    unittest.main()
