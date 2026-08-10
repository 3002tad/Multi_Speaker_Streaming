from __future__ import annotations

import unittest

from agent import AssignmentCursor, assignment_requires_rejoin
from meeting_ai.config import settings


class AssignmentCursorTests(unittest.TestCase):
    def test_resets_generation_when_ai_epoch_changes(self) -> None:
        cursor = AssignmentCursor()
        self.assertFalse(
            cursor.observe(
                {
                    "status": "READY",
                    "assignment_epoch": "ai-before-restart",
                    "assignment_generation": 7,
                }
            )
        )
        self.assertEqual(cursor.generation, 7)

        self.assertTrue(
            cursor.observe(
                {
                    "status": "IDLE",
                    "assignment_epoch": "ai-after-restart",
                    "assignment_generation": 7,
                }
            )
        )
        self.assertEqual(cursor.generation, 0)

        self.assertFalse(
            cursor.observe(
                {
                    "status": "READY",
                    "assignment_epoch": "ai-after-restart",
                    "assignment_generation": 1,
                }
            )
        )
        self.assertEqual(cursor.generation, 1)

    def test_generation_change_rejoins_even_when_runtime_id_is_reused(self) -> None:
        previous = {
            "status": "RECORDING",
            "runtime_session_id": "runtime-1",
            "assignment_epoch": "epoch-1",
            "assignment_generation": 4,
        }
        current = {**previous, "assignment_generation": 5}
        self.assertTrue(assignment_requires_rejoin(previous, current))

    def test_epoch_or_runtime_change_rejoins_and_idle_stops(self) -> None:
        previous = {
            "status": "RECORDING",
            "runtime_session_id": "runtime-1",
            "assignment_epoch": "epoch-1",
            "assignment_generation": 1,
        }
        self.assertTrue(
            assignment_requires_rejoin(
                previous,
                {**previous, "assignment_epoch": "epoch-2"},
            )
        )
        self.assertTrue(
            assignment_requires_rejoin(
                previous,
                {**previous, "runtime_session_id": "runtime-2"},
            )
        )
        self.assertTrue(assignment_requires_rejoin(previous, {"status": "IDLE"}))

    def test_same_assignment_does_not_rejoin(self) -> None:
        assignment = {
            "status": "READY",
            "runtime_session_id": "runtime-1",
            "assignment_epoch": "epoch-1",
            "assignment_generation": 1,
        }
        self.assertFalse(assignment_requires_rejoin(assignment, dict(assignment)))

    def test_missing_epoch_resets_cursor_after_epoch_was_observed(self) -> None:
        cursor = AssignmentCursor()
        self.assertFalse(
            cursor.observe(
                {
                    "status": "READY",
                    "assignment_epoch": "epoch-1",
                    "assignment_generation": 5,
                }
            )
        )
        self.assertTrue(
            cursor.observe(
                {"status": "IDLE", "assignment_generation": 5}
            )
        )
        self.assertEqual(cursor.generation, 0)

    def test_static_room_fallback_is_disabled_by_default(self) -> None:
        self.assertFalse(settings.agent_static_room_fallback)


if __name__ == "__main__":
    unittest.main()
