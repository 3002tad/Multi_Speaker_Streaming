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
from meeting_service.app.config import settings


class MeetingServiceSkeletonTests(unittest.TestCase):
    def test_internal_api_requires_service_key(self) -> None:
        meeting_id = uuid4()
        with TestClient(app) as client:
            self.assertEqual(client.get(f"/internal/v1/meetings/{meeting_id}/status").status_code, 401)
            self.assertEqual(
                client.get(
                    f"/internal/v1/meetings/{meeting_id}/status",
                    headers={"X-Service-Key": "wrong-key"},
                ).status_code,
                401,
            )
            self.assertEqual(client.get("/health/live").status_code, 200)

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
        with TestClient(app, headers={"X-Service-Key": settings.service_key}) as client:
            self.assertEqual(client.get("/health/live").status_code, 200)
            self.assertEqual(client.get("/health/ready").json()["status"], "ok")

    def test_runtime_and_empty_minutes_follow_contract_response_shapes(self) -> None:
        meeting_id = uuid4()
        with TestClient(app, headers={"X-Service-Key": settings.service_key}) as client:
            created = client.post(
                f"/internal/v1/meetings/{meeting_id}/runtime",
                json={"meeting": {"status": "APPROVED"}},
            )
            self.assertEqual(created.status_code, 201)
            runtime = created.json()
            self.assertEqual(runtime["schema_version"], 1)
            self.assertIn("created_at", runtime)

            minutes = client.get(f"/internal/v1/meetings/{meeting_id}/minutes")
            self.assertEqual(minutes.status_code, 200)
            document = minutes.json()["document"]
            self.assertEqual(document["schema_version"], 1)
            self.assertEqual(document["meeting"]["title"], "Biên bản cuộc họp")
            self.assertEqual(document["source_segment_ids"], [])

    def test_runtime_lifecycle_is_service_local(self) -> None:
        meeting_id = uuid4()
        with TestClient(app, headers={"X-Service-Key": settings.service_key}) as client:
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
        with TestClient(app, headers={"X-Service-Key": settings.service_key}) as client:
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

    def test_transcript_contract_uses_the_canonical_singular_route(self) -> None:
        meeting_id = uuid4()
        with TestClient(app, headers={"X-Service-Key": settings.service_key}) as client:
            appended = client.post(
                f"/internal/v1/meetings/{meeting_id}/transcript",
                json={"segment_id": "contract-1", "text": "canonical"},
            )
            self.assertEqual(appended.status_code, 201)
            response = client.get(f"/internal/v1/meetings/{meeting_id}/transcript")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["meeting_id"], str(meeting_id))
            self.assertEqual(response.json()["segments"][0]["text"], "canonical")
            self.assertEqual(
                client.get(f"/internal/v1/meetings/{meeting_id}/transcripts").status_code,
                404,
            )

    def test_snapshot_contract_updates_revision_and_rejects_stale(self) -> None:
        meeting_id = uuid4()
        base = {
            "schema_version": 1,
            "snapshot_revision": 1,
            "meeting_id": str(meeting_id),
            "meeting": {"title": "Contract test", "status": "ONGOING", "started_at": None, "ended_at": None},
            "actor": {"user_id": str(uuid4()), "display_name": "Chair", "role": "CHAIRPERSON", "permissions": ["CONTROL"]},
            "participants": [],
            "hotwords": [],
        }
        with TestClient(app, headers={"X-Service-Key": settings.service_key}) as client:
            created = client.post(f"/internal/v1/meetings/{meeting_id}/runtime", json=base)
            self.assertEqual(created.status_code, 201)
            updated = {**base, "snapshot_revision": 2, "participants": [{"user_id": str(uuid4()), "display_name": "Member", "role": "MEMBER"}]}
            response = client.put(f"/internal/v1/meetings/{meeting_id}/snapshot", json=updated)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["snapshot_revision"], 2)
            self.assertEqual(len(response.json()["snapshot"]["participants"]), 1)
            stale = client.put(f"/internal/v1/meetings/{meeting_id}/snapshot", json=updated)
            self.assertEqual(stale.status_code, 409)
            self.assertEqual(stale.headers["content-type"], "application/problem+json")
            self.assertEqual(stale.json()["code"], "HTTP_409")

    def test_internal_errors_follow_problem_contract(self) -> None:
        with TestClient(app, headers={"X-Service-Key": settings.service_key}) as client:
            response = client.get(f"/internal/v1/meetings/{uuid4()}/status")
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.headers["content-type"], "application/problem+json")
            self.assertIn("correlation_id", response.json())
            invalid = client.put(f"/internal/v1/meetings/{uuid4()}/snapshot", json=[])
            self.assertEqual(invalid.status_code, 422)
            self.assertEqual(invalid.headers["content-type"], "application/problem+json")
            self.assertEqual(invalid.json()["code"], "HTTP_422")

    def test_state_guard_blocks_invalid_runtime_start_and_early_minutes_approval(self) -> None:
        meeting_id = uuid4()
        with TestClient(app, headers={"X-Service-Key": settings.service_key}) as client:
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
        with TestClient(app, headers={"X-Service-Key": settings.service_key}) as client:
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
        with TestClient(app, headers={"X-Service-Key": settings.service_key}) as client:
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
