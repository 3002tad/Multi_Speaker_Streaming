from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from meeting_service.app.main import app
from meeting_service.app.domain.models import RuntimeStatus
from meeting_service.app.infrastructure.database import Base, create_session_factory
from meeting_service.app.infrastructure.repositories import SqlAlchemyRuntimeRepository
from meeting_service.app.infrastructure.runtime_store import InMemoryRuntimeStore
from meeting_service.app.application.runtime_service import RuntimeService


class MeetingServiceSkeletonTests(unittest.TestCase):
    def test_runtime_lifecycle_calls_ai_control_client(self) -> None:
        class FakeAI:
            def __init__(self) -> None:
                self.created = []
                self.stopped = []

            async def create_session(self, payload: dict, idempotency_key: str) -> dict:
                self.created.append((payload, idempotency_key))
                return {"status": "READY"}

            async def stop_session(self, runtime_session_id: str, idempotency_key: str) -> dict:
                self.stopped.append((runtime_session_id, idempotency_key))
                return {"status": "COMPLETED"}

        async def scenario() -> None:
            fake = FakeAI()
            service = RuntimeService(ai_client=fake)
            meeting_id = uuid4()
            session = await service.start(meeting_id, {"participants": [{"user_id": str(uuid4()), "display_name": "Dat"}]})
            self.assertEqual(session.status, RuntimeStatus.READY)
            await service.stop(session.runtime_session_id)
            self.assertEqual(len(fake.created), 1)
            self.assertEqual(len(fake.stopped), 1)

        import asyncio
        asyncio.run(scenario())

    def test_ai_callback_emits_only_accepted_event(self) -> None:
        from meeting_service.app.api.ai_events import receive_ai_event
        from meeting_service.app.api.ai_events import AIEvent

        event = AIEvent(
            schema_version=1,
            event_id=uuid4(),
            type="transcript.final",
            meeting_id=uuid4(),
            runtime_session_id=uuid4(),
            occurred_at="2026-08-05T00:00:00Z",
            sequence=1,
            payload={"segment_id": "seg-1"},
        )

        async def scenario() -> None:
            with patch("meeting_service.app.api.ai_events.sio.emit", new_callable=AsyncMock) as emit:
                result = await receive_ai_event(type("Request", (), {"app": type("App", (), {"state": type("State", (), {})()})()})(), event)
                self.assertEqual(result, {"status": "accepted"})
                emit.assert_awaited_once()
                self.assertEqual(emit.await_args.kwargs["room"], f"meeting:{event.meeting_id}")

        import asyncio
        asyncio.run(scenario())

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

    def test_sql_repository_persists_external_meeting_id_without_fk(self) -> None:
        factory = create_session_factory("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(factory.kw["bind"])
        repository = SqlAlchemyRuntimeRepository(factory)
        meeting_id = uuid4()
        created = repository.create(meeting_id, {"meeting_id": str(meeting_id)})
        self.assertEqual(repository.create(meeting_id, {}).runtime_session_id, created.runtime_session_id)
        stopped = repository.set_status(created.runtime_session_id, RuntimeStatus.COMPLETED)
        self.assertEqual(stopped.status, RuntimeStatus.COMPLETED)


if __name__ == "__main__":
    unittest.main()
