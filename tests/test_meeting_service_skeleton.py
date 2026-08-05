from __future__ import annotations

import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from meeting_service.app.main import app
from meeting_service.app.infrastructure.runtime_store import InMemoryRuntimeStore


class MeetingServiceSkeletonTests(unittest.TestCase):
    def test_health_endpoints(self) -> None:
        with TestClient(app) as client:
            self.assertEqual(client.get("/health/live").status_code, 200)
            self.assertEqual(client.get("/health/ready").json()["status"], "ok")

    def test_runtime_lifecycle_is_service_local(self) -> None:
        meeting_id = uuid4()
        with TestClient(app) as client:
            created = client.post(f"/internal/v1/meetings/{meeting_id}/runtime")
            self.assertEqual(created.status_code, 201)
            payload = created.json()
            self.assertEqual(payload["meeting_id"], str(meeting_id))
            self.assertEqual(payload["status"], "STARTING")
            stopped = client.post(
                f"/internal/v1/runtimes/{payload['runtime_session_id']}/stop"
            )
            self.assertEqual(stopped.status_code, 200)
            self.assertEqual(stopped.json()["status"], "COMPLETED")

    def test_store_does_not_create_cross_service_reference(self) -> None:
        meeting_id = uuid4()
        session = InMemoryRuntimeStore().create(meeting_id)
        self.assertEqual(session.meeting_id, meeting_id)
        self.assertFalse(hasattr(session, "ecabinet_model"))


if __name__ == "__main__":
    unittest.main()
