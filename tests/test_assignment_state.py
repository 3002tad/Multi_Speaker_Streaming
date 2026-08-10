from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from meeting_ai.core.assignment_state import AssignmentStateStore


class AssignmentStateStoreTests(unittest.TestCase):
    def test_save_load_and_clear_assignment_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agent-assignment.json"
            store = AssignmentStateStore(path)
            active = {
                "runtime_session_id": "runtime-1",
                "meeting_id": "meeting-1",
                "assignment_generation": 3,
                "payload": {
                    "meeting_id": "meeting-1",
                    "livekit": {"room": "meeting-room"},
                    "participants": [{"display_name": "Dat"}],
                },
                "participant_revision": 2,
            }
            store.save(assignment_generation_counter=3, active=active)
            self.assertEqual(store.load()["active"], active)
            self.assertEqual(store.load()["assignment_generation_counter"], 3)
            self.assertFalse(list(Path(directory).glob("*.tmp")))

            store.clear()
            self.assertIsNone(store.load())

    def test_corrupt_or_wrong_schema_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agent-assignment.json"
            path.write_text("not-json", encoding="utf-8")
            self.assertIsNone(AssignmentStateStore(path).load())
            path.write_text(
                json.dumps({"schema_version": 99, "active": {"runtime_session_id": "x"}}),
                encoding="utf-8",
            )
            self.assertIsNone(AssignmentStateStore(path).load())

    def test_active_assignment_requires_runtime_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = AssignmentStateStore(Path(directory) / "agent.json")
            with self.assertRaises(ValueError):
                store.save(assignment_generation_counter=1, active={})

    def test_two_runtime_lifecycle_and_restart_cursor_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = AssignmentStateStore(Path(directory) / "agent.json")
            first = {
                "runtime_session_id": "runtime-1",
                "meeting_id": "meeting-1",
                "assignment_generation": 1,
                "payload": {"livekit": {"room": "room-1"}},
            }
            store.save(assignment_generation_counter=1, active=first)
            self.assertEqual(store.load()["active"]["runtime_session_id"], "runtime-1")

            # stop clears the active assignment; the next start may reuse a
            # database row but receives a new assignment generation.
            store.clear()
            second = {
                "runtime_session_id": "runtime-2",
                "meeting_id": "meeting-2",
                "assignment_generation": 2,
                "payload": {"livekit": {"room": "room-2"}},
            }
            store.save(assignment_generation_counter=2, active=second)
            restored = store.load()
            self.assertEqual(restored["assignment_generation_counter"], 2)
            self.assertEqual(restored["active"]["runtime_session_id"], "runtime-2")


if __name__ == "__main__":
    unittest.main()
