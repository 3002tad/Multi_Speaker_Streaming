import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from backend.api import main
from backend.api.database import TranscriptRepository
from backend.api.main import app


class BackendSmokeTests(unittest.TestCase):
    def test_health_and_meeting_metadata(self) -> None:
        with TestClient(app) as client:
            health = client.get("/api/health")
            self.assertEqual(health.status_code, 200)
            self.assertEqual(health.json()["status"], "ok")

            meeting = client.get("/api/meeting")
            self.assertEqual(meeting.status_code, 200)
            self.assertTrue(meeting.json()["room"])

    def test_create_and_join_single_room(self) -> None:
        test_settings = replace(
            main.settings,
            livekit_api_key="demo-key",
            livekit_api_secret="demo-secret",
            minutes_composer_enabled=False,
        )
        temporary_directory = TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        test_repository = TranscriptRepository(
            Path(temporary_directory.name) / "meeting.db"
        )
        test_repository.upsert(
            {
                "segment_id": "old-segment",
                "meeting_id": test_settings.meeting_room,
                "speaker": "Phiên cũ",
                "raw_text": "nội dung cũ",
                "text": "Nội dung cũ.",
                "start_time": 1,
                "end_time": 2,
                "created_at": 1,
            }
        )
        with (
            patch("backend.api.main.settings", test_settings),
            patch("backend.api.main.repository", test_repository),
            TestClient(app) as client,
        ):
            created = client.post(
                "/api/meeting/create",
                json={"host_name": "Chủ trì"},
            )
            self.assertEqual(created.status_code, 200)
            room_code = created.json()["meeting_code"]
            self.assertEqual(created.json()["role"], "host")
            self.assertTrue(created.json()["token"])
            self.assertIn("reset_at", created.json())
            self.assertEqual(
                client.get("/api/transcripts").json()["items"], []
            )

            joined = client.post(
                "/api/meeting/join",
                json={
                    "display_name": "Thành viên",
                    "meeting_code": room_code,
                },
            )
            self.assertEqual(joined.status_code, 200)
            self.assertEqual(joined.json()["role"], "participant")
            self.assertTrue(joined.json()["token"])

    def test_host_title_prepares_dictionary_without_blocking_room(self) -> None:
        test_settings = replace(
            main.settings,
            livekit_api_key="demo-key",
            livekit_api_secret="demo-secret",
        )
        prepared = AsyncMock(
            return_value={"status": "ready", "hotword_count": 2}
        )
        with (
            patch("backend.api.main.settings", test_settings),
            patch("backend.api.main._prepare_adaptive_dictionary", prepared),
            TestClient(app) as client,
        ):
            created = client.post(
                "/api/meeting/create",
                json={
                    "host_name": "Chủ trì",
                    "meeting_title": "Triển khai VNPT SmartCA",
                },
            )

        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["meeting_title"], "Triển khai VNPT SmartCA")
        self.assertEqual(created.json()["adaptive_dictionary"]["status"], "ready")
        prepared.assert_awaited_once_with("Triển khai VNPT SmartCA")

    def test_internal_transcript_event_requires_key(self) -> None:
        test_settings = replace(
            main.settings,
            internal_api_key="internal-test-key",
            minutes_composer_enabled=False,
        )
        temporary_directory = TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        test_repository = TranscriptRepository(
            Path(temporary_directory.name) / "meeting.db"
        )
        with (
            patch("backend.api.main.settings", test_settings),
            patch("backend.api.main.repository", test_repository),
            TestClient(app) as client,
        ):
            denied = client.post(
                "/api/internal/events",
                json={
                    "payload": {
                        "type": "transcript.partial",
                        "source_id": "p1",
                        "speaker": "Người 1",
                        "text": "xin chào",
                    }
                },
            )
            self.assertEqual(denied.status_code, 403)

            accepted = client.post(
                "/api/internal/events",
                headers={"X-Internal-Api-Key": "internal-test-key"},
                json={
                    "payload": {
                        "type": "transcript.final",
                        "segment_id": "seg-refinement",
                        "source_id": "p1",
                        "speaker": "Người 1",
                        "text": "xin chào cuộc họp",
                    }
                },
            )
            self.assertEqual(accepted.status_code, 200)

            refined = client.post(
                "/api/internal/events",
                headers={"X-Internal-Api-Key": "internal-test-key"},
                json={
                    "payload": {
                        "type": "transcript.final",
                        "segment_id": "seg-refinement",
                        "source_id": "p1",
                        "speaker": "Người 1",
                        "raw_text": "xin chào cuộc họp",
                        "text": "Xin chào cuộc họp.",
                        "revision": 2,
                    }
                },
            )
            self.assertEqual(refined.json()["status"], "updated")

            transcript = client.get("/api/transcripts")
            self.assertEqual(transcript.status_code, 200)
            self.assertTrue(
                any(
                    item["text"] == "Xin chào cuộc họp."
                    for item in transcript.json()["items"]
                )
            )


    def test_cross_mic_duplicate_keeps_stronger_signal(self) -> None:
        test_settings = replace(
            main.settings,
            internal_api_key="internal-test-key",
            minutes_composer_enabled=False,
        )
        temporary_directory = TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        test_repository = TranscriptRepository(
            Path(temporary_directory.name) / "meeting.db"
        )
        headers = {"X-Internal-Api-Key": "internal-test-key"}
        base_payload = {
            "type": "transcript.final",
            "speaker": "Speaker",
            "raw_text": "he thong phong hop khong giay hom nay",
            "text": "He thong phong hop khong giay hom nay.",
            "start_time": 100.0,
            "end_time": 108.0,
        }

        with (
            patch("backend.api.main.settings", test_settings),
            patch("backend.api.main.repository", test_repository),
            TestClient(app) as client,
        ):
            weak = client.post(
                "/api/internal/events",
                headers=headers,
                json={
                    "payload": {
                        **base_payload,
                        "segment_id": "weak",
                        "source_id": "mic-b",
                        "signal_rms": 0.01,
                    }
                },
            )
            self.assertEqual(weak.status_code, 200)

            strong = client.post(
                "/api/internal/events",
                headers=headers,
                json={
                    "payload": {
                        **base_payload,
                        "segment_id": "strong",
                        "source_id": "mic-a",
                        "signal_rms": 0.04,
                    }
                },
            )
            self.assertEqual(strong.status_code, 200)

            items = client.get("/api/transcripts").json()["items"]
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["segment_id"], "strong")

    def test_global_turn_keeps_distinct_overlapping_speech(self) -> None:
        test_settings = replace(
            main.settings,
            internal_api_key="internal-test-key",
            minutes_composer_enabled=False,
        )
        temporary_directory = TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        test_repository = TranscriptRepository(
            Path(temporary_directory.name) / "meeting.db"
        )
        headers = {"X-Internal-Api-Key": "internal-test-key"}
        with (
            patch("backend.api.main.settings", test_settings),
            patch("backend.api.main.repository", test_repository),
            TestClient(app) as client,
        ):
            for segment_id, turn_id, source_id, text in (
                (
                    "turn-a",
                    "global-a",
                    "mic-a",
                    "ngân sách dự án cần phê duyệt",
                ),
                (
                    "turn-b",
                    "global-b",
                    "mic-b",
                    "thời hạn triển khai cần thống nhất",
                ),
            ):
                response = client.post(
                    "/api/internal/events",
                    headers=headers,
                    json={
                        "payload": {
                            "type": "transcript.final",
                            "segment_id": segment_id,
                            "source_id": source_id,
                            "global_turn_id": turn_id,
                            "raw_text": text,
                            "text": text,
                            "start_time": 100.0,
                            "end_time": 108.0,
                            "signal_rms": 0.04,
                        }
                    },
                )
                self.assertEqual(response.status_code, 200)
            self.assertEqual(
                len(client.get("/api/transcripts").json()["items"]),
                2,
            )

    def test_minutes_are_versioned_and_require_transcript_sources(self) -> None:
        test_settings = replace(
            main.settings,
            internal_api_key="internal-test-key",
            minutes_composer_enabled=False,
        )
        temporary_directory = TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        test_repository = TranscriptRepository(
            Path(temporary_directory.name) / "meeting.db"
        )
        headers = {"X-Internal-Api-Key": "internal-test-key"}
        with (
            patch("backend.api.main.settings", test_settings),
            patch("backend.api.main.repository", test_repository),
            TestClient(app) as client,
        ):
            accepted = client.post(
                "/api/internal/events",
                headers=headers,
                json={
                    "payload": {
                        "type": "transcript.final",
                        "segment_id": "seg-minute",
                        "speaker": "Chủ trì",
                        "text": "Thống nhất hoàn thành bản demo trước thứ Sáu.",
                    }
                },
            )
            self.assertEqual(accepted.status_code, 200)
            saved = client.put(
                "/api/minutes",
                json={
                    "document": {
                        "meeting": {"title": "Không được tự đổi"},
                        "summary": [
                            {
                                "content": "Hoàn thành bản demo trước thứ Sáu.",
                                "source_segment_ids": ["seg-minute"],
                            },
                            {
                                "content": "Không có nguồn nên phải bỏ.",
                                "source_segment_ids": ["seg-fake"],
                            },
                        ],
                        "topics": [],
                    }
                },
            )
            self.assertEqual(saved.status_code, 200)
            self.assertEqual(saved.json()["version"], 1)
            self.assertEqual(len(saved.json()["document"]["summary"]), 1)
            fetched = client.get("/api/minutes")
            self.assertEqual(fetched.status_code, 200)
            self.assertEqual(fetched.json()["version"], 1)


if __name__ == "__main__":
    unittest.main()
