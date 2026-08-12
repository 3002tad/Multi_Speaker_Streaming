from __future__ import annotations

import unittest
from tempfile import TemporaryDirectory

from meeting_ai.application.session_manager import SessionConflict, SessionManager
from meeting_ai.core.assignment_state import AssignmentStateStore


class SessionManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.manager = SessionManager(
            AssignmentStateStore(f"{self.tempdir.name}/assignment.json")
        )

    @staticmethod
    def _payload(runtime_id: str = "runtime-a", meeting_id: str = "meeting-a") -> dict:
        return {
            "runtime_session_id": runtime_id,
            "meeting_id": meeting_id,
            "assignment_generation": 1,
            "livekit": {"room": "room-a"},
            "participants": [{"display_name": "Dat"}],
            "callback": {"url": "http://meeting-service/internal/v1/ai-events"},
        }

    def test_create_assignment_agent_status_and_stop(self) -> None:
        state, names = self.manager.create(self._payload(), "start-key")
        self.assertEqual(state["status"], "READY")
        self.assertEqual(names, ["Dat"])
        assignment = self.manager.assignment(0, "wss://livekit.example")
        self.assertEqual(assignment["runtime_session_id"], "runtime-a")
        self.assertEqual(assignment["livekit"]["room"], "room-a")
        self.assertEqual(
            self.manager.update_agent(
                {
                    "runtime_session_id": "runtime-a",
                    "assignment_epoch": assignment["assignment_epoch"],
                    "assignment_generation": assignment["assignment_generation"],
                    "status": "READY",
                }
            ),
            "accepted",
        )
        self.assertEqual(self.manager.state("runtime-a")["status"], "RECORDING")
        stopped = self.manager.stop("runtime-a", "stop-key")
        self.assertEqual(stopped["status"], "COMPLETED")
        self.assertEqual(self.manager.assignment(0, "wss://livekit.example")["status"], "IDLE")

    def test_rejects_second_active_runtime_and_stale_agent(self) -> None:
        self.manager.create(self._payload(), None)
        with self.assertRaises(SessionConflict):
            self.manager.create(self._payload("runtime-b", "meeting-b"), None)
        self.assertEqual(
            self.manager.update_agent(
                {
                    "runtime_session_id": "runtime-a",
                    "assignment_epoch": "wrong-epoch",
                    "assignment_generation": 1,
                    "status": "READY",
                }
            ),
            "stale",
        )

    def test_restores_active_assignment_with_new_epoch(self) -> None:
        self.manager.create(self._payload(), None)
        restored = SessionManager(
            AssignmentStateStore(f"{self.tempdir.name}/assignment.json")
        )
        assignment = restored.assignment(0, "wss://livekit.example")
        self.assertEqual(assignment["runtime_session_id"], "runtime-a")
        self.assertNotEqual(assignment["assignment_epoch"], self.manager.epoch)


if __name__ == "__main__":
    unittest.main()
