from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from meeting_service.app.main import app
from meeting_service.app.domain.models import RuntimeStatus
from meeting_service.app.infrastructure.database import Base, create_session_factory
from meeting_service.app.infrastructure.repositories import SqlAlchemyAIEventRepository, SqlAlchemyRuntimeRepository
from meeting_service.app.application.meeting_content import SqlAlchemyMeetingContentRepository
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
            session = await service.start(meeting_id, {"meeting": {"status": "ONGOING"}, "participants": [{"user_id": str(uuid4()), "display_name": "Dat"}]})
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
            created = client.post(f"/internal/v1/meetings/{meeting_id}/runtime", json={"meeting": {"status": "ONGOING"}})
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

    def test_meeting_purge_is_idempotent(self) -> None:
        meeting_id = uuid4()
        with TestClient(app) as client:
            created = client.post(f"/internal/v1/meetings/{meeting_id}/runtime", json={"meeting": {"status": "ONGOING"}})
            self.assertEqual(created.status_code, 201)
            appended = client.post(
                f"/internal/v1/meetings/{meeting_id}/transcript",
                json={"segment_id": "purge-1", "text": "temporary"},
            )
            self.assertEqual(appended.status_code, 201)
            first = client.delete(f"/internal/v1/meetings/{meeting_id}")
            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.json()["status"], "PURGED")
            self.assertEqual(client.get(f"/internal/v1/meetings/{meeting_id}/status").status_code, 404)
            second = client.delete(f"/internal/v1/meetings/{meeting_id}")
            self.assertEqual(second.status_code, 200)
            self.assertEqual(second.json()["status"], "PURGED")

    def test_state_guard_blocks_invalid_runtime_start_and_early_minutes_approval(self) -> None:
        meeting_id = uuid4()
        with TestClient(app) as client:
            rejected_start = client.post(
                f"/internal/v1/meetings/{meeting_id}/runtime",
                json={"meeting": {"status": "DRAFT"}},
            )
            self.assertEqual(rejected_start.status_code, 409)

            created = client.post(
                f"/internal/v1/meetings/{meeting_id}/runtime",
                json={"meeting": {"status": "APPROVED"}},
            )
            self.assertEqual(created.status_code, 201)
            rejected_approval = client.put(
                f"/internal/v1/meetings/{meeting_id}/minutes",
                json={"status": "APPROVED", "document": {"title": "Draft"}},
            )
            self.assertEqual(rejected_approval.status_code, 409)

            stopped = client.post(f"/internal/v1/runtimes/{created.json()['runtime_session_id']}/stop")
            self.assertEqual(stopped.status_code, 200)
            approved = client.put(
                f"/internal/v1/meetings/{meeting_id}/minutes",
                json={"status": "APPROVED", "document": {"title": "Approved"}},
            )
            self.assertEqual(approved.status_code, 200)
            follow_up = client.put(
                f"/internal/v1/meetings/{meeting_id}/minutes",
                json={"document": {"title": "Follow up"}},
            )
            self.assertEqual(follow_up.status_code, 200)
            self.assertEqual(follow_up.json()["status"], "DRAFT")

    def test_minutes_editor_rejects_stale_revision(self) -> None:
        meeting_id = uuid4()
        with TestClient(app) as client:
            first = client.patch(
                f"/internal/v1/meetings/{meeting_id}/minutes",
                json={"base_revision": 0, "document": {"schema_version": 1, "meeting": {"title": "Demo", "started_at": None}, "summary": [], "topics": [], "source_segment_ids": []}},
            )
            self.assertEqual(first.status_code, 200)
            stale = client.patch(
                f"/internal/v1/meetings/{meeting_id}/minutes",
                json={"base_revision": 0, "document": {"schema_version": 1, "meeting": {"title": "Stale", "started_at": None}, "summary": [], "topics": [], "source_segment_ids": []}},
            )
            self.assertEqual(stale.status_code, 409)

    def test_minutes_review_and_approval_follow_lifecycle(self) -> None:
        meeting_id = uuid4()
        with TestClient(app) as client:
            created = client.post(
                f"/internal/v1/meetings/{meeting_id}/runtime",
                json={"meeting": {"status": "APPROVED"}},
            )
            self.assertEqual(created.status_code, 201)
            runtime_id = created.json()["runtime_session_id"]
            stopped = client.post(f"/internal/v1/runtimes/{runtime_id}/stop")
            self.assertEqual(stopped.status_code, 200)
            reviewed = client.post(f"/internal/v1/meetings/{meeting_id}/minutes/review")
            self.assertEqual(reviewed.status_code, 200)
            self.assertEqual(reviewed.json()["status"], "REVIEWING")
            approved = client.post(f"/internal/v1/meetings/{meeting_id}/minutes/approve")
            self.assertEqual(approved.status_code, 200)
            self.assertEqual(approved.json()["status"], "APPROVED")
            repeated = client.post(f"/internal/v1/meetings/{meeting_id}/minutes/approve")
            self.assertEqual(repeated.status_code, 409)

    def test_sql_repository_persists_external_meeting_id_without_fk(self) -> None:
        factory = create_session_factory("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(factory.kw["bind"])
        repository = SqlAlchemyRuntimeRepository(factory)
        meeting_id = uuid4()
        created = repository.create(meeting_id, {"meeting_id": str(meeting_id)})
        self.assertEqual(repository.create(meeting_id, {}).runtime_session_id, created.runtime_session_id)
        stopped = repository.set_status(created.runtime_session_id, RuntimeStatus.COMPLETED)
        self.assertEqual(stopped.status, RuntimeStatus.COMPLETED)

    def test_final_callback_commits_transcript_before_socket_emit(self) -> None:
        factory = create_session_factory("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(factory.kw["bind"])
        meeting_id = uuid4()
        event = {
            "event_id": str(uuid4()),
            "type": "transcript.final",
            "meeting_id": str(meeting_id),
            "runtime_session_id": str(uuid4()),
            "sequence": 1,
            "occurred_at": "2026-08-07T01:00:00Z",
            "payload": {"segment_id": "final-1", "content_text": "persist first"},
        }
        events = SqlAlchemyAIEventRepository(factory)
        content = SqlAlchemyMeetingContentRepository(factory)
        self.assertEqual(events.accept(event), "accepted")
        self.assertEqual(content.transcript(meeting_id)[0]["segment_id"], "final-1")
        self.assertEqual(events.accept(event), "duplicate")
        self.assertEqual(len(content.transcript(meeting_id)), 1)


if __name__ == "__main__":
    unittest.main()
