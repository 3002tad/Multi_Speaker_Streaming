from __future__ import annotations

import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from meeting_service.app.main import app
from meeting_service.app.config import settings


HEADERS = {"X-Service-Key": settings.service_key}


def _document(text: str = "Nội dung cuộc họp") -> dict:
    return {
        "schema_version": 1,
        "meeting": {"title": "P1-03", "started_at": None},
        "summary": [{"content": text, "source_segment_ids": ["seg-1"]}],
        "topics": [],
        "source_segment_ids": ["seg-1"],
    }


class MinutesLifecycleTests(unittest.TestCase):
    def test_manual_edit_uses_cas_and_approved_revision_is_immutable(self) -> None:
        meeting_id = uuid4()
        headers = {**HEADERS, "Idempotency-Key": f"p103-start-{meeting_id}"}
        with TestClient(app, headers=headers) as client:
            created = client.post(
                f"/internal/v1/meetings/{meeting_id}/runtime",
                json={"meeting": {"status": "ONGOING"}},
            )
            self.assertEqual(created.status_code, 201)
            runtime_id = created.json()["runtime_session_id"]
            first = client.put(
                f"/internal/v1/meetings/{meeting_id}/minutes",
                headers={"Idempotency-Key": f"p103-edit-{meeting_id}"},
                json={"base_revision": 0, "document": _document(), "status": "DRAFT"},
            )
            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.json()["revision"], 1)
            stale = client.put(
                f"/internal/v1/meetings/{meeting_id}/minutes",
                json={"base_revision": 0, "document": _document("Bản cũ")},
            )
            self.assertEqual(stale.status_code, 409)

            stopped = client.post(
                f"/internal/v1/runtimes/{runtime_id}/stop",
                headers={"Idempotency-Key": f"p103-stop-{meeting_id}"},
            )
            self.assertEqual(stopped.status_code, 200)
            reviewing = client.post(
                f"/internal/v1/meetings/{meeting_id}/minutes/review",
                headers={"Idempotency-Key": f"p103-review-{meeting_id}"},
            )
            self.assertEqual(reviewing.status_code, 200)
            approved = client.post(
                f"/internal/v1/meetings/{meeting_id}/minutes/approve",
                headers={"Idempotency-Key": f"p103-approve-{meeting_id}"},
            )
            self.assertEqual(approved.status_code, 200)
            self.assertEqual(approved.json()["status"], "APPROVED")

            illegal = client.put(
                f"/internal/v1/meetings/{meeting_id}/minutes",
                json={"base_revision": approved.json()["revision"], "document": _document(), "status": "APPROVED"},
            )
            self.assertEqual(illegal.status_code, 409)
            draft = client.put(
                f"/internal/v1/meetings/{meeting_id}/minutes",
                json={"base_revision": approved.json()["revision"], "document": _document("Bản sửa sau duyệt"), "status": "DRAFT"},
            )
            self.assertEqual(draft.status_code, 200)
            self.assertEqual(draft.json()["status"], "DRAFT")

    def test_stale_llm_callback_does_not_overwrite_manual_revision(self) -> None:
        meeting_id = uuid4()
        analysis_service = app.state.minutes_analysis
        previous_ai = analysis_service.ai_client

        class FakeAI:
            async def analyze_evidence(self, runtime_id: str, evidence: dict, key: str) -> dict:
                return {"status": "accepted"}

        analysis_service.ai_client = FakeAI()
        try:
            with TestClient(app, headers={**HEADERS, "Idempotency-Key": f"p103-start-{meeting_id}"}) as client:
                created = client.post(
                    f"/internal/v1/meetings/{meeting_id}/runtime",
                    json={"meeting": {"status": "ONGOING"}},
                )
                runtime_id = created.json()["runtime_session_id"]
                client.post(
                    f"/internal/v1/meetings/{meeting_id}/transcript",
                    json={
                        "segment_id": "seg-1",
                        "revision": 1,
                        "content_text": "Transcript gốc.",
                        "speaker": {"label": "Dat", "identity_method": "mic_fallback"},
                        "started_at": "2026-08-12T01:00:00Z",
                        "ended_at": "2026-08-12T01:00:02Z",
                    },
                )
                requested = client.post(
                    f"/internal/v1/meetings/{meeting_id}/minutes/analyze",
                    headers={"Idempotency-Key": f"p103-analysis-{meeting_id}"},
                )
                self.assertEqual(requested.status_code, 202)
                manual = client.put(
                    f"/internal/v1/meetings/{meeting_id}/minutes",
                    json={"base_revision": 0, "document": _document("Bản sửa tay")},
                )
                self.assertEqual(manual.status_code, 200)
                event = {
                    "schema_version": 1,
                    "event_id": str(uuid4()),
                    "type": "minutes.updated",
                    "meeting_id": str(meeting_id),
                    "runtime_session_id": runtime_id,
                    "occurred_at": "2026-08-12T01:01:00Z",
                    "sequence": 1,
                    "payload": {
                        "analysis_id": requested.json()["analysis_id"],
                        "generation_id": requested.json()["generation_id"],
                        "base_transcript_revision": requested.json()["base_transcript_revision"],
                        "base_minutes_revision": requested.json()["base_minutes_revision"],
                        "document": _document("Kết quả LLM cũ"),
                    },
                }
                callback = client.post("/internal/v1/ai-events", headers=HEADERS, json=event)
                self.assertEqual(callback.status_code, 200)
                self.assertEqual(callback.json(), {"status": "stale"})
                self.assertEqual(client.get(f"/internal/v1/meetings/{meeting_id}/minutes").json()["document"]["summary"][0]["content"], "Bản sửa tay")
                self.assertEqual(client.get(f"/internal/v1/meetings/{meeting_id}/minutes/analyze").json()["status"], "STALE")
        finally:
            analysis_service.ai_client = previous_ai

    def test_purge_keeps_storage_tombstone_for_retry(self) -> None:
        class FailingStorage:
            def __init__(self) -> None:
                self.objects: dict[str, bytes] = {}
                self.fail_delete = True

            def put(self, key: str, content: bytes, content_type: str) -> None:
                self.objects[key] = content

            def get(self, key: str) -> bytes:
                return self.objects[key]

            def delete(self, key: str) -> None:
                if self.fail_delete:
                    raise RuntimeError("temporary storage outage")
                self.objects.pop(key, None)

        meeting_id = uuid4()
        storage = FailingStorage()
        previous_storage = app.state.object_storage
        app.state.object_storage = storage
        try:
            with TestClient(app, headers={**HEADERS, "Idempotency-Key": f"p103-start-{meeting_id}"}) as client:
                created = client.post(
                    f"/internal/v1/meetings/{meeting_id}/runtime",
                    json={"meeting": {"status": "ONGOING"}},
                )
                runtime_id = created.json()["runtime_session_id"]
                client.post(f"/internal/v1/runtimes/{runtime_id}/stop", headers={"Idempotency-Key": f"p103-stop-{meeting_id}"})
                client.put(
                    f"/internal/v1/meetings/{meeting_id}/minutes",
                    json={"base_revision": 0, "status": "APPROVED", "document": {"schema_version": 1, "meeting": {"title": "P1-03", "started_at": None}, "summary": [], "topics": [], "source_segment_ids": []}},
                )
                exported = client.post(f"/internal/v1/meetings/{meeting_id}/minutes/exports/docx", json={})
                self.assertEqual(exported.status_code, 201)
                first = client.delete(f"/internal/v1/meetings/{meeting_id}", headers={"Idempotency-Key": f"p103-purge-{meeting_id}"})
                self.assertEqual(first.status_code, 200)
                self.assertEqual(first.json()["purge_cleanup_status"], "PENDING")
                self.assertEqual(first.json()["pending_storage_objects"], 1)
                storage.fail_delete = False
                second = client.delete(f"/internal/v1/meetings/{meeting_id}", headers={"Idempotency-Key": f"p103-purge-retry-{meeting_id}"})
                self.assertEqual(second.status_code, 200)
                self.assertEqual(second.json()["purge_cleanup_status"], "COMPLETED")
                self.assertEqual(second.json()["pending_storage_objects"], 0)
        finally:
            app.state.object_storage = previous_storage

    def test_export_db_failure_removes_uploaded_object(self) -> None:
        class Storage:
            def __init__(self) -> None:
                self.objects: dict[str, bytes] = {}

            def put(self, key: str, content: bytes, content_type: str) -> None:
                self.objects[key] = content

            def get(self, key: str) -> bytes:
                return self.objects[key]

            def delete(self, key: str) -> None:
                self.objects.pop(key, None)

        meeting_id = uuid4()
        storage = Storage()
        previous_storage = app.state.object_storage
        app.state.object_storage = storage
        try:
            with TestClient(app, headers={**HEADERS, "Idempotency-Key": f"p103-start-{meeting_id}"}) as client:
                created = client.post(
                    f"/internal/v1/meetings/{meeting_id}/runtime",
                    json={"meeting": {"status": "ONGOING"}},
                )
                runtime_id = created.json()["runtime_session_id"]
                client.post(f"/internal/v1/runtimes/{runtime_id}/stop", headers={"Idempotency-Key": f"p103-stop-{meeting_id}"})
                client.put(
                    f"/internal/v1/meetings/{meeting_id}/minutes",
                    json={"base_revision": 0, "status": "APPROVED", "document": {"schema_version": 1, "meeting": {"title": "P1-03", "started_at": None}, "summary": [], "topics": [], "source_segment_ids": []}},
                )
                with patch.object(app.state.content_store, "create_export", side_effect=RuntimeError("db commit failed")):
                    failed = client.post(f"/internal/v1/meetings/{meeting_id}/minutes/exports/docx", json={})
                self.assertEqual(failed.status_code, 502)
                self.assertEqual(storage.objects, {})
        finally:
            app.state.object_storage = previous_storage


if __name__ == "__main__":
    unittest.main()
