from __future__ import annotations

import unittest
import asyncio
import tempfile
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from meeting_service.app.main import app
from meeting_service.app.domain.models import RuntimeStatus
from meeting_service.app.infrastructure.database import Base, create_session_factory
from meeting_service.app.infrastructure.repositories import SqlAlchemyAIEventRepository, SqlAlchemyRuntimeRepository
from meeting_service.app.application.meeting_content import SqlAlchemyMeetingContentRepository
from meeting_service.app.application.minutes_analysis import MinutesAnalysisService
from meeting_service.app.infrastructure.runtime_store import InMemoryRuntimeStore
from meeting_service.app.application.runtime_service import RuntimeService
from meeting_service.app.config import settings


IDEMPOTENCY_HEADERS = {
    "X-Service-Key": settings.service_key,
    "Idempotency-Key": "test-idempotency-key",
}


class MeetingServiceSkeletonTests(unittest.TestCase):
    def test_p1_01_evidence_revision_changes_without_segment_revision_collision(self) -> None:
        meeting_id = uuid4()
        runtime_id = uuid4()
        common = {
            "meeting_id": meeting_id,
            "runtime_session_id": runtime_id,
            "meeting_snapshot": {"meeting": {"title": "Kiểm tra revision"}},
            "previous_document": None,
        }
        first, _ = MinutesAnalysisService.build_evidence(
            **common,
            transcript=[
                {
                    "segment_id": "one",
                    "revision": 2,
                    "content_text": "Một đoạn đã chỉnh sửa.",
                    "started_at": "2026-08-12T01:00:00Z",
                    "ended_at": "2026-08-12T01:00:02Z",
                }
            ],
        )
        second, _ = MinutesAnalysisService.build_evidence(
            **common,
            transcript=[
                {
                    "segment_id": "one",
                    "revision": 1,
                    "content_text": "Một đoạn ban đầu.",
                    "started_at": "2026-08-12T01:00:00Z",
                    "ended_at": "2026-08-12T01:00:02Z",
                },
                {
                    "segment_id": "two",
                    "revision": 1,
                    "content_text": "Đoạn thứ hai.",
                    "started_at": "2026-08-12T01:00:03Z",
                    "ended_at": "2026-08-12T01:00:05Z",
                },
            ],
        )
        self.assertNotEqual(first, second)

    def test_p1_01_minutes_analysis_uses_final_evidence_without_exposing_it(self) -> None:
        class FakeAI:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict, str]] = []

            async def analyze_evidence(self, runtime_id: str, evidence: dict, key: str) -> dict:
                self.calls.append((runtime_id, evidence, key))
                return {
                    "status": "accepted",
                    "runtime_session_id": runtime_id,
                    "analysis_id": evidence["analysis_id"],
                    "generation_id": evidence["generation_id"],
                }

        meeting_id = uuid4()
        fake = FakeAI()
        analysis = app.state.minutes_analysis
        previous_ai = analysis.ai_client
        analysis.ai_client = fake
        try:
            with TestClient(app, headers=IDEMPOTENCY_HEADERS) as client:
                runtime = client.post(
                    f"/internal/v1/meetings/{meeting_id}/runtime",
                    json={
                        "meeting": {
                            "status": "ONGOING",
                            "title": "P1 evidence test",
                            "started_at": "2026-08-12T01:00:00Z",
                        },
                    },
                )
                self.assertEqual(runtime.status_code, 201)
                client.post(
                    f"/internal/v1/meetings/{meeting_id}/transcript",
                    json={
                        "segment_id": "p1-final-1",
                        "revision": 1,
                        "content_text": "Nội dung transcript final.",
                        "speaker": {"label": "Dat", "ecabinet_user_id": str(uuid4())},
                        "started_at": "2026-08-12T01:00:01Z",
                        "ended_at": "2026-08-12T01:00:03Z",
                    },
                )
                requested = client.post(
                    f"/internal/v1/meetings/{meeting_id}/minutes/analyze",
                    headers=IDEMPOTENCY_HEADERS,
                )
                self.assertEqual(requested.status_code, 202)
                self.assertEqual(requested.json()["status"], "RUNNING")
                self.assertNotIn("evidence", requested.json())
                self.assertEqual(len(fake.calls), 1)
                evidence = fake.calls[0][1]
                self.assertEqual(evidence["meeting"]["title"], "P1 evidence test")
                self.assertEqual(evidence["segments"][0]["content_text"], "Nội dung transcript final.")
                self.assertNotIn("database_url", evidence)

                status = client.get(f"/internal/v1/meetings/{meeting_id}/minutes/analyze")
                self.assertEqual(status.json()["status"], "RUNNING")
                self.assertNotIn("evidence", status.json())
                repeated = client.post(
                    f"/internal/v1/meetings/{meeting_id}/minutes/analyze",
                    headers=IDEMPOTENCY_HEADERS,
                )
                self.assertEqual(repeated.status_code, 202)
                self.assertEqual(len(fake.calls), 1)
        finally:
            analysis.ai_client = previous_ai

    def test_p1_01_minutes_analysis_rejects_empty_transcript(self) -> None:
        meeting_id = uuid4()
        with TestClient(app, headers=IDEMPOTENCY_HEADERS) as client:
            created = client.post(
                f"/internal/v1/meetings/{meeting_id}/runtime",
                json={"meeting": {"status": "ONGOING"}},
            )
            self.assertEqual(created.status_code, 201)
            response = client.post(f"/internal/v1/meetings/{meeting_id}/minutes/analyze")
            self.assertEqual(response.status_code, 409)

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
            payload={
                "segment_id": "seg-1",
                "source_identity": "mic-a",
                "speaker": {"label": "Dat", "identity_method": "mic_fallback"},
                "raw_text": "xin chao",
                "content_text": "Xin chào.",
                "started_at": "2026-08-05T00:00:00Z",
                "ended_at": "2026-08-05T00:00:02Z",
                "revision": 1,
            },
        )

        async def scenario() -> None:
            with patch("meeting_service.app.api.ai_events.sio.emit", new_callable=AsyncMock) as emit:
                result = await receive_ai_event(type("Request", (), {"app": type("App", (), {"state": type("State", (), {})()})()})(), event)
                self.assertEqual(result, {"status": "accepted"})
                emit.assert_awaited_once()
                self.assertEqual(emit.await_args.kwargs["room"], f"meeting:{event.meeting_id}")

        import asyncio
        asyncio.run(scenario())

    def test_p0_05_callback_upserts_revisions_and_retraction(self) -> None:
        meeting_id = uuid4()
        with TestClient(app, headers=IDEMPOTENCY_HEADERS) as client:
            created = client.post(
                f"/internal/v1/meetings/{meeting_id}/runtime",
                json={"meeting": {"status": "ONGOING"}},
            )
            self.assertEqual(created.status_code, 201)
            runtime_id = created.json()["runtime_session_id"]
            callback_headers = {"X-Service-Key": settings.service_key}
            base = {
                "schema_version": 1,
                "event_id": str(uuid4()),
                "meeting_id": str(meeting_id),
                "runtime_session_id": runtime_id,
                "occurred_at": "2026-08-10T01:00:00Z",
            }

            def send(event_type: str, sequence: int, revision: int, payload: dict) -> dict:
                event = {
                    **base,
                    "event_id": str(uuid4()),
                    "type": event_type,
                    "sequence": sequence,
                    "payload": {"segment_id": "p0-05", "revision": revision, **payload},
                }
                response = client.post(
                    "/internal/v1/ai-events",
                    headers=callback_headers,
                    json=event,
                )
                self.assertEqual(response.status_code, 200)
                return response.json()

            partial = send(
                "transcript.partial",
                1,
                1,
                {
                    "source_identity": "mic-a",
                    "speaker": {"label": "Dat", "identity_method": "mic_fallback"},
                    "content_text": "bản nháp",
                },
            )
            self.assertEqual(partial, {"status": "accepted"})
            final_payload = {
                "source_identity": "mic-a",
                "speaker": {"label": "Dat", "identity_method": "mic_fallback"},
                "raw_text": "ban nhap",
                "content_text": "Bản nháp.",
                "started_at": "2026-08-10T01:00:00Z",
                "ended_at": "2026-08-10T01:00:02Z",
            }
            final = send("transcript.final", 2, 2, final_payload)
            self.assertEqual(final, {"status": "accepted"})
            updated = send(
                "transcript.updated",
                3,
                3,
                {"content_text": "Bản nháp đã hiệu chỉnh."},
            )
            self.assertEqual(updated, {"status": "accepted"})
            hydrated = client.get(
                f"/internal/v1/meetings/{meeting_id}/transcript",
                headers=callback_headers,
            )
            self.assertEqual(hydrated.status_code, 200)
            self.assertEqual(
                hydrated.json()["segments"][0]["content_text"],
                "Bản nháp đã hiệu chỉnh.",
            )

            # Retrying the exact envelope is idempotent even after a client
            # lost the response. Rebuild the same event to exercise the API
            # duplicate path rather than merely sending a stale sequence.
            duplicate_event = {
                **base,
                "event_id": str(uuid4()),
                "type": "transcript.final",
                "sequence": 4,
                "payload": {"segment_id": "p0-05", "revision": 4, **final_payload},
            }
            response = client.post(
                "/internal/v1/ai-events",
                headers=callback_headers,
                json=duplicate_event,
            )
            self.assertEqual(response.json(), {"status": "accepted"})
            duplicate_retry = client.post(
                "/internal/v1/ai-events",
                headers=callback_headers,
                json=duplicate_event,
            )
            self.assertEqual(duplicate_retry.json(), {"status": "duplicate"})

            stale = send(
                "transcript.updated",
                3,
                5,
                {"content_text": "không được nhận"},
            )
            self.assertEqual(stale, {"status": "stale"})
            retracted = send(
                "transcript.retracted",
                5,
                5,
                {"reason": "segment bị thay thế"},
            )
            self.assertEqual(retracted, {"status": "accepted"})
            transcript = client.get(
                f"/internal/v1/meetings/{meeting_id}/transcript",
                headers=callback_headers,
            )
            self.assertEqual(transcript.status_code, 200)
            self.assertEqual(transcript.json()["segments"], [])

    def test_p0_05_callback_rejects_unknown_or_terminal_runtime(self) -> None:
        meeting_id = uuid4()
        callback_headers = {"X-Service-Key": settings.service_key}
        with TestClient(app, headers=IDEMPOTENCY_HEADERS) as client:
            event = {
                "schema_version": 1,
                "event_id": str(uuid4()),
                "type": "transcript.final",
                "meeting_id": str(meeting_id),
                "runtime_session_id": str(uuid4()),
                "occurred_at": "2026-08-10T01:00:00Z",
                "sequence": 1,
                "payload": {
                    "segment_id": "unknown-runtime",
                    "source_identity": "mic-a",
                    "speaker": {"label": "Dat", "identity_method": "mic_fallback"},
                    "raw_text": "raw",
                    "content_text": "final",
                    "started_at": "2026-08-10T01:00:00Z",
                    "ended_at": "2026-08-10T01:00:01Z",
                    "revision": 1,
                },
            }
            response = client.post("/internal/v1/ai-events", headers=callback_headers, json=event)
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json()["code"], "HTTP_404")

            created = client.post(
                f"/internal/v1/meetings/{meeting_id}/runtime",
                json={"meeting": {"status": "ONGOING"}},
            )
            runtime_id = created.json()["runtime_session_id"]
            stopped = client.post(f"/internal/v1/runtimes/{runtime_id}/stop")
            self.assertEqual(stopped.status_code, 200)
            event["event_id"] = str(uuid4())
            event["runtime_session_id"] = runtime_id
            response = client.post("/internal/v1/ai-events", headers=callback_headers, json=event)
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["code"], "HTTP_409")

    def test_health_endpoints(self) -> None:
        with TestClient(app, headers=IDEMPOTENCY_HEADERS) as client:
            self.assertEqual(client.get("/health/live").status_code, 200)
            self.assertEqual(client.get("/health/ready").json()["status"], "ok")

    def test_runtime_and_empty_minutes_follow_contract_response_shapes(self) -> None:
        meeting_id = uuid4()
        with TestClient(app, headers=IDEMPOTENCY_HEADERS) as client:
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
        with TestClient(app, headers=IDEMPOTENCY_HEADERS) as client:
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
        with TestClient(app, headers=IDEMPOTENCY_HEADERS) as client:
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
        with TestClient(app, headers=IDEMPOTENCY_HEADERS) as client:
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
        with TestClient(app, headers=IDEMPOTENCY_HEADERS) as client:
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
        with TestClient(app, headers=IDEMPOTENCY_HEADERS) as client:
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
        with TestClient(app, headers=IDEMPOTENCY_HEADERS) as client:
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
        with TestClient(app, headers=IDEMPOTENCY_HEADERS) as client:
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
        with TestClient(app, headers=IDEMPOTENCY_HEADERS) as client:
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

    def test_sql_repository_persists_idempotency_and_rejects_hash_reuse(self) -> None:
        factory = create_session_factory("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(factory.kw["bind"])
        repository = SqlAlchemyRuntimeRepository(factory)
        response = {"runtime_session_id": str(uuid4()), "status": "STARTING"}
        self.assertEqual(
            repository.put_idempotency("start:meeting", "sql-key-1", "hash-a", response),
            response,
        )
        self.assertEqual(repository.get_idempotency("start:meeting", "sql-key-1", "hash-a"), response)
        with self.assertRaises(ValueError):
            repository.get_idempotency("start:meeting", "sql-key-1", "hash-b")

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

    def test_p0_05_sql_callback_persists_before_emit_and_survives_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_url = f"sqlite+pysqlite:///{directory}/meeting.db"
            factory = create_session_factory(database_url)
            Base.metadata.create_all(factory.kw["bind"])
            meeting_id = uuid4()
            runtime_repo = SqlAlchemyRuntimeRepository(factory)
            runtime = runtime_repo.create(meeting_id, {"meeting": {"status": "ONGOING"}})
            events = SqlAlchemyAIEventRepository(factory)
            content = SqlAlchemyMeetingContentRepository(factory)
            event = {
                "schema_version": 1,
                "event_id": str(uuid4()),
                "type": "transcript.final",
                "meeting_id": str(meeting_id),
                "runtime_session_id": str(runtime.runtime_session_id),
                "sequence": 1,
                "occurred_at": "2026-08-10T01:00:00Z",
                "payload": {
                    "segment_id": "durable-final",
                    "source_identity": "mic-a",
                    "speaker": {"label": "Dat", "identity_method": "mic_fallback"},
                    "raw_text": "raw",
                    "content_text": "Persist trước emit.",
                    "started_at": "2026-08-10T01:00:00Z",
                    "ended_at": "2026-08-10T01:00:01Z",
                    "revision": 1,
                },
            }
            from meeting_service.app.api.ai_events import AIEvent, receive_ai_event

            request = type(
                "Request",
                (),
                {
                    "app": type(
                        "App",
                        (),
                        {
                            "state": type(
                                "State",
                                (),
                                {
                                    "runtime_service": RuntimeService(runtime_repo),
                                    "ai_event_repository": events,
                                    "content_store": content,
                                },
                            )(),
                        },
                    )(),
                },
            )()

            async def emit_after_persist(*args, **kwargs) -> None:
                self.assertEqual(content.transcript(meeting_id)[0]["segment_id"], "durable-final")

            async def scenario() -> None:
                with patch("meeting_service.app.api.ai_events.sio.emit", new=emit_after_persist):
                    result = await receive_ai_event(request, AIEvent(**event))
                    self.assertEqual(result, {"status": "accepted"})

            asyncio.run(scenario())
            reopened_factory = create_session_factory(database_url)
            reopened_content = SqlAlchemyMeetingContentRepository(reopened_factory)
            reopened_events = SqlAlchemyAIEventRepository(reopened_factory)
            self.assertEqual(reopened_content.transcript(meeting_id)[0]["content_text"], "Persist trước emit.")
            self.assertTrue(reopened_events.has_event(UUID(event["event_id"])))

    def test_p0_04_concurrent_start_calls_one_ai_side_effect(self) -> None:
        class SlowAI:
            def __init__(self) -> None:
                self.created = 0

            async def create_session(self, payload: dict, idempotency_key: str) -> dict:
                self.created += 1
                await asyncio.sleep(0.01)
                return {"status": "READY"}

            async def stop_session(self, runtime_session_id: str, idempotency_key: str) -> dict:
                return {"status": "COMPLETED"}

        async def scenario() -> None:
            ai = SlowAI()
            service = RuntimeService(ai_client=ai)
            meeting_id = uuid4()
            results = await asyncio.gather(
                service.start(meeting_id, {"meeting": {"status": "ONGOING"}}, "start-key-a"),
                service.start(meeting_id, {"meeting": {"status": "ONGOING"}}, "start-key-b"),
            )
            self.assertEqual(results[0].runtime_session_id, results[1].runtime_session_id)
            self.assertEqual(ai.created, 1)

        asyncio.run(scenario())

    def test_p0_04_retry_after_ai_timeout_reuses_runtime_and_can_succeed(self) -> None:
        class FlakyAI:
            def __init__(self) -> None:
                self.created = 0

            async def create_session(self, payload: dict, idempotency_key: str) -> dict:
                self.created += 1
                if self.created == 1:
                    raise TimeoutError("simulated AI timeout")
                return {"status": "READY"}

            async def stop_session(self, runtime_session_id: str, idempotency_key: str) -> dict:
                return {"status": "COMPLETED"}

        async def scenario() -> None:
            ai = FlakyAI()
            service = RuntimeService(ai_client=ai)
            meeting_id = uuid4()
            with self.assertRaises(TimeoutError):
                await service.start(meeting_id, {"meeting": {"status": "ONGOING"}}, "retry-key-1")
            retried = await service.start(meeting_id, {"meeting": {"status": "ONGOING"}}, "retry-key-1")
            self.assertEqual(retried.status, RuntimeStatus.READY)
            self.assertEqual(ai.created, 2)

        asyncio.run(scenario())

    def test_p0_04_repeated_stop_does_not_call_ai_twice(self) -> None:
        class StopAI:
            def __init__(self) -> None:
                self.stopped = 0

            async def create_session(self, payload: dict, idempotency_key: str) -> dict:
                return {"status": "READY"}

            async def stop_session(self, runtime_session_id: str, idempotency_key: str) -> dict:
                self.stopped += 1
                await asyncio.sleep(0.01)
                return {"status": "COMPLETED"}

        async def scenario() -> None:
            ai = StopAI()
            service = RuntimeService(ai_client=ai)
            session = await service.start(uuid4(), {"meeting": {"status": "ONGOING"}}, "start-stop-key")
            stopped = await asyncio.gather(
                service.stop(session.runtime_session_id, "stop-key-a"),
                service.stop(session.runtime_session_id, "stop-key-b"),
            )
            self.assertEqual(ai.stopped, 1)
            self.assertEqual({item.status for item in stopped}, {RuntimeStatus.COMPLETED})
            replay = await service.stop(session.runtime_session_id, "stop-key-a")
            self.assertEqual(replay.status, RuntimeStatus.COMPLETED)
            self.assertEqual(ai.stopped, 1)

        asyncio.run(scenario())

    def test_p0_04_internal_key_required_and_reuse_conflict_is_rejected(self) -> None:
        meeting_id = uuid4()
        with TestClient(app, headers={"X-Service-Key": settings.service_key}) as client:
            missing = client.post(
                f"/internal/v1/meetings/{meeting_id}/runtime",
                json={"meeting": {"status": "ONGOING"}},
            )
            self.assertEqual(missing.status_code, 422)
            self.assertEqual(missing.json()["code"], "HTTP_422")
            key = "p0-04-start-key"
            first = client.post(
                f"/internal/v1/meetings/{meeting_id}/runtime",
                json={"meeting": {"status": "ONGOING"}},
                headers={"Idempotency-Key": key},
            )
            replay = client.post(
                f"/internal/v1/meetings/{meeting_id}/runtime",
                json={"meeting": {"status": "ONGOING"}},
                headers={"Idempotency-Key": key},
            )
            self.assertEqual(first.status_code, 201)
            self.assertEqual(replay.status_code, 201)
            self.assertEqual(replay.json(), first.json())
            conflict = client.post(
                f"/internal/v1/meetings/{meeting_id}/runtime",
                json={"meeting": {"status": "APPROVED"}},
                headers={"Idempotency-Key": key},
            )
            self.assertEqual(conflict.status_code, 409)


if __name__ == "__main__":
    unittest.main()
