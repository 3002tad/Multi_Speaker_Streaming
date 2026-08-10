from __future__ import annotations

import unittest

from agent import AssignmentCursor


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


if __name__ == "__main__":
    unittest.main()
