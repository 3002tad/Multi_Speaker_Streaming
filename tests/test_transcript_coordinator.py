from __future__ import annotations

import json
import unittest

from meeting_ai.application.transcript_coordinator import TranscriptCoordinator


class TranscriptCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_is_throttled_and_resets_per_turn(self) -> None:
        messages: list[dict] = []

        async def send(value: str) -> None:
            messages.append(json.loads(value))

        coordinator = TranscriptCoordinator(send, partial_interval_seconds=0)
        self.assertTrue(await coordinator.publish_partial("một", identity="mic-a", speaker="A"))
        self.assertFalse(await coordinator.publish_partial("một", identity="mic-a", speaker="A"))
        coordinator.begin_turn()
        self.assertTrue(await coordinator.publish_partial("một", identity="mic-a", speaker="A"))
        self.assertEqual(len(messages), 2)

    async def test_exact_final_repeat_is_suppressed(self) -> None:
        messages: list[dict] = []

        async def send(value: str) -> None:
            messages.append(json.loads(value))

        coordinator = TranscriptCoordinator(send)
        payload = {
            "global_turn_id": "turn-1",
            "raw_text": "xin chào",
            "start_time": 1.0,
            "end_time": 2.0,
        }
        self.assertTrue(await coordinator.publish_final(payload))
        self.assertFalse(await coordinator.publish_final(payload))
        self.assertEqual(len(messages), 1)


if __name__ == "__main__":
    unittest.main()
